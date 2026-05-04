
import json
import ast
from typing import Optional
import requests
from loguru import logger
import asyncio
import httpx
from config import config

user_connected = []
TIMEOUT = 30.0


async def ensure_alias_registered(alias: str, base_delay: float = 2.0, max_delay: float = 30.0) -> str:
    """Retry alias registration with bounded exponential backoff."""
    attempt = 0

    while True:
        try:
            registered_alias = get_or_create_alias(alias)
            if attempt:
                logger.info(f"Alias '{alias}' registrado correctamente tras {attempt + 1} intentos.")
            return registered_alias
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(
                f"No se pudo obtener o crear el alias '{alias}' (intento {attempt + 1}). "
                f"Reintentando en {delay:.1f}s. Error: {exc}"
            )
            await asyncio.sleep(delay)
            attempt += 1


async def create_agent_and_connect(agent, agent_name, greetings_enabled: bool = True):
    get_or_create_alias(agent_name)    
    # Mantenemos un registro local de a quién ya saludamos en esta sesión
    # para no depender únicamente de lo que devuelva la API
    notified_aliases = set()

    try:
        while True: 
            # 1. Obtenemos la lista actualizada desde el Butler
            negotiation_user_list = get_user_to_negotiate(agent_name)
            
            # 2. Filtramos solo los usuarios que no hemos saludado aún
            # (Verificamos tanto el flag de la API como nuestro set local)
            new_users = [
                user for user in negotiation_user_list 
                if not user.get('notified') and user['alias'] not in notified_aliases
            ]

            # 3. Si el agente reinició su negociación (alias fuera de _initiated_aliases),
            #    quitarlo de notified_aliases para poder re-saludar en el próximo ciclo
            if hasattr(agent, '_initiated_aliases'):
                stale = [a for a in list(notified_aliases) if a not in agent._initiated_aliases]
                for a in stale:
                    notified_aliases.discard(a)
                    # Resetear el flag en user_connected para que get_user_to_negotiate lo devuelva
                    for u in user_connected:
                        if u['alias'] == a:
                            u['notified'] = False

            if new_users:
                logger.info(f"Nuevos agentes detectados: {[u['alias'] for u in new_users]}")
                
                if greetings_enabled:
                    # Usamos asyncio.gather para ejecutar todos los saludos al mismo tiempo
                    tasks = []
                    for user in new_users:
                        alias = user['alias']
                        tasks.append(agent.send_greeting(alias))
                        notified_aliases.add(alias) # Marcamos localmente de inmediato
                    
                    # Ejecutamos todas las tareas de saludo concurrentemente
                    if tasks:
                        await asyncio.gather(*tasks)
                        logger.info(f"Saludos enviados a {len(tasks)} agentes.")
                else:
                    logger.info("Saludos desactivados. No se enviaron mensajes a los nuevos agentes.")
                    for user in new_users:
                        notified_aliases.add(user['alias'])

            # 4. Espera controlada antes de la siguiente verificación
            await asyncio.sleep(5)

    except asyncio.CancelledError:
        logger.info("Agent connection task cancelled")
        raise
    except Exception as e:
        logger.error(f"Error en el bucle de negociación: {e}")

def get_user_to_negotiate(agent_name):
    users = get_connected_users()

    for user in users:
        if user['alias'] == agent_name:
            continue

        existing_user = next((u for u in user_connected if u['alias'] == user['alias']), None)
        if existing_user is not None:
            user['notified'] = existing_user['notified']
        else:
           user['notified'] = False
           user_connected.append(user)

    return [user for user in user_connected if not user['notified']]
    


async def send_message(msg: str, ip: str):
    route = f'http://{ip}:7720/buzon'
    logger.info(f"Enviando mensaje a {route}: {msg}")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        response = await client.post(route, json={
            "msg": msg
        })
    return response.json()

def send_message_by_alias(msg: Optional[str], alias: Optional[str]):
    if msg is None or alias is None:
        raise ValueError("El mensaje y el alias no pueden ser None.")

    users = get_connected_users()
    for user in users:
        if user['alias'] == alias:
            return send_message(msg, user['ip'])
    raise ValueError(f"Alias '{alias}' no encontrado entre los usuarios conectados.")

def get_connected_users():
    """Obtiene la lista de usuarios (gente) conectados al servidor central"""
    response = requests.get(f'{config.URL_BUTLER_SERVER}/gente', timeout=5)
    response.raise_for_status()
    return response.json()

def get_information():
    """Obtiene la información del agente desde el servidor central"""
    response = requests.get(f'{config.URL_BUTLER_SERVER}/info', timeout=5)
    response.raise_for_status()
    logger.info("Información del agente obtenida exitosamente.")
    return response.json()

def create_alias(alias):
    """Crea un alias para el agente en el servidor central"""
    response = requests.post(f'{config.URL_BUTLER_SERVER}/alias/{alias}')
    response.raise_for_status()
    logger.info(f"Alias '{alias}' creado exitosamente.")
    return response.json()

def get_my_alias(alias):
    users = get_connected_users()
    for user in users:
        if user['alias'] == alias:
            return user['alias']
    return None

def get_my_ip_by_alias(alias):
    users = get_connected_users()
    logger.info(f"Buscando IP para el alias '{alias}' entre los usuarios conectados: {users}")
    for user in users:
        if user['alias'] == alias:
            logger.info(f"IP encontrada para el alias '{alias}': {user['ip']}")
            return user['ip']
    logger.warning(f"No se encontró IP para el alias '{alias}'")
    return None

