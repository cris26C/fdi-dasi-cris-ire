from services import (get_connected_users,
                      get_actual_resources_and_objectives,
                      send_message,
                      get_my_ip_by_alias,
                      send_package)
from ollama import chat
from loguru import logger
from config import config
import asyncio
import json

MAX_MSGS = 15

# System promt, indica reglas y uso de tools

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "enviar_respuesta",
            "description": "Envía un mensaje a otro agente utilizando su alias",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "El alias del agente al que se quiere enviar el mensaje"
                    },
                    "mensaje": {
                        "type": "string",
                        "description": "El mensaje a enviar"
                    }
                },
                "required": ["alias", "mensaje"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_package",
            "description": "Envía un paquete de recursos a otro agente utilizando su alias",
            "parameters": {
                "type": "object",
                "properties": {
                    "alias": {
                        "type": "string",
                        "description": "El alias del agente al que se quiere enviar el paquete"
                    },
                    "package": {
                        "type": "object",
                        "description": "El paquete de recursos a enviar, en formato JSON, por ejemplo: {'madera': 2, 'hierro': 1}"
                    }
                },
                "required": ["alias", "package"],
            }
        }
    }
]

def get_system_prompt_by_alias(alias):
    return """Eres un agente negociador.
    Vas a negociar con el agente con el alias """ + alias + """ para conseguir los recursos que te faltan de tu objetivo.
    === REGLAS DE NEGOCIACIÓN ===
    1. Primera vez: saluda y propón un intercambio concreto
    2. Intercambios solo 1 a 1
    3. Negocia con recursos sobrantes Y con los recursos en "actual"
    4. Prioriza conseguir recursos faltantes del objetivo
    5. No reveles todos tus recursos innecesariamente
    6. Acepta intercambios que cumplan con el objetivo que tenemos en "actual"
    7. Si tenemos cantidades de recursos "sobrantes" al objetivo intercambialo con oro.
    8. Acepta cambios hasta que las cantidades de recursos en "objetivo" sea mayor que las de "actual".
    9. Genera intercambios siempre y cuando la cantidad de "actual" no sea menor a la cantidad "objetivo".
    10. Busca oportunidades de intercambio que maximicen los recursos objetivo.
    11. Utiliza solo lo que esta especificado como tools no crees nuevas funciones ni cambies su estructura.
    === TOOLS DISPONIBLES ===
    Si vas a utilizar send_package asegurate de usar las seguientes claves para la respuesta:
        {"alias": "<alias_agente>", "package": {"<recurso>": <cantidad>}}}
    No lo cambies, ni inventes nuevas claves, pero asegúrate de seguir este formato.
    No uses destinatario_alias como clave para la respuesta.
    <recurso> es el nombre del recurso a generar.
    <cantidad> es la cantidad del recurso a generar.
    """
# tool para enviar una respuesta
async def enviar_respuesta(alias: str, mensaje: str):
    # Enviamos la respueta
    logger.info(f"[{alias}] Enviando respuesta: {mensaje}")
    # Obtenemos la ip a partir del alias
    ip = get_my_ip_by_alias(alias)
    if ip:
        await send_message(mensaje, ip)
    return f"Respuesta enviada a {alias}"

AVAILABLE_TOOLS = {
    "enviar_respuesta": enviar_respuesta,
    "send_package": send_package
}

