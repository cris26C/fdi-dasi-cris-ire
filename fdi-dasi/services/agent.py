# from typing import Optional
# from loguru import logger
# from services.memory import Memory
# from config import config
# from services.prompt import (TOOLS, INITIAL_GREETING_SYSTEM_PROMPT, AGREEMENT_SYSTEM_PROMPT, MAX_MSGS, NEGOTIATOR_SYSTEM_PROMPT)
# from ollama import AsyncClient
# from services.butler_service import (send_message_to_alias, send_package, get_actual_resources_and_objectives)

# AVAILABLE_TOOLS = {
#     "send_message_to_alias": send_message_to_alias,
#     "send_package": send_package
# }

# class Agent:
#     def __init__(self, name: str):
#         self.name = name
#         self.memory = Memory()

#     def receive_message(self, role: str, content: str):
#         self.memory.add_message(self.name, role, content)

#     async def response(self, alias: str, message: Optional[str] = None):
#         # Aquí se implementaría la lógica para generar una respuesta a un mensaje recibido, utilizando la memoria del agente y posiblemente otras herramientas o funciones
#         logger.debug(f"Tamaño de la memoria para el agente {self.name}: {len(self.memory.get_history(alias))} mensajes.")
#         if not self.memory.agent_name_in_memory(alias):
#             # Si el agente no tiene memoria previa, se le envía un saludo inicial
#             return await self.send_greeting(alias)
        
#         resources = get_actual_resources_and_objectives()
#         actual_resources = resources['actual']
#         objective = resources['objetivo']

#         agent_prompt = NEGOTIATOR_SYSTEM_PROMPT.format(agent_alias=alias, resources=actual_resources, objective=objective)
#         messages = self.memory.get_history(alias)[-MAX_MSGS:]
#         messages.insert(0, {"role": "system", "content": agent_prompt})
#         messages.append({"role": "user", "content": message})
#         # logger.debug(f"Generando respuesta para el agente {self.name} con el siguiente contexto: {messages}")
#         response = await self.make_response(messages)
#         tools_executed = await self.get_and_execute_tools(response)

#         self.sync_memory(tools_executed, content=response['message']['content'])
#         return response, tools_executed
    
#     async def send_greeting(self, alias: str):
#         resources = get_actual_resources_and_objectives()
#         actual_resources = resources['actual']
#         objective = resources['objetivo']

#         alias_prompt = INITIAL_GREETING_SYSTEM_PROMPT.format(agent_alias=alias, resources=actual_resources, objective=objective)
#         messages = [
#                     {"role": "system", "content": alias_prompt},
#                     {"role": "user", "content": f"Por favor, saluda al agente {alias} utilizando la herramienta correspondiente."}]
#         response = await self.make_response(messages)

        
#         tool_executed = await self.get_and_execute_tools(response)
#         self.sync_memory(tool_executed, content=response['message']['content'])
#         return response, tool_executed

#     def sync_memory(self, tool_executed: list, content: Optional[str] = None):
#         # {"name": name, "arguments": arguments, "response": response}
#         description = '[Ninguna acción ejecutada]'

#         if not tool_executed and content:
#             description = content
#         elif not tool_executed and not content:
#             description = '[Ninguna acción ejecutada y sin contenido para agregar a la memoria]'

#         if tool_executed:
#             for tool in tool_executed:
#                 nombre = tool['name']
#                 args = tool['arguments']
#                 if nombre == "send_message_to_alias":
#                     description = f"Envié un mensaje a {args.get('alias')} con el siguiente mensaje:'{args.get('mensaje')}'"
#                 elif nombre == "send_package":
#                     description = f"Envié un paquete a {args.get('alias')}: {args.get('package')}"


#         self.memory.add_message(self.name, "assistant", description)

#     async def get_and_execute_tools(self, response):
#         tools_calls = []
#         tools_executed = []
#         logger.debug(f'Nro de llamadas a herramientas en la respuesta: {len(response.get("message", {}).get("tool_calls", []))}')
#         if 'message' in response and 'tool_calls' in response['message']:
#             tools_calls = response['message']['tool_calls']

