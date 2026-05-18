from typing import Optional
from loguru import logger
from services.memory import Memory, active_sessions
from config import config
from services.prompt import (TOOLS, get_tools, INITIAL_GREETING_SYSTEM_PROMPT, AGREEMENT_SYSTEM_PROMPT,
                              MAX_MSGS, SUMMARY_THRESHOLD, NEGOTIATOR_SYSTEM_PROMPT, SUMMARY_PROMPT)
from ollama import AsyncClient
from services.butler_service import (MAX_NEGOTIATION_TURNS, send_message_to_alias, send_package,
                                     get_actual_resources_and_objectives, _sanitize_mensaje)
import asyncio
import random

AVAILABLE_TOOLS = {
    "send_message_to_alias": send_message_to_alias,
    "send_package": send_package
}

_ollama_client = None

def get_ollama_client():
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = AsyncClient(host=config.OLLAMA_HOST)
    return _ollama_client


def has_tool_calls(response) -> bool:
    if response is None:
        return False
    try:
        return bool(response.message.tool_calls)
    except AttributeError:
        return bool(response.get("message", {}).get("tool_calls", []))


class Agent:
    def __init__(self, name: str):
        self.name = name
        self.memory = Memory()
        self._initiated_aliases: set = set()
        self._processing_aliases: set = set()
        self._pending_messages: dict = {}
        self._prompt_history: dict = {}
        self._turn_counter: dict = {}

    def receive_message(self, role: str, content: str):
        self.memory.add_message(self.name, role, content)

    async def response(self, alias: str, message: Optional[str] = None):
        if alias in self._processing_aliases:
            self._pending_messages[alias] = message
            return None
        self._processing_aliases.add(alias)
        try:
            await self._response_inner(alias, message)
            while alias in self._pending_messages:
                pending = self._pending_messages.pop(alias)
                await self._response_inner(alias, pending)
        finally:
            self._processing_aliases.discard(alias)

    async def _response_inner(self, alias: str, message: Optional[str] = None):
        # Check farewell before anything else — even if alias was already reset
        if message and self._is_farewell(message):
            logger.info(f"[{self.name} ← {alias}] Cierre de ciclo detectado — reseteando.")
            self._reset_negotiation(alias)
            return None

        is_first_contact = alias not in self._initiated_aliases
        self._initiated_aliases.add(alias)

        if is_first_contact and message is None:
            # No incoming message — we detected them first, send our greeting
            logger.info(f"[{self.name}] Primer contacto con '{alias}' — iniciando saludo.")
            return await self.send_greeting(alias)

        if not message:
            return None

        # 1. Save incoming message and advance turn counter
        self.memory.add_message(alias, "user", message)
        self._turn_counter.setdefault(alias, 0)
        self._turn_counter[alias] += 1
        turns = self._turn_counter[alias]
        remaining = max(0, MAX_NEGOTIATION_TURNS - turns)

        # Hard stop if past the limit (LLM ignored the ultimatum last turn)
        if turns > MAX_NEGOTIATION_TURNS:
            logger.warning(f"[{self.name}] Límite superado ({turns}/{MAX_NEGOTIATION_TURNS}) con '{alias}'. Cerrando.")
            await self.send_farewell(alias, None)
            return None

        # On the last allowed turn inject an ultimatum so the LLM must decide now
        if turns == MAX_NEGOTIATION_TURNS:
            logger.warning(f"[{self.name}] Último turno con '{alias}'. Inyectando ultimátum.")
            self.memory.add_message(alias, "user",
                "AVISO DEL SISTEMA: ¡ÚLTIMO TURNO! La negociación tomó demasiado tiempo. "
                "O aceptas la última oferta ejecutando la herramienta 'send_package' AHORA MISMO, "
                "o despídete indicando que no hay trato. Ya no puedes hacer contraofertas."
            )

        # 2. Get current resources
        resources = get_actual_resources_and_objectives()
        surplus_names = [k for k, v in resources['sobrante'].items() if v > 0]
        missing_names = [k for k, v in resources['faltante'].items() if v > 0]

        # 3. Build context: include short history for negotiation continuity
        history = await self.get_history_with_summary(alias)

        agent_prompt = NEGOTIATOR_SYSTEM_PROMPT.format(
            agent_alias=alias,
            missing_resources=', '.join(missing_names) or 'ninguno',
            surplus_resources=', '.join(surplus_names) or 'ninguno',
            remaining_turns=remaining,
            example_surplus=surplus_names[0] if surplus_names else 'mi recurso',
            example_missing=missing_names[0] if missing_names else 'tu recurso',
        )

        # Anti-copy guard: quote the last received message and forbid copying it.
        # This is the most reliable signal for a 3B model — it can't reproduce text
        # that the prompt explicitly marks as forbidden.
        last_received = (message or '').strip()
        guard_lines = [
            f"Mis SOBRANTES son {', '.join(surplus_names) or 'ninguno'}.",
            f"Mis FALTANTES son {', '.join(missing_names) or 'ninguno'}.",
            "Solo puedo usar esos recursos.",
        ]
        if last_received:
            guard_lines.append(
                f"PROHIBIDO copiar este mensaje recibido: \"{last_received}\". "
                f"Mi respuesta debe usar palabras y estructura distintas."
            )
        guard_lines.append("Responde con send_package o send_message_to_alias.")
        anti_copy_guard = " ".join(guard_lines)

        messages = [{"role": "system", "content": agent_prompt}] + history + [
            {"role": "system", "content": anti_copy_guard}
        ]
        tools = get_tools(alias, surplus_names=surplus_names, missing_names=missing_names)

        # 4. Call LLM
        response = await self.make_response(messages, tools)
        response = await self.ensure_tool_response(messages, response, alias, tools=tools)

        if response is None:
            return None

        # 4b. Echo guard: if the LLM generated the same message as last time, force a different one
        response = await self._break_echo_if_needed(response, messages, alias, tools, surplus_names, missing_names)
        if response is None:
            return None

        # 5. Execute tools
        tools_executed = await self.get_and_execute_tools(response, alias, surplus_names=surplus_names)
        content = getattr(response.message, 'content', None) or ''
        self.sync_memory(alias, tools_executed, content=content)

        # 6. If deal closed, send farewell
        package_tool = next((t for t in tools_executed if t['name'] == 'send_package'), None)
        if package_tool:
            await self.send_farewell(alias, package_tool['arguments'].get('package', {}))

        return response, tools_executed

    def _extract_mensaje_from_response(self, response) -> str:
        """Extract the mensaje text from a send_message_to_alias tool call, if present."""
        try:
            for tc in (response.message.tool_calls or []):
                if tc.function.name == 'send_message_to_alias':
                    msg = dict(tc.function.arguments).get('mensaje', '')
                    if isinstance(msg, str):
                        return msg.strip()
        except AttributeError:
            pass
        return ''

    async def _break_echo_if_needed(self, response, messages, alias, tools, surplus_names, missing_names):
        """If the LLM generated the exact same message as the last assistant turn, retry with a nudge."""
        new_msg = self._extract_mensaje_from_response(response)
        if not new_msg:
            return response  # send_package or no tool — not an echo

        history = self.memory.get_history(alias)
        last_sent = ''
        for entry in reversed(history):
            if entry.get('role') == 'assistant':
                last_sent = entry.get('content', '').strip()
                break

        if new_msg.lower() != last_sent.lower():
            return response  # different message — fine

        # Echo detected — nudge with concrete alternative
        ex_s = surplus_names[0] if surplus_names else 'mi recurso'
        ex_m = missing_names[0] if missing_names else 'tu recurso'
        logger.warning(f"[{self.name}→{alias}] Echo detectado: '{new_msg[:60]}' — reintentando con variacion.")
        nudge_messages = list(messages) + [
            {"role": "assistant", "content": new_msg},
            {"role": "user", "content": (
                f"Ese mensaje ya lo enviaste antes. Propón algo diferente: "
                f"si no tienes lo que te piden, di exactamente "
                f"'No tengo ese recurso, pero tengo {ex_s} de sobra. "
                f"Te doy 1 {ex_s} por 1 de tus {ex_m}.' "
                f"Usa send_message_to_alias con esa frase."
            )},
        ]
        retry = await self.make_response(nudge_messages, tools)
        return await self.ensure_tool_response(nudge_messages, retry, alias, tools=tools)

    async def get_history_with_summary(self, alias: str) -> list:
        """Return recent history. When history grows, replace older messages with an LLM summary."""
        history = self.memory.get_history(alias)
        if len(history) <= SUMMARY_THRESHOLD:
            return list(history[-MAX_MSGS:])

        old_msgs = history[:-MAX_MSGS]
        recent_msgs = history[-MAX_MSGS:]

        summary_messages = [{"role": "system", "content": SUMMARY_PROMPT}] + list(old_msgs)
        resp = await self.make_response(summary_messages, tools=[])
        summary = (getattr(resp.message, 'content', '') or '').strip() if resp else ''

        if summary:
            return [{"role": "user", "content": f"[Contexto anterior: {summary}]"}] + list(recent_msgs)
        return list(recent_msgs)

    async def send_greeting(self, alias: str):
        self._initiated_aliases.add(alias)

        resources = get_actual_resources_and_objectives()
        surplus_resources = resources['sobrante']
        missing_resources = resources['faltante']

        surplus_keys_sorted = [k for k, _ in sorted(surplus_resources.items(), key=lambda x: x[1], reverse=True)]
        missing_keys = list(missing_resources.keys())
        alias_prompt = INITIAL_GREETING_SYSTEM_PROMPT.format(
            agent_alias=alias,
            missing_resources=', '.join(missing_keys) or 'ninguno',
            surplus_resources=', '.join(surplus_keys_sorted) or 'ninguno',
            example_surplus=surplus_keys_sorted[0] if surplus_keys_sorted else 'recursos',
            example_missing=missing_keys[0] if missing_keys else 'tus recursos',
        )
        self._prompt_history.setdefault(alias, []).append({"type": "greeting", "content": alias_prompt})
        surplus_keys = surplus_keys_sorted
        ex_s = surplus_keys[0] if surplus_keys else 'recursos'
        ex_m = missing_keys[0] if missing_keys else 'lo que necesitas'
        greeting_hint = (
            f"Envía tu mensaje de apertura. Solo texto en español, nada de JSON.\n"
            f"Ejemplo: \"Hola, tengo {ex_s} de sobra y es de primera calidad. "
            f"Te doy 1 {ex_s} por 1 de tus {ex_m}, cerramos?\""
        )
        messages = [
            {"role": "system", "content": alias_prompt},
            {"role": "user", "content": greeting_hint},
        ]
        greeting_tools = get_tools(alias, surplus_names=surplus_keys, missing_names=missing_keys, greeting=True)

        for attempt in range(3):
            response = await self.make_response(messages, greeting_tools)
            response = await self.ensure_tool_response(messages, response, alias, tools=greeting_tools)
            if response is None:
                logger.error(f"make_response devolvió None en send_greeting para '{alias}' (intento {attempt + 1}).")
                continue

            tool_executed = await self.get_and_execute_tools(response, alias, surplus_names=surplus_keys)

            msg_tool = next((t for t in tool_executed if t['name'] == 'send_message_to_alias'), None)
            if msg_tool:
                tool_response = msg_tool.get('response', '')
                if isinstance(tool_response, str) and tool_response.startswith('ERROR'):
                    bad_msg = msg_tool['arguments'].get('mensaje', '')
                    logger.warning(f"Saludo inválido (intento {attempt + 1}): {bad_msg!r} — reintentando.")
                    messages.append({"role": "assistant", "content": bad_msg})
                    messages.append({"role": "user", "content": (
                        "El mensaje anterior era inválido. Escribe SOLO texto en español, sin llaves ni JSON. "
                        "Saluda y propón intercambiar uno de tus sobrantes por uno de tus faltantes."
                    )})
                    continue

            content = getattr(response.message, 'content', None) or ''
            self.sync_memory(alias, tool_executed, content=content)
            return response, tool_executed

        logger.error(f"[{self.name}] No se pudo generar un saludo válido para '{alias}' tras 3 intentos.")
        return None

    async def send_farewell(self, alias: str, package_sent):
        """Send a deterministic farewell — no LLM call needed for a simple closing message.
        Avoids contamination from generic tool examples leaking into the message."""
        logger.info(f"Generando despedida para '{alias}'.")
        if package_sent and isinstance(package_sent, dict) and package_sent:
            resource_given = next(iter(package_sent.keys()))
            farewell_text = f"Trato cerrado, te envie 1 {resource_given}. Fue un placer negociar contigo!"
        else:
            farewell_text = "No logramos cerrar trato esta vez. Nos vemos en la proxima!"

        await send_message_to_alias(alias, f"{farewell_text} {self._FAREWELL_MARKER}")
        self.memory.add_message(alias, "assistant", f"Despedida enviada a {alias}: '{farewell_text}'")
        self._reset_negotiation(alias)
        logger.info(f"Ciclo de negociacion con '{alias}' completado. Reiniciando.")

    _FAREWELL_MARKER = '[[CICLO_CERRADO]]'

    def _is_farewell(self, message: str) -> bool:
        return bool(message) and self._FAREWELL_MARKER in message

    def _reset_negotiation(self, alias: str):
        logger.info(f"Reiniciando ciclo de negociación con '{alias}'.")
        self._initiated_aliases.discard(alias)
        self._pending_messages.pop(alias, None)
        self._turn_counter.pop(alias, None)
        self.memory.mark_cycle_boundary(alias)
        active_sessions.pop(alias, None)

    async def ensure_tool_response(self, messages: list, response, alias: str, tools: list = None):
        if response is None or has_tool_calls(response):
            return response

        content = getattr(response.message, 'content', None) or ''
        logger.warning(f"El modelo respondió sin tool-call para '{alias}'. Reintentando. Content: {content!r}")

        repair_messages = list(messages)
        if content:
            repair_messages.append({"role": "assistant", "content": content})
        repair_messages.append({
            "role": "user",
            "content": (
                "Tu respuesta anterior no usó ninguna herramienta. "
                "Debes responder AHORA con una tool-call real: "
                "send_message_to_alias para enviar un mensaje, o send_package para cerrar el trato. "
                "No escribas texto suelto."
            ),
        })
        return await self.make_response(repair_messages, tools or get_tools(alias))

    def sync_memory(self, alias_name: str, tool_executed: list, content: Optional[str] = None):
        description = '[Ninguna acción ejecutada]'

        if not tool_executed and content:
            description = content
        elif not tool_executed and not content:
            description = '[Ninguna acción ejecutada y sin contenido]'

        if tool_executed:
            for tool in tool_executed:
                nombre = tool['name']
                args = tool['arguments']
                tool_response = tool.get('response', '')
                send_failed = isinstance(tool_response, str) and tool_response.startswith('ERROR')
                if nombre == "send_message_to_alias":
                    description = f"[Mensaje rechazado: {tool_response[:80]}]" if send_failed else args.get('mensaje', '[mensaje vacío]')
                elif nombre == "send_package":
                    description = f"[Paquete rechazado: {tool_response[:80]}]" if send_failed else f"[Paquete enviado a {args.get('alias')}: {args.get('package')}]"

        existing = self.memory.get_history(alias_name)
        if existing and existing[-1].get('role') == 'assistant' and existing[-1].get('content') == description:
            logger.warning(f"Omitiendo mensaje de asistente duplicado en memoria para '{alias_name}'.")
            return

        self.memory.add_message(alias_name, "assistant", description)

    async def get_and_execute_tools(self, response, alias: str = None, surplus_names: list = None):
        tools_executed = []

        if response is None:
            return tools_executed

        tool_calls = []
        try:
            tool_calls = response.message.tool_calls or []
        except AttributeError:
            tool_calls = response.get("message", {}).get("tool_calls", []) or []

        logger.debug(f"[{alias}] LLM devolvió {len(tool_calls)} tool-call(s).")

        names = [tc.function.name for tc in tool_calls]
        if 'send_package' in names and 'send_message_to_alias' in names:
            logger.warning(f"[{alias}] LLM emitió send_message + send_package — descartando send_message.")
            tool_calls = [tc for tc in tool_calls if tc.function.name == 'send_package']

        for tool_call in tool_calls:
            name = tool_call.function.name
            arguments = dict(tool_call.function.arguments)
            # Always force alias to the known string — LLM sometimes passes schema objects
            if alias:
                if arguments.get('alias') != alias:
                    logger.warning(f"[{alias}] Herramienta '{name}': alias incorrecto {arguments.get('alias')!r} — corrigiendo.")
                arguments['alias'] = alias
            # Normalize the mensaje arg BEFORE calling the tool, so both the actual send
            # and the memory record store clean text — not raw schema dumps.
            if name == 'send_message_to_alias' and 'mensaje' in arguments:
                msg_val = arguments['mensaje']
                if isinstance(msg_val, dict):
                    # LLM emitted a dict instead of a string — pick the real text field.
                    extracted = None
                    for key in ('value', 'mensaje', 'message', 'content', 'texto', 'oferta'):
                        v = msg_val.get(key)
                        if isinstance(v, str) and v.strip():
                            extracted = v.strip()
                            break
                    if extracted:
                        logger.warning(f"[{alias}] mensaje era dict — extraído: {extracted!r}")
                        arguments['mensaje'] = extracted
                    else:
                        logger.error(f"[{alias}] mensaje no es string ni dict recuperable: {msg_val!r} — saltando tool call.")
                        tools_executed.append({"name": name, "arguments": arguments, "response": "ERROR: mensaje inválido"})
                        continue
                elif isinstance(msg_val, str):
                    # Run the same sanitizer butler_service uses — strips schema dumps,
                    # extracts the real text from "value"/"mensaje"/etc. fields.
                    cleaned = _sanitize_mensaje(msg_val)
                    if cleaned is None:
                        logger.error(f"[{alias}] mensaje string irrecuperable: {msg_val[:120]!r} — saltando tool call.")
                        tools_executed.append({"name": name, "arguments": arguments, "response": "ERROR: mensaje inválido"})
                        continue
                    if cleaned != msg_val:
                        logger.warning(f"[{alias}] mensaje saneado: {msg_val[:80]!r} → {cleaned!r}")
                    arguments['mensaje'] = cleaned

            if name == 'send_package' and surplus_names is not None:
                import json as _json
                pkg = arguments.get('package', {})
                if isinstance(pkg, str):
                    try:
                        pkg = _json.loads(pkg)
                    except Exception:
                        pkg = {}
                if not isinstance(pkg, dict):
                    pkg = {}
                arguments['package'] = pkg  # always store the cleaned dict back
                invalid_keys = [k for k in pkg if k not in surplus_names]
                if invalid_keys or not pkg:
                    logger.error(f"[{self.name} >> {alias}] send_package con recursos NO sobrantes: {invalid_keys}.")
                    tools_executed.append({
                        "name": name,
                        "arguments": arguments,
                        "response": f"ERROR: No puedes enviar {invalid_keys} — no son tus sobrantes. Solo puedes enviar: {surplus_names}"
                    })
                    continue

            logger.debug(f"[{alias}] Ejecutando '{name}' args={arguments}")
            if name in AVAILABLE_TOOLS:
                response_tool = await AVAILABLE_TOOLS[name](**arguments)
                tools_executed.append({"name": name, "arguments": arguments, "response": response_tool})
        return tools_executed

    async def make_response(self, messages: list, tools: list = None):
        client = get_ollama_client()
        # tools=None → use default TOOLS; tools=[] → text-only call (no tools)
        effective_tools = TOOLS if tools is None else tools
        max_retries = 3
        retry_count = 0

        while retry_count < max_retries:
            try:
                logger.debug(f"Ollama API call (intento {retry_count + 1}/{max_retries}), mensajes={len(messages)}")
                kwargs = {"model": config.LLM_MODEL, "messages": messages}
                if effective_tools:
                    kwargs["tools"] = effective_tools
                response = await asyncio.wait_for(client.chat(**kwargs), timeout=400.0)
                return response
            except asyncio.TimeoutError:
                retry_count += 1
                delay = 2 ** retry_count + random.uniform(0, 3)
                logger.warning(f"Ollama timeout (intento {retry_count}/{max_retries}) — reintentando en {delay:.1f}s")
                if retry_count < max_retries:
                    await asyncio.sleep(delay)
                else:
                    raise
            except Exception as e:
                retry_count += 1
                delay = 2 ** retry_count + random.uniform(0, 3)
                logger.error(f"Ollama error (intento {retry_count}/{max_retries}): {e} — reintentando en {delay:.1f}s")
                if retry_count < max_retries:
                    await asyncio.sleep(delay)
                else:
                    raise

    def get_memory(self):
        history = self.memory.get_all_history()
        result = {}
        for alias, messages in history.items():
            result[alias] = {
                "messages": messages,
                "prompts": self._prompt_history.get(alias, [])
            }
        return result
