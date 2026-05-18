from typing import Optional
import asyncio
import random
import re
import traceback
from datetime import datetime

from loguru import logger
from ollama import AsyncClient

from core.config import config
from services.memory import Memory, active_sessions
from core.prompt import GREETING_PROMPT, NEGOTIATOR_PROMPT, get_tools
from services.butler_service import (
    MAX_NEGOTIATION_TURNS, send_message_to_alias, send_package,
    get_actual_resources_and_objectives, _sanitize_mensaje,
)


FAREWELL_MARKER = '[[CICLO_CERRADO]]'
_ollama_client: Optional[AsyncClient] = None

def get_ollama_client() -> AsyncClient:
    """Get or create Ollama AsyncClient. Recreates on connection errors."""
    global _ollama_client
    if _ollama_client is None:
        logger.info(f"Creating new Ollama client for {config.OLLAMA_HOST}")
        _ollama_client = AsyncClient(host=config.OLLAMA_HOST)
    return _ollama_client


def reset_ollama_client():
    """Reset global Ollama client on connection failure. Next call will create fresh."""
    global _ollama_client
    if _ollama_client is not None:
        logger.warning(f"Resetting stale Ollama client (was {config.OLLAMA_HOST})")
        _ollama_client = None


def _normalize(text: str) -> str:
    s = (text or '').lower()
    s = re.sub(r"[^\w\sñáéíóúü]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


class Agent:
    _ERROR_LOG_MAX = 100  # tope del buffer circular

    def __init__(self, name: str):
        self.name = name
        self.memory = Memory()
        self._initiated: set = set()
        self._turns: dict = {}
        self._locks: dict = {}
        self._prompt_history: dict = {}
        self._errors: list = []  # buffer circular de errores para el dashboard

    @property
    def _initiated_aliases(self):  # compat con butler_service
        return self._initiated

    def log_error(self, category: str, message: str, alias: Optional[str] = None,
                  include_traceback: bool = False, **extra):
        """Registra un error visible en el dashboard, sin truncar. Si include_traceback=True
        captura sys.exc_info() en formato texto completo."""
        entry = {
            "ts": datetime.now().isoformat(timespec='seconds'),
            "category": category,
            "message": str(message),  # SIN truncar
            "alias": alias,
        }
        if include_traceback:
            tb = traceback.format_exc()
            if tb and tb.strip() and tb.strip() != "NoneType: None":
                entry["traceback"] = tb
        if extra:
            entry["extra"] = {k: str(v) for k, v in extra.items()}
        self._errors.append(entry)
        if len(self._errors) > self._ERROR_LOG_MAX:
            self._errors = self._errors[-self._ERROR_LOG_MAX:]
        suffix = f" alias={alias}" if alias else ""
        logger.error(f"[{self.name}] {category}{suffix}: {message}")

    def get_errors(self) -> list:
        return list(self._errors)

    def _record_llm_call(self, alias: str, purpose: str, messages_in: list,
                         response, validation: Optional[str] = None,
                         executed: bool = False):
        """Guarda un trace completo de una llamada al LLM en el historial de prompts.

        - messages_in: lista completa de mensajes enviados al modelo (system+history+user).
        - response: objeto de respuesta de Ollama (puede ser None si falló).
        - validation: motivo del rechazo si la salida fue inválida (echo, no_target_resource, etc).
        - executed: True si efectivamente ejecutamos la tool-call.
        """
        if response is not None:
            try:
                out_content = getattr(response.message, "content", "") or ""
                out_tools = [
                    {"name": tc.function.name, "arguments": dict(tc.function.arguments)}
                    for tc in (response.message.tool_calls or [])
                ]
            except (AttributeError, TypeError):
                out_content = repr(response)
                out_tools = []
        else:
            out_content = ""
            out_tools = []

        system_content = next(
            (m["content"] for m in messages_in if m.get("role") == "system"), ""
        )
        trace = {
            "type": purpose,
            "ts": datetime.now().isoformat(timespec='seconds'),
            # backward-compat con el dashboard antiguo: campo content = system prompt
            "content": system_content,
            # nuevos campos para inspección completa
            "input_messages": [
                {"role": m.get("role", ""), "content": m.get("content", "")}
                for m in messages_in
            ],
            "output_content": out_content,
            "output_tool_calls": out_tools,
            "validation": validation,
            "executed": executed,
        }
        self._prompt_history.setdefault(alias, []).append(trace)


    async def response(self, alias: str, message: Optional[str] = None):
        lock = self._locks.setdefault(alias, asyncio.Lock())
        async with lock:
            try:
                await self._handle(alias, message)
            except Exception as e:
                # Capturamos el traceback completo sin filtrar
                self.log_error(
                    "handle_exception",
                    f"{type(e).__name__}: {e}",
                    alias=alias,
                    include_traceback=True,
                )

    async def _handle(self, alias: str, message: Optional[str]):
        # 1. Despedida >> cerrar ciclo
        if message and FAREWELL_MARKER in message:
            logger.info(f"[{self.name} ← {alias}] Despedida recibida.")
            self._reset(alias)
            return

        # 2. Primer contacto >> saludo
        if alias not in self._initiated:
            self._initiated.add(alias)
            if message is None:
                logger.info(f"[{self.name}] Primer contacto con '{alias}'.")
                await self._greet(alias)
                return

        if not message:
            return

        # 3. Registrar + contar turno
        self.memory.add_message(alias, "user", message)
        self._turns[alias] = self._turns.get(alias, 0) + 1
        turns = self._turns[alias]

        if turns > MAX_NEGOTIATION_TURNS:
            logger.warning(f"[{self.name}] Limite de turnos con '{alias}'.")
            await self._send_farewell(alias, None)
            return
        logger.info(f"MEMORY DE {alias} en {self.name}: {self.memory.get_history(alias)}")
        logger.info(f"[{self.name} ← {alias}] NEGOCIANDO {turns}: {message}")
        await self._negotiate(alias, message, turns)


    async def _greet(self, alias: str):
        surplus, missing = self._get_resources()
        if not surplus:
            logger.warning(f"[{self.name}] Sin sobrantes — no saludo a '{alias}'.")
            return

        ex_s = surplus[0]
        ex_m = missing[0] if missing else 'algo'

        prompt = GREETING_PROMPT.format(
            my_name=self.name, alias=alias,
            surplus=', '.join(surplus),
            missing=', '.join(missing) or 'ninguno',
            ex_surplus=ex_s, ex_missing=ex_m,
        )
        tools = get_tools(alias, surplus_names=surplus, missing_names=missing, greeting=True)
        user_prompt = (
            f"Negocia con {alias}. "
            f"Tus sobrantes: {', '.join(surplus)}. "
            f"Tus faltantes: {', '.join(missing) or 'ninguno'}. "
            f"Saluda a {alias}, menciona un recurso que tienes de sobra, "
            f"menciona un recurso que necesitas y propone un intercambio 1 por 1. "
            f"Termina con una pregunta. "
            f"Ejemplo: 'Hola {alias}, tengo {ex_s} de sobra y necesito {ex_m}. "
            f"¿Te interesa intercambiar?'"
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_prompt},
        ]

        response = await self._call_llm(messages, tools)
        ok = await self._handle_message_tool(response, alias, incoming=None)
        self._record_llm_call(alias, "greeting", messages, response,
                              validation=None if ok else "invalid_or_missing", executed=ok)
        if not ok:
            self.log_error("greeting_failed", "El LLM no produjo un saludo válido.", alias=alias)


    async def _negotiate(self, alias: str, incoming: str, turns: int):
        surplus, missing = self._get_resources()
        remaining = max(0, MAX_NEGOTIATION_TURNS - turns)

        if not surplus:
            logger.warning(f"[{self.name}] Sin sobrantes — despedida.")
            await self._send_farewell(alias, None)
            return
        if not missing:
            logger.warning(f"[{self.name}] Sin faltantes — despedida.")
            await self._send_farewell(alias, None)
            return

        # Ejemplos del prompt: usamos el primer sobrante/faltante. No es una propuesta forzada,
        # solo un patrón para que el modelo entienda el formato.
        ex_s = surplus[0]
        ex_m = missing[0]

        prompt = NEGOTIATOR_PROMPT.format(
            my_name=self.name, alias=alias, remaining=remaining,
            surplus=', '.join(surplus),
            missing=', '.join(missing),
            ex_surplus=ex_s, ex_missing=ex_m,
        )
        tools = get_tools(alias, surplus_names=surplus, missing_names=missing)

        # `incoming` ya es el texto plano que el otro agente acaba de enviar
        # (parámetro de _negotiate). Usarlo directamente evita el bug de serializar
        # un dict {role, content} dentro de la f-string.
        user_prompt = (
            f"Último mensaje de {alias}: \"{incoming}\". "
            f"Decide si debes cerrar el trato con send_package "
            f"o responder con send_message_to_alias."
        )
        logger.info(f"[{self.name} >> {alias}] ")
        logger.info(f"[{self.name} >> {alias}] User prompt: {user_prompt}")
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_prompt},
        ]


        response = await self._call_llm(messages, tools)
        result = await self._handle_tool_call(response, alias, surplus, missing, incoming)
        self._record_llm_call(alias, "negotiation", messages, response,
                              validation=result.get('reason'), executed=result['ok'])
        if result['ok']:
            return

        # Reintento con nudge específico al motivo del fallo.
        rejected = result.get('rejected_text', '')
        reason = result.get('reason', 'desconocido')

        if reason == 'echo' and rejected:
            nudge = (
                f"RECHAZO: copiaste textualmente a {alias}. "
                f"PROHIBIDO repetir esta frase: \"{rejected}\". "
                f"Genera una contraoferta DISTINTA: cambia el sobrante que ofreces o el faltante que pides. "
                f"Recuerda: ofreces algo de {surplus}, pides algo de {missing}."
            )
        elif reason == 'no_target_resource':
            nudge = (
                f"RECHAZO: tu mensaje no pedía un faltante. Solo puedes pedir recursos de {missing}. "
                f"Llama send_message_to_alias con un mensaje donde \"de tus X\" tenga X ∈ {missing}."
            )
        elif reason == 'package_invalido':
            nudge = (
                f"RECHAZO: el package no era válido. La clave DEBE ser uno de {surplus}. "
                f"Llama de nuevo send_package(alias=\"{alias}\", package={{\"<sobrante>\": 1}}) "
                f"donde <sobrante> ∈ {surplus}."
            )
        elif reason == 'no_tool_call':
            nudge = (
                f"RECHAZO: no llamaste ninguna herramienta. DEBES llamar una: "
                f"send_package o send_message_to_alias. No escribas texto suelto."
            )
        else:
            nudge = (
                f"RECHAZO: respuesta inválida ({reason}). "
                f"Elige una herramienta (send_package o send_message_to_alias) y llámala respetando las reglas."
            )
        logger.warning(f"[{self.name} >> {alias}] Reintento (motivo={reason}).")
        retry_messages = messages + [{"role": "user", "content": nudge}]
        response = await self._call_llm(retry_messages, tools)
        result2 = await self._handle_tool_call(response, alias, surplus, missing, incoming)
        self._record_llm_call(alias, "negotiation_retry", retry_messages, response,
                              validation=result2.get('reason'), executed=result2['ok'])
        if not result2['ok']:
            self.log_error(
                "turn_lost",
                f"LLM falló tras reintento. Motivo: {result2.get('reason')}. "
                f"Primer intento: {reason}. Rechazado: {rejected!r}",
                alias=alias,
            )

    async def _handle_tool_call(self, response, alias: str, surplus: list, missing: list, incoming: Optional[str]) -> dict:
        """Ejecuta el primer tool-call válido. Devuelve {ok, reason, rejected_text?}."""
        tool_calls = self._tool_calls(response)
        if not tool_calls:
            return {'ok': False, 'reason': 'no_tool_call'}

        # Si el LLM emite ambas herramientas, preferimos send_package (cierre).
        names = [tc.function.name for tc in tool_calls]
        if 'send_package' in names:
            tc = next(t for t in tool_calls if t.function.name == 'send_package')
            return await self._exec_package(tc, alias, surplus)
        if 'send_message_to_alias' in names:
            tc = next(t for t in tool_calls if t.function.name == 'send_message_to_alias')
            args = dict(tc.function.arguments)
            return await self._exec_message_text(args.get('mensaje', ''), alias, missing, incoming)

        logger.warning(f"[{self.name}] Tool inesperado: {names}")
        return {'ok': False, 'reason': 'tool_inesperado'}

    async def _handle_message_tool(self, response, alias: str, incoming: Optional[str]) -> bool:
        """Solo acepta send_message_to_alias (usado en saludo). Sin validar faltante."""
        for tc in self._tool_calls(response):
            if tc.function.name == 'send_message_to_alias':
                args = dict(tc.function.arguments)
                if (await self._exec_message_text(args.get('mensaje', ''), alias, missing=None, incoming=incoming))['ok']:
                    return True
        return False

    async def _exec_package(self, tc, alias: str, surplus: list) -> dict:
        args = dict(tc.function.arguments)
        pkg = args.get('package')
        if not isinstance(pkg, dict):
            logger.warning(f"[{self.name} >> {alias}] package no es dict: {pkg!r}")
            return {'ok': False, 'reason': 'package_no_dict'}

        valid_keys = [k for k, v in pkg.items() if k in surplus and isinstance(v, int) and v >= 1]
        if not valid_keys:
            logger.warning(f"[{self.name} >> {alias}] package sin claves válidas: {pkg} (sobrantes={surplus})")
            return {'ok': False, 'reason': 'package_invalido', 'rejected_text': str(pkg)}

        give = valid_keys[0]
        final_pkg = {give: 1}
        logger.info(f"[{self.name} >> {alias}] LLM ejecuta send_package({final_pkg})")
        await send_package(alias, final_pkg)
        # Log con el formato que el dashboard reconoce como evento de paquete.
        self.memory.add_message(alias, "system", f"[Paquete enviado a {alias}: {final_pkg}]")
        await self._send_farewell(alias, final_pkg)
        return {'ok': True, 'reason': 'package'}

    async def _exec_message_text(self, mensaje, alias: str, missing: Optional[list], incoming: Optional[str]) -> dict:
        if not isinstance(mensaje, str):
            return {'ok': False, 'reason': 'mensaje_no_string'}
        clean = _sanitize_mensaje(mensaje)
        if not clean:
            logger.warning(f"[{self.name} >> {alias}] mensaje irrecuperable: {mensaje!r}")
            return {'ok': False, 'reason': 'mensaje_irrecuperable', 'rejected_text': mensaje[:200]}
        if incoming and _normalize(clean) == _normalize(incoming):
            logger.warning(f"[{self.name} >> {alias}] mensaje es eco del recibido — descartando.")
            return {'ok': False, 'reason': 'echo', 'rejected_text': clean}
        # En negociación, el mensaje DEBE mencionar al menos un faltante (lo que necesito).
        # Sin eso, el LLM está pidiendo basura (p.ej. un sobrante que ya tengo).
        if missing:
            clean_low = _normalize(clean)
            mentions_target = any(_normalize(m) in clean_low for m in missing)
            if not mentions_target:
                logger.warning(f"[{self.name} >> {alias}] mensaje no pide ningún faltante {missing}: {clean!r}")
                return {'ok': False, 'reason': 'no_target_resource', 'rejected_text': clean}
        logger.info(f"[{self.name} >> {alias}] LLM ejecuta send_message: {clean!r}")
        await send_message_to_alias(alias, clean)
        self.memory.add_message(alias, "assistant", clean)
        return {'ok': True, 'reason': 'message'}

    @staticmethod
    def _tool_calls(response) -> list:
        if response is None:
            return []
        try:
            return list(response.message.tool_calls or [])
        except AttributeError:
            return []


    async def _send_farewell(self, alias: str, package_sent):
        if package_sent and isinstance(package_sent, dict) and package_sent:
            res = next(iter(package_sent.keys()))
            text = f"Trato cerrado, te envie 1 {res}. Fue un placer!"
        else:
            text = "No logramos cerrar trato esta vez. Nos vemos en la proxima!"
        try:
            await send_message_to_alias(alias, f"{text} {FAREWELL_MARKER}")
        except Exception as e:
            self.log_error("farewell_failed", f"{type(e).__name__}: {e}", alias=alias)
        self.memory.add_message(alias, "assistant", text)
        self._reset(alias)
        logger.info(f"[{self.name}] Ciclo con '{alias}' cerrado.")

    def _reset(self, alias: str):
        self._initiated.discard(alias)
        self._turns.pop(alias, None)
        self.memory.mark_cycle_boundary(alias)
        active_sessions.pop(alias, None)

    def _get_resources(self) -> tuple:
        resources = get_actual_resources_and_objectives()
        surplus = [k for k, v in resources['sobrante'].items() if v > 0]
        missing = [k for k, v in resources['faltante'].items() if v > 0]
        return surplus, missing

    async def _call_llm(self, messages: list, tools: list, max_retries: int = 3):
        kwargs = {"model": config.LLM_MODEL, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                # Get fresh client in case previous one was stale
                client = get_ollama_client()
                response = await asyncio.wait_for(client.chat(**kwargs), timeout=120.0)
                return response
            except asyncio.TimeoutError:
                last_err = "timeout (>120s)"
                # Reset client on timeout in case connection is stuck
                reset_ollama_client()
                delay = 2 ** attempt + random.uniform(0, 2)
                logger.warning(f"[{self.name}] Ollama timeout {attempt}/{max_retries}; reintento en {delay:.1f}s")
            except (ConnectionError, ConnectionRefusedError, ConnectionResetError) as e:
                last_err = f"{type(e).__name__}: {e}"
                reset_ollama_client()
                delay = 2 ** attempt + random.uniform(0, 2)
                logger.warning(f"[{self.name}] Ollama connection error {attempt}/{max_retries}: {last_err}; reintento en {delay:.1f}s")
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                # On protocol errors (RemoteProtocolError), reset client
                if "RemoteProtocol" in type(e).__name__ or "disconnected" in str(e).lower():
                    reset_ollama_client()
                delay = 2 ** attempt + random.uniform(0, 2)
                logger.warning(f"[{self.name}] Ollama error {attempt}/{max_retries}: {last_err}; reintento en {delay:.1f}s")
            if attempt < max_retries:
                await asyncio.sleep(delay)
        # Todos los reintentos fallaron >> registrar en buffer
        self.log_error(
            "ollama_call_failed",
            f"Ollama no respondió tras {max_retries} intentos. Último error: {last_err}. Host: {config.OLLAMA_HOST}",
        )
        return None

    def get_memory(self):
        history = self.memory.get_all_history()
        return {
            alias: {
                "messages": messages,
                "prompts": self._prompt_history.get(alias, []),
            }
            for alias, messages in history.items()
        }