#         for tool_call in tools_calls:
#             name = tool_call.function.name
#             arguments = tool_call.function.arguments
#             logger.info(f"Intentando ejecutar la herramienta '{name}' con los siguientes argumentos: {arguments}")
#             if name in AVAILABLE_TOOLS:
#                 response = await AVAILABLE_TOOLS[name](**arguments)
#                 tools_executed.append({"name": name, "arguments": arguments, "response": response})
#         return tools_executed
        
#     async def make_response(self, messages: list):
#         return await AsyncClient().chat(
#                     model=config.LLM_MODEL,
#                     messages=messages,
#                     tools=TOOLS
#                 )


from typing import Optional
from loguru import logger
from services.memory import Memory, active_sessions
from config import config
from services.prompt import (TOOLS, get_tools, INITIAL_GREETING_SYSTEM_PROMPT, AGREEMENT_SYSTEM_PROMPT, MAX_MSGS, NEGOTIATOR_SYSTEM_PROMPT)
from ollama import AsyncClient
from services.butler_service import (send_message_to_alias, send_package, get_actual_resources_and_objectives)
import asyncio

AVAILABLE_TOOLS = {
    "send_message_to_alias": send_message_to_alias,
    "send_package": send_package
}

# Global singleton AsyncClient
_ollama_client = None

def get_ollama_client():
    global _ollama_client
    if _ollama_client is None:
        _ollama_client = AsyncClient(host=config.OLLAMA_HOST)
    return _ollama_client

