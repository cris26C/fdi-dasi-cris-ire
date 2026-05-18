from typing import Optional
from datetime import datetime
from loguru import logger
from ollama import AsyncClient
from core.config import config
from services.memory import Memory, active_sessions
from core.prompt import GREETING_PROMPT, NEGOTIATOR_PROMPT, get_tools
from services.butler_service import ButlerService
import asyncio
import random
import re
import traceback

FAREWELL_MARKER = '[[CICLO_CERRADO]]'

def _normalize(text: str) -> str:
    s = (text or '').lower()
    s = re.sub(r"[^\w\sñáéíóúü]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


class Agent:
    _ERROR_LOG_MAX = 100  # tope del buffer circular

    def __init__(self, name: str, butler_service: ButlerService):
        self.name = name
        self._butler = butler_service
        self._ollama_client: Optional[AsyncClient] = None
        self.memory = Memory()
        self._initiated: set = set()
        self._turns: dict = {}
        self._locks: dict = {}
        self._prompt_history: dict = {}
        self._errors: list = []  # buffer circular de errores para el dashboard

    def _get_ollama_client(self) -> AsyncClient:
        if self._ollama_client is None:
            logger.info(f"Creating new Ollama client for {config.OLLAMA_HOST}")
            self._ollama_client = AsyncClient(host=config.OLLAMA_HOST)
        return self._ollama_client

    def _reset_ollama_client(self):
        if self._ollama_client is not None:
            logger.warning(f"Resetting stale Ollama client (was {config.OLLAMA_HOST})")
            self._ollama_client = None

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

        if turns >= config.MAX_NEGOTIATION_TURNS:
            logger.warning(f"[{self.name}] Limite de turnos con '{alias}'.")
            await self._send_farewell(alias, None)
            return
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
        context = await self._build_context(alias)
        context_block = f"{context}\n\n" if context else ""
        user_prompt = (
            f"{context_block}"
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
        remaining = max(0, config.MAX_NEGOTIATION_TURNS - turns)

        if not surplus:
            logger.warning(f"[{self.name}] Sin sobrantes — despedida.")
            await self._send_farewell(alias, None)
            return
        if not missing:
            logger.warning(f"[{self.name}] Sin faltantes — despedida.")
            await self._send_farewell(alias, None)
            return

        ex_s, ex_m = surplus[0], missing[0]
        incoming_low = _normalize(incoming)

        # Detect closeable offer: parse "te doy X por Y" directionally.
        # Y (after "por") = requested FROM us → check against our surplus.
        # X (after "te doy") = offered TO us → check against our faltante.
        _offer_signals = ['te doy', 'acepto', 'trato', 'de acuerdo', 'ofrezco', 'cambio']
        has_offer = any(s in incoming_low for s in _offer_signals)
        _rm = re.search(r'\bpor\s+(?:\d+\s+)?(?:de\s+\w+\s+)?(\w+)', incoming, re.IGNORECASE)
        _om = re.search(r'(?:te\s+doy|ofrezco)\s+(?:\d+\s+)?(?:de\s+\w+\s+)?(\w+)', incoming, re.IGNORECASE)
        requested_kw = _normalize(_rm.group(1)) if _rm else ''
        offered_kw   = _normalize(_om.group(1)) if _om else ''

        close_resource = None
        if has_offer:
            if requested_kw and any(_normalize(s) == requested_kw for s in surplus):
                close_resource = requested_kw          # they ask for our surplus → give it
            elif offered_kw and any(_normalize(m) == offered_kw for m in missing):
                close_resource = ex_s                  # they offer our faltante → give first surplus

        prompt = NEGOTIATOR_PROMPT.format(
            my_name=self.name, alias=alias, remaining=remaining,
            surplus=', '.join(surplus), missing=', '.join(missing),
            ex_surplus=ex_s, ex_missing=ex_m,
        )

        if close_resource:
            # Only send_package available — LLM cannot escape into another proposal.
            tools = [t for t in get_tools(alias, surplus_names=[close_resource], missing_names=missing)
                     if t['function']['name'] == 'send_package']
            user_prompt = (
                f"Mensaje de {alias}: \"{incoming}\"\n"
                f"CIERRA EL TRATO. Llama:\n"
                f"send_package(alias=\"{alias}\", package={{\"{close_resource}\": 1}})"
            )
        else:
            tools = get_tools(alias, surplus_names=surplus, missing_names=missing)
            context = await self._build_context(alias)
            context_block = f"{context}\n\n" if context else ""
            user_prompt = (
                f"{context_block}"
                f"Mensaje de {alias}: \"{incoming}\"\n"
                f"Propón: \"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\""
            )

        logger.info(f"[{self.name} >> {alias}] close_resource={close_resource!r} user_prompt={user_prompt!r}")
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user",   "content": user_prompt},
        ]

        response = await self._call_llm(messages, tools)
        result = await self._handle_tool_call(response, alias, surplus, missing, incoming)
        self._record_llm_call(alias, "negotiation", messages, response,
                              validation=result.get('reason'), executed=result['ok'])
        if result['ok']:
            return

        # Progressive retries: each attempt gets a more explicit nudge.
        # Each retry starts from the original messages (not chained) to avoid context bloat.
        for attempt in range(1, 4):
            reason = result.get('reason', 'desconocido')
            if close_resource:
                nudge = [
                    f"Llama send_package(alias=\"{alias}\", package={{\"{close_resource}\": 1}}). Solo eso.",
                    f"OBLIGATORIO send_package: alias=\"{alias}\", package={{\"{close_resource}\": 1}}. Sin texto.",
                    f"Tool call AHORA — send_package, alias={alias!r}, package={{\"{close_resource}\": 1}}.",
                ][attempt - 1]
            elif reason in ('echo', 'no_target_resource'):
                nudge = [
                    f"Nueva oferta: \"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\"",
                    f"send_message_to_alias(alias=\"{alias}\", mensaje=\"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\").",
                    f"OBLIGATORIO send_message_to_alias: alias=\"{alias}\", mensaje=\"Te doy 1 {ex_s} por 1 {ex_m}\".",
                ][attempt - 1]
            else:
                nudge = [
                    f"Llama send_message_to_alias: \"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\"",
                    f"send_message_to_alias(alias=\"{alias}\", mensaje=\"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\").",
                    f"OBLIGATORIO send_message_to_alias: alias=\"{alias}\", mensaje=\"Te doy 1 {ex_s} por 1 {ex_m}\".",
                ][attempt - 1]

            logger.warning(f"[{self.name} >> {alias}] Reintento {attempt}/3 (motivo={reason}).")
            retry_messages = messages + [{"role": "user", "content": nudge}]
            response = await self._call_llm(retry_messages, tools)
            result = await self._handle_tool_call(response, alias, surplus, missing, incoming)
            self._record_llm_call(alias, f"negotiation_retry_{attempt}", retry_messages, response,
                                  validation=result.get('reason'), executed=result['ok'])
            if result['ok']:
                return

        self.log_error(
            "turn_lost",
            f"LLM falló tras 3 reintentos. Último motivo: {result.get('reason')} close_resource={close_resource!r}",
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
        # Small models sometimes serialize the package as a JSON string instead of an object.
        if isinstance(pkg, str):
            import json as _json
            try:
                pkg = _json.loads(pkg)
                logger.warning(f"[{self.name} >> {alias}] package era string, parseado a dict: {pkg}")
            except Exception:
                pass
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
        await self._butler.send_package(alias, final_pkg)
        # Log con el formato que el dashboard reconoce como evento de paquete.
        self.memory.add_message(alias, "system", f"[Paquete enviado a {alias}: {final_pkg}]")
        await self._send_farewell(alias, final_pkg)
        return {'ok': True, 'reason': 'package'}

    async def _exec_message_text(self, mensaje, alias: str, missing: Optional[list], incoming: Optional[str]) -> dict:
        if not isinstance(mensaje, str):
            return {'ok': False, 'reason': 'mensaje_no_string'}
        clean = self._butler._sanitize_mensaje(mensaje)
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
        await self._butler.send_message_to_alias(alias, clean)
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
            await self._butler.send_message_to_alias(alias, f"{text} {FAREWELL_MARKER}")
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

    async def _build_context(self, alias: str) -> str:
        """Return conversation history as a compact text block for the prompt.

        Formats the last exchanges directly if there are 5 or fewer.
        Beyond 5, asks the LLM for a 2-3 sentence summary so the small model
        is not overwhelmed and can still focus on closing the trade.
        """
        history = self.memory.get_history(alias)
        exchanges = [m for m in history if m['role'] in ('user', 'assistant')]
        if not exchanges:
            return ""

        def _fmt(msgs: list) -> str:
            lines = []
            for m in msgs:
                speaker = alias if m['role'] == 'user' else self.name
                lines.append(f"{speaker}: {m['content']}")
            return "\n".join(lines)

        if len(exchanges) <= config.MAX_RESUME_MEMORY:
            return f"Historial de la negociación:\n{_fmt(exchanges)}"

        # Summarize with LLM — keep the prompt very short for llama3.2:3b
        transcript = _fmt(exchanges)
        summary_msgs = [
            {
                "role": "system",
                "content": (
                    f"Eres un asistente que resume negociaciones. "
                    f"Resume en máximo 3 frases cortas en español la negociación entre {self.name} y {alias}. "
                    f"Incluye: qué recursos se ofrecieron, si hubo acuerdo parcial, y qué falta para cerrar el trato. "
                    f"Solo el resumen, sin explicaciones ni saludos."
                ),
            },
            {
                "role": "user",
                "content": f"Conversación:\n{transcript}\n\nResumen:",
            },
        ]
        response = await self._call_llm(summary_msgs, tools=[])
        if response and getattr(response.message, 'content', None):
            summary = response.message.content.strip()
            logger.info(f"[{self.name}] Contexto resumido para '{alias}': {summary!r}")
            return f"Resumen de lo negociado hasta ahora:\n{summary}"

        # Fallback: keep the 5 most recent exchanges
        logger.warning(f"[{self.name}] Resumen de contexto falló — usando últimos {config.MAX_RESUME_MEMORY} mensajes.")
        return f"Últimos mensajes:\n{_fmt(exchanges[-config.MAX_RESUME_MEMORY:])}"

    def _get_resources(self) -> tuple:
        resources = self._butler.get_actual_resources_and_objectives()
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
                client = self._get_ollama_client()
                response = await asyncio.wait_for(client.chat(**kwargs), timeout=120.0)
                return response
            except asyncio.TimeoutError:
                last_err = "timeout (>120s)"
                self._reset_ollama_client()
                delay = 2 ** attempt + random.uniform(0, 2)
                logger.warning(f"[{self.name}] Ollama timeout {attempt}/{max_retries}; reintento en {delay:.1f}s")
            except (ConnectionError, ConnectionRefusedError, ConnectionResetError) as e:
                last_err = f"{type(e).__name__}: {e}"
                self._reset_ollama_client()
                delay = 2 ** attempt + random.uniform(0, 2)
                logger.warning(f"[{self.name}] Ollama connection error {attempt}/{max_retries}: {last_err}; reintento en {delay:.1f}s")
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if "RemoteProtocol" in type(e).__name__ or "disconnected" in str(e).lower():
                    self._reset_ollama_client()
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