class Orchestrator:
    def __init__(self):
        self.conversaciones = {}
        self.locks = {}
        self.queue = asyncio.Queue()
        self.global_lock = asyncio.Lock()
        # buzón para que lea y escriba
        self.BUZON = {} 
    
    # sirve para guardar un mensaje en el buzón
    async def save_message(self, alias: str, msg: str):
        self.BUZON.setdefault(alias, []).append(msg)
        

    # tool para leer mensajes pendientes
    async def leer_buzon(self, alias: str, buzon):
        mensajes = {}
        mensajes[alias] = buzon.get(alias, []).copy()
        buzon[alias] = []
       
        return mensajes[alias]

    # permite obtener el lock asociado al alias para el worker
    async def get_lock(self, alias):
        async with self.global_lock:
            if alias not in self.locks:
                self.locks[alias] = asyncio.Lock()
            return self.locks[alias]

    async def respuesta(self, alias, msg):
        # espera para procesar por orden cuando se accede al mismo alias
        lock = await self.get_lock(alias)

        async with lock:
            system_content = generate_system_propmt(alias)
            
            if alias not in self.conversaciones:
                # realizamos copia no lo pasamos por referencia
                self.conversaciones[alias] = [{'role': 'system', 'content': system_content}]
            # Es para actualizar el system prompt de un alias que se va a enviar a Ollama    
            list_act = self.conversaciones[alias]
            list_act.append({'role': 'user', 'content': msg})
            # logger.debug(f"[ollama] >> {list_act}")
            logger.debug(f"[ollama] >> {len(list_act)}")
            logger.info(f"[ollama previous] >> {list_act[-1]}")
            # nos quedamos solo con los últimos MAX_MSGS mensajes
            list_act = list_act[-MAX_MSGS:]
            # reintroducimos el system promt en la primera posición
            list_act[0]['role'] = 'system'
            list_act[0]['content'] = system_content

            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(
                    chat,
                    model=config.LLM_MODEL, 
                    messages= list_act,
                    format='json',
                    tools=TOOLS
                    ),
                    timeout=180
                )
            except asyncio.TimeoutError:
                logger.error("Timeout en Ollama")
                return "Vuelve a preguntar, no he sido capaz de responder"
            
            tools_calls = []
            message = None
            if 'message' in response:
                message = response['message']
          
            if 'message' in response and 'tool_calls' in response['message']:
                tools_calls = response['message']['tool_calls']

            try:
                logger.debug(f"[>>>> tool calls] >> {tools_calls}")
                for tool_call in tools_calls:
                    name = tool_call.function.name
                    arguments = tool_call.function.arguments

                    logger.info(f"[>>>> tool calls] >> {name}: {arguments}")
                    if name in AVAILABLE_TOOLS:
                        response = await AVAILABLE_TOOLS[name](**arguments)

            except Exception as e:
                import traceback
                traceback.print_exc()

            logger.debug(f"[ollama] >> {message}")
            # if response is None or response.message is None or response.message.content is None:
            #     logger.error("Respuesta de Ollama es None")
            #     return "Vuelve a preguntar, no he sido capaz de responder"
            
            # mensaje = json.loads(response.message.content)

            # if 'content' in mensaje:
            #     content = mensaje['content']
            #     if type(content) == str:
            #         mensaje = json.loads(content)
            #     else:
            #         mensaje = content

            # logger.debug(f'respuesta: {mensaje}')
            #     list_act.append({
            #         "role": "tool",
            #         "name": call,
            #         "content": str(result)
            #     })

            # Se introduce en la conversación la respuesta
            #list_act.append({'role': 'assistant', 'content': message})
            logger.debug(f"[ollama updated] >> {len(list_act[-1])}")
            self.conversaciones[alias] = list_act

            return message

    async def worker(self, alias, buzon):
        # Siempre envía un mensaje al iniciar
        logger.info(f"[{alias}] Iniciando worker con buzon: {buzon}")
        try:
            resultado = await self.respuesta(alias, "Inicia una negociacion")
           
        except Exception as e:
            logger.error(f"Error en {alias}: {e}")

        logger.info(f"[{alias}] Iniciando negociacion")
       
        #while True:
        # Procesa los mensajes si lo hay
        mensajes = await self.leer_buzon(alias, buzon)
        msg = mensajes[-1] if len(mensajes) > 0 else ''
        mensaje = 'Haz una oferta para avanzar con la negociación utilizando este mensaje como contexto: ' + msg
        resultado = await self.respuesta(alias, mensaje)

        logger.info(f"[{alias}]: Mensajes guardados {len(mensajes)}")
        #if mensajes: 
        #    for mensaje in mensajes:
        #        try:
        #            mensaje = 'Haz una oferta para avanzar con la negociación utilizando este mensaje como contexto: ' + mensaje
        #            resultado = await self.respuesta(alias, mensaje)
                    
        #        except Exception as e:
        #            logger.error(f"Error en {alias}: {e}")
        #else: # Envía mensajes en caso de no tener mensajes pendientes
        #    try:
        #        resultado = await self.respuesta(alias, "Avanza con la negociación utilizando los mensajes anteriores como contexto, si no tienes nada más que decir responde 'no tengo nada más que decir'")
                
        #    except Exception as e:
        #        logger.error(f"Error en {alias}: {e}")

    async def start_agents(self):
        info = get_connected_users()
        for user in info:
            asyncio.create_task(self.worker(user["alias"], self.BUZON))

    async def add_worker_alias(self, alias):
        asyncio.create_task(self.worker(alias, self.BUZON))

    async def send(self, alias, mensaje):
        # Obtiene o crea el bucle de eventos
        loop = asyncio.get_running_loop()
        # Es una promesa
        future = loop.create_future()

        await self.queue.put({
            "alias": alias,
            "mensaje": mensaje,
            "future": future
        })

        return await future

#Se utiliza para generar el system prompt
def generate_system_propmt(alias):
    data = get_actual_resources_and_objectives()
    system_prompt = get_system_prompt_by_alias(alias)
    format_string = f"""{system_prompt}
                    Estos son los recursos que tienes para negociar:
                    {data}"""
    return format_string