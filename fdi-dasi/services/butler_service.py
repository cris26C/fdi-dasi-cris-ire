from typing import Optional

import requests
from loguru import logger
import asyncio
import httpx
from config import config

user_connected = []

async def create_agent_and_connect(orchestrator, agent_name):
    get_or_create_alias(agent_name)
    negotiation_user_list = get_user_to_negotiate(agent_name)
    print(f"1. Usuarios conectados: {user_connected}")
    try:
        while True: 
            logger.info(f"Usuarios conectados: {user_connected}")
            negotiation_user_list = get_user_to_negotiate(agent_name)
            for user in negotiation_user_list:
                await orchestrator.add_worker_alias(user['alias'])
            await asyncio.sleep(5)
            break
    except asyncio.CancelledError:
        logger.info("Agent connection task cancelled")
        raise

def get_user_to_negotiate(agent_name):
    users = get_connected_users()

    for user in users:
        # if user['alias'] == agent_name:
        #      continue

        existing_user = next((u for u in user_connected if u['alias'] == user['alias']), None)
        if existing_user is not None:
            user['notified'] = existing_user['notified']
        else:
           user['notified'] = False
           user_connected.append(user)

    return [user for user in user_connected if not user['notified']]
    

async def async_send_message(msg: str, ip: str):
    route = f'http://{ip}:7720/buzon'
    async with httpx.AsyncClient() as client:
        response = await client.post(route, json={
            "msg": msg
        })
        return response.json()

async def send_message(msg: str, ip: str):
    route = f'http://{ip}:7720/buzon'
    logger.info(f"Enviando mensaje a {route}: {msg}")

    async with httpx.AsyncClient() as client:
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
    for user in users:
        if user['alias'] == alias:
            return user['ip']
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

async def send_package(to_alias, package):
    """"
    El formato del paquete es un diccionario con los recursos que se quieren enviar, por ejemplo:
    {
    "madera": 4,
    "oro": 2
    }
    """
    users = get_connected_users()
    for user in users:
        if user['alias'] == to_alias:
            async with httpx.AsyncClient() as client:
                response = await client.post(f'http://{user["ip"]}:7720/paquete/{to_alias}', json=package)
                return response.json()
    raise ValueError(f"Alias '{to_alias}' no encontrado entre los usuarios conectados.")