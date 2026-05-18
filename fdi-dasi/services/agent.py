from typing import Optional
from datetime import datetime
from loguru import logger
from core.config import config
from core.negotiation import (
    detect_close_resource,
    has_offer_signal,
    is_echo,
    mentions_any,
    parse_package,
    valid_package_keys,
    format_history,
    build_greeting_user_prompt,
    build_negotiate_user_prompt,
    build_retry_nudge,
)
from services.memory import Memory, active_sessions
from services.llm_client import LLMClient
from core.prompt import GREETING_PROMPT, NEGOTIATOR_PROMPT, get_tools
from services.butler_service import ButlerService
import asyncio
import traceback

FAREWELL_MARKER = '[[CICLO_CERRADO]]'


class Agent:
    _ERROR_LOG_MAX = 100

    def __init__(self, name: str, butler_service: ButlerService):
        self.name = name
        self._butler = butler_service
        self._llm = LLMClient(name)
        self.memory = Memory()
        self._initiated: set = set()
        self._turns: dict = {}
        self._incompatible_turns: dict = {}
        self._locks: dict = {}
        self._prompt_history: dict = {}
        self._errors: list = []

    @property
    def _initiated_aliases(self):  # required by butler_service
        return self._initiated

    def log_error(self, category: str, message: str, alias: Optional[str] = None,
                  include_traceback: bool = False, **extra):
        entry = {
            "ts": datetime.now().isoformat(timespec='seconds'),
            "category": category,
            "message": str(message),
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
            "content": system_content,  # backward-compat: dashboard expects this field name
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
                self.log_error(
                    "handle_exception",
                    f"{type(e).__name__}: {e}",
                    alias=alias,
                    include_traceback=True,
                )

    async def _handle(self, alias: str, message: Optional[str]):
        if message and FAREWELL_MARKER in message:
            logger.info(f"[{self.name} ← {alias}] Despedida recibida.")
            self._reset(alias)
            return

        if alias not in self._initiated:
            self._initiated.add(alias)
            if message is None:
                logger.info(f"[{self.name}] Primer contacto con '{alias}'.")
                await self._greet(alias)
                return

        if not message:
            return

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
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user",   "content": build_greeting_user_prompt(alias, surplus, missing, ex_s, ex_m, context)},
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
        close_resource = detect_close_resource(incoming, surplus, missing)

        # Detect incompatible offers: other side has an offer verb but doesn't mention our faltante.
        is_incompatible = (
            not close_resource
            and has_offer_signal(incoming)
            and not mentions_any(incoming, missing)
        )
        if is_incompatible:
            count = self._incompatible_turns.get(alias, 0) + 1
            self._incompatible_turns[alias] = count
            logger.warning(f"[{self.name}] Oferta incompatible #{count} de '{alias}' (no menciona {missing}).")
            if count >= 2:
                logger.warning(f"[{self.name}] {count} turnos incompatibles — cerrando con '{alias}'.")
                await self._send_farewell(alias, None)
                return
        else:
            self._incompatible_turns[alias] = 0

        prompt = NEGOTIATOR_PROMPT.format(
            my_name=self.name, alias=alias, remaining=remaining,
            surplus=', '.join(surplus), missing=', '.join(missing),
            ex_surplus=ex_s, ex_missing=ex_m,
        )

        if close_resource:
            # Closing: only send_package available — LLM cannot escape into another proposal.
            tools = [t for t in get_tools(alias, surplus_names=[close_resource], missing_names=missing)
                     if t['function']['name'] == 'send_package']
            context = ""
        else:
            # Proposing: only send_message_to_alias available — LLM cannot close prematurely.
            tools = [t for t in get_tools(alias, surplus_names=surplus, missing_names=missing)
                     if t['function']['name'] == 'send_message_to_alias']
            context = await self._build_context(alias)

        user_prompt = build_negotiate_user_prompt(alias, incoming, close_resource, ex_s, ex_m, context,
                                                   incompatible=is_incompatible)
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

        # Each retry starts from the original messages (not chained) to avoid context bloat.
        for attempt in range(1, 4):
            reason = result.get('reason', 'desconocido')
            nudge = build_retry_nudge(attempt, reason, close_resource, alias, ex_s, ex_m)
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

    async def _handle_tool_call(self, response, alias: str, surplus: list, missing: list,
                                incoming: Optional[str]) -> dict:
        tool_calls = self._tool_calls(response)
        if not tool_calls:
            return {'ok': False, 'reason': 'no_tool_call'}

        # If LLM emits both tools, prefer send_package (close the deal).
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
        for tc in self._tool_calls(response):
            if tc.function.name == 'send_message_to_alias':
                args = dict(tc.function.arguments)
                if (await self._exec_message_text(args.get('mensaje', ''), alias,
                                                   missing=None, incoming=incoming))['ok']:
                    return True
        return False

    async def _exec_package(self, tc, alias: str, surplus: list) -> dict:
        args = dict(tc.function.arguments)
        pkg = args.get('package')
        # Small models sometimes serialize the package as a JSON string instead of an object.
        if isinstance(pkg, str):
            parsed = parse_package(pkg)
            if parsed is not None:
                logger.warning(f"[{self.name} >> {alias}] package era string, parseado a dict: {parsed}")
                pkg = parsed
        if not isinstance(pkg, dict):
            logger.warning(f"[{self.name} >> {alias}] package no es dict: {pkg!r}")
            return {'ok': False, 'reason': 'package_no_dict'}

        keys = valid_package_keys(pkg, surplus)
        if not keys:
            logger.warning(f"[{self.name} >> {alias}] package sin claves válidas: {pkg} (sobrantes={surplus})")
            return {'ok': False, 'reason': 'package_invalido', 'rejected_text': str(pkg)}

        final_pkg = {keys[0]: 1}
        logger.info(f"[{self.name} >> {alias}] LLM ejecuta send_package({final_pkg})")
        await self._butler.send_package(alias, final_pkg)
        self.memory.add_message(alias, "system", f"[Paquete enviado a {alias}: {final_pkg}]")
        await self._send_farewell(alias, final_pkg)
        return {'ok': True, 'reason': 'package'}

    async def _exec_message_text(self, mensaje, alias: str, missing: Optional[list],
                                  incoming: Optional[str]) -> dict:
        if not isinstance(mensaje, str):
            return {'ok': False, 'reason': 'mensaje_no_string'}
        clean = self._butler._sanitize_mensaje(mensaje)
        if not clean:
            logger.warning(f"[{self.name} >> {alias}] mensaje irrecuperable: {mensaje!r}")
            return {'ok': False, 'reason': 'mensaje_irrecuperable', 'rejected_text': mensaje[:200]}
        if is_echo(clean, incoming):
            logger.warning(f"[{self.name} >> {alias}] mensaje es eco del recibido — descartando.")
            return {'ok': False, 'reason': 'echo', 'rejected_text': clean}
        # Negotiation messages must mention a faltante — otherwise the LLM is proposing something we don't need.
        if missing and not mentions_any(clean, missing):
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
        self._incompatible_turns.pop(alias, None)
        self.memory.mark_cycle_boundary(alias)
        active_sessions.pop(alias, None)

    async def _build_context(self, alias: str) -> str:
        history = self.memory.get_history(alias)
        exchanges = [m for m in history if m['role'] in ('user', 'assistant')]
        if not exchanges:
            return ""

        if len(exchanges) <= config.MAX_RESUME_MEMORY:
            return f"Historial de la negociación:\n{format_history(exchanges, self.name, alias)}"

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
            {"role": "user", "content": f"Conversación:\n{format_history(exchanges, self.name, alias)}\n\nResumen:"},
        ]
        response = await self._call_llm(summary_msgs, tools=[])
        if response and getattr(response.message, 'content', None):
            summary = response.message.content.strip()
            logger.info(f"[{self.name}] Contexto resumido para '{alias}': {summary!r}")
            return f"Resumen de lo negociado hasta ahora:\n{summary}"

        logger.warning(f"[{self.name}] Resumen de contexto falló — usando últimos {config.MAX_RESUME_MEMORY} mensajes.")
        return f"Últimos mensajes:\n{format_history(exchanges[-config.MAX_RESUME_MEMORY:], self.name, alias)}"

    def _get_resources(self) -> tuple:
        resources = self._butler.get_actual_resources_and_objectives()
        surplus = [k for k, v in resources['sobrante'].items() if v > 0]
        missing = [k for k, v in resources['faltante'].items() if v > 0]
        return surplus, missing

    async def _call_llm(self, messages: list, tools: list):
        response = await self._llm.call(messages, tools)
        if response is None:
            self.log_error("ollama_call_failed", f"Ollama no respondió. Host: {config.OLLAMA_HOST}")
        return response

    def get_memory(self):
        history = self.memory.get_all_history()
        return {
            alias: {
                "messages": messages,
                "prompts": self._prompt_history.get(alias, []),
            }
            for alias, messages in history.items()
        }