class Agent:
    def __init__(self, name: str):
        self.name = name
        self.memory = Memory()
        self._initiated_aliases: set = set()  # Aliases we have greeted or are greeting
        self._processing_aliases: set = set()  # Aliases whose response() is currently running

    def receive_message(self, role: str, content: str):
        self.memory.add_message(self.name, role, content)

    async def response(self, alias: str, message: Optional[str] = None):
        # Drop concurrent calls for the same alias — prevents memory triplication
        # when messages arrive faster than the LLM can respond.
        if alias in self._processing_aliases:
            logger.warning(f"response() para '{alias}' ya en curso, descartando mensaje duplicado.")
            return None
        self._processing_aliases.add(alias)
        try:
            return await self._response_inner(alias, message)
        finally:
            self._processing_aliases.discard(alias)

    async def _response_inner(self, alias: str, message: Optional[str] = None):
        # Check first-contact synchronously BEFORE any await to avoid race conditions
        is_first_contact = alias not in self._initiated_aliases
        self._initiated_aliases.add(alias)  # Mark immediately (no await between check and add)

        logger.debug(f"Tamaño de la memoria para el agente {self.name}: {len(self.memory.get_history(alias))} mensajes.")
        logger.debug(f"First contact: {is_first_contact}")

        if is_first_contact:
            # First time hearing from this alias — greet them.
            # Don't save message to memory yet; send_greeting builds its own prompt.
            return await self.send_greeting(alias)

        # Save the incoming message to memory (only for non-first-contact turns)
        if message:
            self.memory.add_message(alias, "user", message)

        # Stalemate detection: if the last 3 user messages are identical, inject a breaker
        history = self.memory.get_history(alias)
        user_msgs = [m['content'] for m in history if m.get('role') == 'user']
        if len(user_msgs) >= 3 and len(set(user_msgs[-3:])) == 1:
            logger.warning(f"Bucle de negociación detectado con '{alias}': mismo mensaje repetido {len(user_msgs)} veces. Inyectando ruptura.")
            self.memory.add_message(alias, "user",
                "AVISO DEL SISTEMA: La negociación está bloqueada porque recibes el mismo mensaje repetidamente. "
                "DEBES cambiar tu estrategia AHORA: "
                "acepta la oferta recibida enviando send_package con los recursos acordados, "
                "o haz una contraoferta COMPLETAMENTE DIFERENTE con otros recursos o cantidades distintas. "
                "NO repitas el mismo mensaje de nuevo.")

        resources = get_actual_resources_and_objectives()
        actual_resources = resources['actual']
        objective = resources['objetivo']
        missing_resources = resources['faltante']
        surplus_resources = resources['sobrante']

        agent_prompt = NEGOTIATOR_SYSTEM_PROMPT.format(
            agent_alias=alias,
            resources=actual_resources,
            objective=objective,
            missing_resources=missing_resources,
            surplus_resources=surplus_resources,
        )
        # Copy history to avoid mutating the stored list with the system prompt insert
        messages = list(self.memory.get_history(alias)[-MAX_MSGS:])
        messages.insert(0, {"role": "system", "content": agent_prompt})
        response = await self.make_response(messages, get_tools(alias))
        if response is None:
            logger.error(f"make_response devolvió None para el alias '{alias}', abortando turno.")
            return None
        logger.debug(response)
        tools_executed = await self.get_and_execute_tools(response, alias)

        content = getattr(response.message, 'content', None) or ''
        self.sync_memory(alias, tools_executed, content=content)

        # If a package was sent this turn, close the cycle with a farewell message
        package_tool = next((t for t in tools_executed if t['name'] == 'send_package'), None)
        if package_tool:
            await self.send_farewell(alias, package_tool['arguments'].get('package', {}))

        return response, tools_executed
    
    async def send_greeting(self, alias: str):
        # Mark alias as initiated BEFORE any await to prevent race conditions:
        # if the greeted agent responds before sync_memory runs, response() will
        # see is_first_contact=False and go straight to negotiation.
        self._initiated_aliases.add(alias)

        resources = get_actual_resources_and_objectives()
        actual_resources = resources['actual']
        objective = resources['objetivo']
        missing_resources = resources['faltante']
        surplus_resources = resources['sobrante']

        alias_prompt = INITIAL_GREETING_SYSTEM_PROMPT.format(
            agent_alias=alias,
            resources=actual_resources,
            objective=objective,
            missing_resources=missing_resources,
            surplus_resources=surplus_resources,
        )
        messages = [
                    {"role": "system", "content": alias_prompt},
                    {"role": "user", "content": f"Por favor, saluda al agente {alias} utilizando la herramienta correspondiente."}]
        response = await self.make_response(messages, get_tools(alias))
        if response is None:
            logger.error(f"make_response devolvió None en send_greeting para '{alias}', abortando.")
            return None

        tool_executed = await self.get_and_execute_tools(response, alias)
        content = getattr(response.message, 'content', None) or ''
        self.sync_memory(alias, tool_executed, content=content)
        return response, tool_executed

    async def send_farewell(self, alias: str, package_sent):
        """Send a closing farewell message using AGREEMENT_SYSTEM_PROMPT, then mark negotiation as done."""
        logger.info(f"Enviando despedida de cierre a '{alias}'.")
        giving = str(package_sent) if package_sent else "los recursos acordados"
        farewell_prompt = AGREEMENT_SYSTEM_PROMPT.format(
            agent_alias=alias,
            giving=giving,
            receiving="los recursos acordados en el trato"
        )
        messages = [
            {"role": "system", "content": farewell_prompt},
            {"role": "user", "content": "Envía tu mensaje de cierre al otro agente."}
        ]
        response = await self.make_response(messages, get_tools(alias))
        if response is None:
            logger.error(f"make_response devolvió None en send_farewell para '{alias}'.")
        else:
            tool_executed = await self.get_and_execute_tools(response, alias)
            farewell_desc = getattr(response.message, 'content', None) or ''
            if tool_executed:
                for tool in tool_executed:
                    if tool['name'] == 'send_message_to_alias':
                        farewell_desc = f"Despedida enviada a {alias}: '{tool['arguments'].get('mensaje')}'"
            if farewell_desc:
                self.memory.add_message(alias, "assistant", farewell_desc)

        # Reset state so a new negotiation cycle can begin with the same partner
        self._reset_negotiation(alias)
        logger.info(f"Ciclo de negociación con '{alias}' completado. Reiniciando para seguir negociando.")

    def _reset_negotiation(self, alias: str):
        """Clear memory and first-contact flag for an alias so a new negotiation cycle begins."""
        logger.info(f"Reiniciando ciclo de negociación con '{alias}'.")
        self._initiated_aliases.discard(alias)
        active_sessions.pop(alias, None)

    def sync_memory(self, alias_name: str, tool_executed: list, content: Optional[str] = None):
        # {"name": name, "arguments": arguments, "response": response}
        description = '[Ninguna acción ejecutada]'

        if not tool_executed and content:
            description = content
        elif not tool_executed and not content:
            description = '[Ninguna acción ejecutada y sin contenido para agregar a la memoria]'

        if tool_executed:
            for tool in tool_executed:
                nombre = tool['name']
                args = tool['arguments']
                if nombre == "send_message_to_alias":
                    description = f"Envié un mensaje a {args.get('alias')} con el siguiente mensaje:'{args.get('mensaje')}'"
                elif nombre == "send_package":
                    description = f"Envié un paquete a {args.get('alias')}: {args.get('package')}"

        # Avoid storing consecutive duplicate assistant messages (prevents context poisoning)
        existing = self.memory.get_history(alias_name)
        if existing and existing[-1].get('role') == 'assistant' and existing[-1].get('content') == description:
            logger.warning(f"Omitiendo mensaje de asistente duplicado en memoria para '{alias_name}'.")
            return

        self.memory.add_message(alias_name, "assistant", description)

    async def get_and_execute_tools(self, response, alias: str = None):
        tools_executed = []

        if response is None:
            logger.warning("get_and_execute_tools recibió una respuesta None, ignorando.")
            return tools_executed

        # The Ollama SDK returns an object, not a dict — use attribute access
        tool_calls = []
        try:
            tool_calls = response.message.tool_calls or []
        except AttributeError:
            # Fallback for dict-style responses
            tool_calls = response.get("message", {}).get("tool_calls", []) or []

        logger.debug(f'Nro de llamadas a herramientas en la respuesta: {len(tool_calls)}')

        # If LLM emits both send_message and send_package in one turn,
        # only keep send_package — it closes the deal and sending a message
        # first would trigger a new response cycle from the other agent.
        names = [tc.function.name for tc in tool_calls]
        if 'send_package' in names and 'send_message_to_alias' in names:
            logger.warning("LLM emitió send_message + send_package en el mismo turno — descartando send_message.")
            tool_calls = [tc for tc in tool_calls if tc.function.name == 'send_package']

        for tool_call in tool_calls:
            name = tool_call.function.name
            arguments = dict(tool_call.function.arguments)  # make a mutable copy
            # Inject alias if the LLM forgot to include it
            if alias and 'alias' not in arguments:
                logger.warning(f"Herramienta '{name}' no incluyó 'alias', inyectando '{alias}' automáticamente.")
                arguments['alias'] = alias
            logger.info(f"Intentando ejecutar la herramienta '{name}' con los siguientes argumentos: {arguments}")
            if name in AVAILABLE_TOOLS:
                response_tool = await AVAILABLE_TOOLS[name](**arguments)
                tools_executed.append({"name": name, "arguments": arguments, "response": response_tool})
        return tools_executed
        
    async def make_response(self, messages: list, tools: list = None):
        client = get_ollama_client()
        if tools is None:
            tools = TOOLS
        max_retries = 3
        retry_count = 0
        
        while retry_count < max_retries:
            try:
                logger.debug(f"Attempting to call Ollama API (attempt {retry_count + 1}/{max_retries})")
                response = await asyncio.wait_for(
                    client.chat(
                        model=config.LLM_MODEL,
                        messages=messages,
                        tools=tools
                    ),
                    timeout=60.0  # 60 second timeout
                )
                logger.debug(f"Successfully received response from Ollama")
                return response
            except asyncio.TimeoutError:
                retry_count += 1
                logger.warning(f"Timeout calling Ollama (attempt {retry_count}/{max_retries}), retrying...")
                if retry_count < max_retries:
                    await asyncio.sleep(2 ** retry_count)  # Exponential backoff
                else:
                    raise
            except Exception as e:
                retry_count += 1
                logger.error(f"Error calling Ollama (attempt {retry_count}/{max_retries}): {str(e)}")
                if retry_count < max_retries:
                    await asyncio.sleep(2 ** retry_count)  # Exponential backoff
                else:
                    raise
    
    def get_memory(self):
        return self.memory.get_all_history()