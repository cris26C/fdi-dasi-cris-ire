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
from services.memory import Memory
from config import config
from services.prompt import (TOOLS, INITIAL_GREETING_SYSTEM_PROMPT, AGREEMENT_SYSTEM_PROMPT, MAX_MSGS, NEGOTIATOR_SYSTEM_PROMPT)
from ollama import AsyncClient
from services.butler_service import (send_message_to_alias, send_package, get_actual_resources_and_objectives)

AVAILABLE_TOOLS = {
    "send_message_to_alias": send_message_to_alias,
    "send_package": send_package
}

class Agent:
    def __init__(self, name: str):
        self.name = name
        self.memory = Memory()

    def receive_message(self, role: str, content: str):
        self.memory.add_message(self.name, role, content)

    async def response(self, alias: str, message: Optional[str] = None):
        # Aquí se implementaría la lógica para generar una respuesta a un mensaje recibido, utilizando la memoria del agente y posiblemente otras herramientas o funciones
        logger.debug(f"Tamaño de la memoria para el agente {self.name}: {len(self.memory.get_history(alias))} mensajes.")
        if not self.memory.agent_name_in_memory(alias):
            # Si el agente no tiene memoria previa, se le envía un saludo inicial
            return await self.send_greeting(alias)
        
        resources = get_actual_resources_and_objectives()
        actual_resources = resources['actual']
        objective = resources['objetivo']

        agent_prompt = NEGOTIATOR_SYSTEM_PROMPT.format(agent_alias=alias, resources=actual_resources, objective=objective)
        messages = self.memory.get_history(alias)[-MAX_MSGS:]
        messages.insert(0, {"role": "system", "content": agent_prompt})
        messages.append({"role": "user", "content": message})
        # logger.debug(f"Generando respuesta para el agente {self.name} con el siguiente contexto: {messages}")
        response = await self.make_response(messages)
        logger.debug(response)
        tools_executed = await self.get_and_execute_tools(response)

        self.sync_memory(alias, tools_executed, content=response['message']['content'])
        return response, tools_executed
    
    async def send_greeting(self, alias: str):
        resources = get_actual_resources_and_objectives()
        actual_resources = resources['actual']
        objective = resources['objetivo']

        alias_prompt = INITIAL_GREETING_SYSTEM_PROMPT.format(agent_alias=alias, resources=actual_resources, objective=objective)
        messages = [
                    {"role": "system", "content": alias_prompt},
                    {"role": "user", "content": f"Por favor, saluda al agente {alias} utilizando la herramienta correspondiente."}]
        response = await self.make_response(messages)

        
        tool_executed = await self.get_and_execute_tools(response)
        self.sync_memory(alias, tool_executed, content=response['message']['content'])
        return response, tool_executed

    def sync_memory(self,alias_name: str, tool_executed: list, content: Optional[str] = None):
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


        self.memory.add_message(alias_name, "assistant", description)

    async def get_and_execute_tools(self, response):
        tools_calls = []
        tools_executed = []
        logger.debug(f'Nro de llamadas a herramientas en la respuesta: {len(response.get("message", {}).get("tool_calls", []))}')
        if 'message' in response and 'tool_calls' in response['message']:
            tools_calls = response['message']['tool_calls']

        for tool_call in tools_calls:
            name = tool_call.function.name
            arguments = tool_call.function.arguments
            logger.info(f"Intentando ejecutar la herramienta '{name}' con los siguientes argumentos: {arguments}")
            if name in AVAILABLE_TOOLS:
                response_tool = await AVAILABLE_TOOLS[name](**arguments)
                tools_executed.append({"name": name, "arguments": arguments, "response": response_tool})
        return tools_executed
        
    async def make_response(self, messages: list):
        return await AsyncClient().chat(
                    model=config.LLM_MODEL,
                    messages=messages,
                    tools=TOOLS
                )