def get_or_create_alias(alias: str) -> str:
    """Obtiene o crea un alias para el agente"""
    alias_stored = get_my_alias(alias)

    if alias_stored:
        logger.info(f"Alias '{alias}' ya existe. Usando alias existente.")
        return alias_stored
    else:
        logger.info(f"Alias '{alias}' no encontrado. Creando nuevo alias.")
        create_alias(alias)
        return alias
    
def get_actual_resources_and_objectives():
    """Obtiene los recursos actuales y objetivos del agente desde el servidor central
        y calcula los recursos faltantes y sobrantes"""
    response = requests.get(f'{config.URL_BUTLER_SERVER}/info')
    response.raise_for_status()
    logger.info("Recursos y objetivos obtenidos exitosamente.")
    data = response.json()
    return process_resources_information(data)

def process_resources_information(butler_data):
    recursos = butler_data['Recursos']
    objetivo = butler_data['Objetivo']
    info_actual = {
        "actual": recursos,
        "objetivo": objetivo,
        "faltante": {},
        "sobrante": {}
    }

    for obj in objetivo:
        info_actual["faltante"][obj] = objetivo[obj]
        info_actual["sobrante"][obj] = 0

    for obj in objetivo:
        for rec in recursos:
            if obj == rec:
                info_actual["faltante"][obj] = objetivo[obj] - recursos[rec]
                if info_actual["faltante"][obj] < 0:
                    info_actual["sobrante"][obj] = abs(info_actual["faltante"][obj])
    return info_actual

def get_alias_by_ip(ip):
    users = get_connected_users()
    for user in users:
        if user['ip'] == ip:
            return user['alias']
    return None

async def send_package(alias: str, package):
    """
    El formato del paquete es un diccionario con los recursos que se quieren enviar, por ejemplo:
    {"madera": 4, "oro": 2}
    """
    if isinstance(package, str):
        try:
            package = json.loads(package)
        except (json.JSONDecodeError, ValueError):
            try:
                # Fallback: handle Python dict syntax with single quotes (e.g. "{'madera': 2}")
                package = ast.literal_eval(package)
            except (ValueError, SyntaxError) as e:
                logger.error(f"send_package recibió un string inválido como package: {package!r} — error: {e}")
                return f"Error: el paquete no es un formato válido: {package!r}"
    if not isinstance(package, dict):
        logger.error(f"send_package recibió un tipo inesperado: {type(package)} — valor: {package}")
        return f"Error: el paquete debe ser un diccionario, recibido: {type(package)}"

    users = get_connected_users()
    for user in users:
        if user['alias'] == alias:
            logger.info(f"Enviando paquete a {alias}: {package}")
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                #Calling to BUTLER SERVER to send the package to the other agent
                response = await client.post(f'{config.URL_BUTLER_SERVER}/paquete/{alias}', json=package)
                logger.info(f"Respuesta paquete: {response.status_code}")
                return response.json()
    raise ValueError(f"Alias '{alias}' no encontrado entre los usuarios conectados.")

async def send_message_to_alias(alias: str, mensaje: str):
    if not isinstance(alias, str) or not isinstance(mensaje, str):
        logger.error(f"El LLM alucinó los argumentos: alias={type(alias)}, mensaje={type(mensaje)}")
        # Le devuelves un texto al LLM para que se dé cuenta de su error y lo intente de nuevo
        return "ERROR INTERNO: Has usado mal la herramienta. 'alias' y 'mensaje' DEBEN ser texto plano (strings), no objetos JSON con descripciones."
    logger.info(f"Preparando para enviar mensaje al alias '{alias}': {mensaje}")
    ip = get_my_ip_by_alias(alias)

    if alias == 'perro':
        logger.warning("¡CUIDADO! Estás a punto de enviar un mensaje a 'perro', que es un alias de prueba. Asegúrate de que esto es lo que quieres hacer.")
        ip = 'agent-two'
    elif alias == 'gato':
        logger.warning("¡CUIDADO! Estás a punto de enviar un mensaje a 'gato', que es un alias de prueba. Asegúrate de que esto es lo que quieres hacer.")
        ip = 'agent-one'

    route = f'http://{ip}:{config.EXTERNAL_AGENT_PORT}/buzon'
    logger.info(f"Enviando mensaje a {route}: {mensaje}")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            logger.debug(f"Conectando a {route}...")
            response = await client.post(route, json={
                "msg": mensaje
            })
            logger.debug(f"Respuesta HTTP status: {response.status_code}")
            response.raise_for_status() # Verifica que el otro servidor respondió 200 OK
        logger.info(f"Mensaje enviado exitosamente a {alias}")
        return response.json()
    except httpx.ConnectError as e:
        logger.error(f"Error de conexión enviando mensaje a {route}: {str(e)} - Verifica que el servicio {ip} está corriendo en puerto {config.EXTERNAL_AGENT_PORT}")
        return f"Error de conexión: No se puede contactar a {ip}:{config.EXTERNAL_AGENT_PORT}"
    except httpx.TimeoutException as e:
        logger.error(f"Timeout enviando mensaje a {route}: {str(e)} - El servicio tardó más de {TIMEOUT}s en responder")
        return f"Error de timeout: {ip} no respondió en {TIMEOUT}s"
    except httpx.HTTPStatusError as e:
        logger.error(f"Error HTTP {e.response.status_code} enviando mensaje a {route}: {str(e)}")
        return f"Error HTTP: El servidor respondió con status {e.response.status_code}"
    except Exception as e:
        logger.error(f"Error inesperado enviando mensaje a {route}: {type(e).__name__}: {str(e)}")
        return f"Error: No se pudo contactar al agente en {route} - {str(e)}"