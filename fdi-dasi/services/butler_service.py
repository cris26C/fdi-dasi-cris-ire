import json
import ast
import time
from typing import Optional
import requests
from loguru import logger
import asyncio
import httpx
from core.config import config
import unicodedata
import re


class ButlerService:
    _TIMEOUT: float = 30.0
    _INFO_TTL: float = 15.0

    def __init__(self):
        self._user_connected: list = []
        self._info_cache: dict | None = None
        self._info_cache_ts: float = 0.0


    @staticmethod
    def get_connected_users() -> list:
        response = requests.get(f'{config.URL_BUTLER_SERVER}/gente', timeout=5)
        response.raise_for_status()
        return response.json()

    @staticmethod
    def get_information() -> dict:
        response = requests.get(f'{config.URL_BUTLER_SERVER}/info', timeout=5)
        response.raise_for_status()
        logger.info("Información del agente obtenida exitosamente.")
        return response.json()

    @staticmethod
    def create_alias(alias: str) -> dict:
        response = requests.post(f'{config.URL_BUTLER_SERVER}/alias/{alias}')
        response.raise_for_status()
        logger.info(f"Alias '{alias}' creado exitosamente.")
        return response.json()

    @staticmethod
    def get_my_alias(alias: str) -> Optional[str]:
        users = ButlerService.get_connected_users()
        for user in users:
            if user['alias'] == alias:
                return user['alias']
        return None

    @staticmethod
    def get_my_ip_by_alias(alias: str) -> Optional[str]:
        users = ButlerService.get_connected_users()
        logger.info(f"Buscando IP para el alias '{alias}' entre los usuarios conectados: {users}")
        for user in users:
            if user['alias'] == alias:
                logger.info(f"IP encontrada para el alias '{alias}': {user['ip']}")
                return user['ip']
        logger.warning(f"No se encontró IP para el alias '{alias}'")
        return None

    @staticmethod
    def get_alias_by_ip(ip: str) -> Optional[str]:
        users = ButlerService.get_connected_users()
        for user in users:
            if user['ip'] == ip:
                return user['alias']
        return None

    @staticmethod
    def get_or_create_alias(alias: str) -> str:
        alias_stored = ButlerService.get_my_alias(alias)
        if alias_stored:
            logger.info(f"Alias '{alias}' ya existe. Usando alias existente.")
            return alias_stored
        logger.info(f"Alias '{alias}' no encontrado. Creando nuevo alias.")
        ButlerService.create_alias(alias)
        return alias

    @staticmethod
    def process_resources_information(butler_data: dict) -> dict:
        recursos = butler_data['Recursos']
        objetivo = butler_data['Objetivo']
        info_actual = {
            "actual": dict(recursos),
            "objetivo": dict(objetivo),
            "faltante": {},
            "sobrante": {},
        }
        for resource_name in set(objetivo) | set(recursos):
            objective_amount = objetivo.get(resource_name, 0)
            actual_amount = recursos.get(resource_name, 0)
            deficit = objective_amount - actual_amount
            surplus = actual_amount - objective_amount
            if deficit > 0:
                info_actual["faltante"][resource_name] = deficit
            if surplus > 0:
                info_actual["sobrante"][resource_name] = surplus
        return info_actual


    @staticmethod
    def _clean_text(text: str) -> str:
        if not text:
            return text
        replacements = {
            '\xa0': ' ', '©': 'o', '®': 'o', '™': '',
            '€': 'e', '¥': 'y', '§': '', '¶': '', '†': '', '‡': '',
        }
        result = text
        for bad, good in replacements.items():
            result = result.replace(bad, good)
        result = ''.join(c for c in result if unicodedata.category(c)[0] != 'C' or c in '\t\n\r')
        return re.sub(r'\s+', ' ', result).strip()

    @staticmethod
    def _sanitize_mensaje(mensaje: str) -> Optional[str]:
        """Return clean plain-text mensaje, or None if the content is unrecoverable garbage."""
        stripped = mensaje.strip()
        if not stripped.startswith('{'):
            return ButlerService._clean_text(mensaje)
        if len(stripped) <= 3:
            return None

        normalized = stripped.replace('\\"', '"').replace("\\'", "'")
        _SCHEMA_KEYWORDS = ('"type"', "'type'", '"description"', "'description'",
                            '"enum"', "'enum'", '"properties"', "'properties'")
        is_schema_dump = any(kw in normalized for kw in _SCHEMA_KEYWORDS)

        if is_schema_dump:
            for key in ('"value"', '"mensaje"', '"text"', '"content"'):
                idx = normalized.find(key)
                if idx == -1:
                    continue
                tail = normalized[idx + len(key):]
                m = re.search(r':\s*"([^"]+)"', tail)
                if m and len(m.group(1)) >= 5 and not m.group(1).startswith('{'):
                    extracted = m.group(1)
                    logger.warning(f"Schema dump detectado — extraido valor interior: {extracted!r}")
                    return ButlerService._clean_text(extracted)
            logger.error(f"Schema dump detectado en mensaje — rechazando: {stripped[:80]!r}")
            return None

        try:
            parsed = json.loads(stripped)
            if not isinstance(parsed, dict):
                return None
            params = parsed.get('parameters') or {}
            if isinstance(params, dict):
                inner = params.get('mensaje')
                if isinstance(inner, str) and inner.strip() and not inner.strip().startswith('{'):
                    return ButlerService._clean_text(inner.strip())
            for key in ('solicitud', 'mensaje', 'message', 'content', 'texto', 'oferta'):
                val = parsed.get(key)
                if isinstance(val, str) and val.strip() and not val.strip().startswith('{'):
                    return ButlerService._clean_text(val.strip())
            return None
        except json.JSONDecodeError:
            if stripped.endswith('}'):
                inner = stripped[1:-1].strip()
                if inner.startswith('"') and inner.endswith('"'):
                    inner = inner[1:-1]
                if inner and len(inner) >= 10 and not inner.startswith('{'):
                    logger.warning(f"Mensaje envuelto en {{...}}, extrayendo texto interior")
                    return ButlerService._clean_text(inner)
            return None if len(stripped) < 10 else ButlerService._clean_text(mensaje)

    @staticmethod
    async def send_message(msg: str, ip: str):
        route = f'http://{ip}:7720/buzon'
        logger.info(f"Enviando mensaje a {route}: {msg}")
        async with httpx.AsyncClient(timeout=ButlerService._TIMEOUT) as client:
            response = await client.post(route, json={"msg": msg})
        return response.json()

    @staticmethod
    async def send_message_by_alias(msg: Optional[str], alias: Optional[str]):
        if msg is None or alias is None:
            raise ValueError("El mensaje y el alias no pueden ser None.")
        users = ButlerService.get_connected_users()
        for user in users:
            if user['alias'] == alias:
                return await ButlerService.send_message(msg, user['ip'])
        raise ValueError(f"Alias '{alias}' no encontrado entre los usuarios conectados.")

    @staticmethod
    async def send_message_to_alias(alias: str, mensaje: str):
        if not isinstance(alias, str) or not isinstance(mensaje, str):
            logger.error(f"El LLM alucinó los argumentos: alias={type(alias)}, mensaje={type(mensaje)}")
            return "ERROR INTERNO: 'alias' y 'mensaje' DEBEN ser texto plano (strings)."

        clean = ButlerService._sanitize_mensaje(mensaje)
        if clean is None:
            logger.error(f"send_message_to_alias: mensaje inválido para '{alias}': {mensaje!r} — envío cancelado.")
            return "ERROR: el modelo generó un mensaje inválido (JSON/vacío). Envío cancelado."
        if clean != mensaje:
            logger.warning(f"send_message_to_alias: mensaje limpiado: {mensaje!r} → {clean!r}")
            mensaje = clean

        logger.info(f"Preparando para enviar mensaje al alias '{alias}': {mensaje}")
        ip = ButlerService.get_my_ip_by_alias(alias)

        if alias.lower() == 'perro':
            logger.warning("Enviando a alias de prueba 'perro'.")
            ip = 'agent-two'
        elif alias.lower() == 'gato':
            logger.warning("Enviando a alias de prueba 'gato'.")
            ip = 'agent-one'

        route = f'http://{ip}:{config.EXTERNAL_AGENT_PORT}/buzon'
        logger.info(f"Enviando mensaje a {route}: {mensaje}")
        try:
            async with httpx.AsyncClient(timeout=ButlerService._TIMEOUT) as client:
                response = await client.post(route, json={"msg": mensaje})
                response.raise_for_status()
            logger.info(f"Mensaje enviado exitosamente a {alias}")
            return response.json()
        except httpx.ConnectError as e:
            logger.error(f"Error de conexión a {route}: {e}")
            return f"Error de conexión: No se puede contactar a {ip}:{config.EXTERNAL_AGENT_PORT}"
        except httpx.TimeoutException as e:
            logger.error(f"Timeout a {route}: {e}")
            return f"Error de timeout: {ip} no respondió en {ButlerService._TIMEOUT}s"
        except httpx.HTTPStatusError as e:
            logger.error(f"Error HTTP {e.response.status_code} a {route}: {e}")
            return f"Error HTTP: El servidor respondió con status {e.response.status_code}"
        except Exception as e:
            logger.error(f"Error inesperado a {route}: {type(e).__name__}: {e}")
            return f"Error: No se pudo contactar al agente en {route} - {e}"

    @staticmethod
    async def ensure_alias_registered(alias: str, base_delay: float = 2.0, max_delay: float = 30.0) -> str:
        attempt = 0
        while True:
            try:
                registered_alias = ButlerService.get_or_create_alias(alias)
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

    def _invalidate_info_cache(self):
        self._info_cache = None
        self._info_cache_ts = 0.0

    def get_actual_resources_and_objectives(self) -> dict:
        now = time.monotonic()
        if self._info_cache is not None and (now - self._info_cache_ts) < self._INFO_TTL:
            return self._info_cache
        response = requests.get(f'{config.URL_BUTLER_SERVER}/info')
        response.raise_for_status()
        data = response.json()
        self._info_cache = ButlerService.process_resources_information(data)
        self._info_cache_ts = now
        return self._info_cache

    def get_user_to_negotiate(self, agent_name: str) -> list:
        users = ButlerService.get_connected_users()
        for user in users:
            if user['alias'] == agent_name:
                continue
            existing = next((u for u in self._user_connected if u['alias'] == user['alias']), None)
            if existing is not None:
                user['notified'] = existing['notified']
            else:
                user['notified'] = False
                self._user_connected.append(user)
        return [u for u in self._user_connected if not u['notified']]

    async def create_agent_and_connect(self, agent, agent_name: str, greetings_enabled: bool = True):
        ButlerService.get_or_create_alias(agent_name)
        notified_aliases: set = set()

        try:
            while True:
                negotiation_user_list = self.get_user_to_negotiate(agent_name)
                new_users = [
                    u for u in negotiation_user_list
                    if not u.get('notified') and u['alias'] not in notified_aliases
                ]

                if hasattr(agent, '_initiated_aliases'):
                    stale = [a for a in list(notified_aliases) if a not in agent._initiated_aliases]
                    for a in stale:
                        notified_aliases.discard(a)
                        for u in self._user_connected:
                            if u['alias'] == a:
                                u['notified'] = False

                if new_users:
                    logger.info(f"Nuevos agentes detectados: {[u['alias'] for u in new_users]}")
                    if greetings_enabled:
                        tasks = []
                        for user in new_users:
                            alias = user['alias']
                            tasks.append(agent.response(alias, None))
                            notified_aliases.add(alias)
                        if tasks:
                            await asyncio.gather(*tasks)
                            logger.info(f"Saludos enviados a {len(tasks)} agentes.")
                    else:
                        logger.info("Saludos desactivados.")
                        for user in new_users:
                            notified_aliases.add(user['alias'])

                await asyncio.sleep(5)

        except asyncio.CancelledError:
            logger.info("Agent connection task cancelled")
            raise
        except Exception as e:
            logger.error(f"Error en el bucle de negociación: {e}")

    async def send_package(self, alias: str, package):
        if isinstance(package, str):
            try:
                package = json.loads(package)
            except (json.JSONDecodeError, ValueError):
                try:
                    package = ast.literal_eval(package)
                except (ValueError, SyntaxError) as e:
                    logger.error(f"send_package: string inválido: {package!r} — error: {e}")
                    return f"Error: el paquete no es un formato válido: {package!r}"
        if not isinstance(package, dict):
            logger.error(f"send_package: tipo inesperado: {type(package)} — valor: {package}")
            return f"Error: el paquete debe ser un diccionario, recibido: {type(package)}"

        clean_package = {k: v for k, v in package.items() if isinstance(v, int) and v > 0}
        if len(clean_package) != len(package):
            bad_keys = [k for k in package if k not in clean_package]
            logger.warning(f"send_package: claves inválidas eliminadas {bad_keys}. Original: {package} → Limpio: {clean_package}")
            package = clean_package
        if not package:
            logger.error("send_package: paquete vacío tras sanitización.")
            return "Error: el paquete no contiene recursos válidos."

        users = ButlerService.get_connected_users()
        for user in users:
            if user['alias'] == alias:
                logger.info(f"Enviando paquete a {alias}: {package}")
                async with httpx.AsyncClient(timeout=self._TIMEOUT) as client:
                    response = await client.post(f'{config.URL_BUTLER_SERVER}/paquete/{alias}', json=package)
                    logger.info(f"Respuesta paquete: {response.status_code}")
                    self._invalidate_info_cache()
                    return response.json()
        raise ValueError(f"Alias '{alias}' no encontrado entre los usuarios conectados.")
