import pytest
import responses as resp_mock
import respx
import httpx
from unittest.mock import patch

pytestmark = pytest.mark.asyncio



BUTLER_BASE = "http://localhost:7719"

USERS_PAYLOAD = [
    {"alias": "gato",  "ip": "172.0.0.1"},
    {"alias": "perro", "ip": "172.0.0.2"},
]

INFO_PAYLOAD = {
    "Recursos": {"aceite": 5, "queso": 1},
    "Objetivo": {"aceite": 2, "queso": 4},
}


def make_butler(url: str = BUTLER_BASE):
    """Crea una instancia de ButlerService apuntando a una URL de test."""
    from services.butler_service import ButlerService
    with patch("core.config.config") as mock_cfg:
        mock_cfg.URL_BUTLER_SERVER = url
        mock_cfg.EXTERNAL_AGENT_PORT = 7720
        mock_cfg.OLLAMA_HOST = ""
    return ButlerService()


# ---------------------------------------------------------------------------
# 1. process_resources_information
# ---------------------------------------------------------------------------

class TestProcessResources:

    def test_calcula_sobrante_y_faltante(self):
        from services.butler_service import ButlerService

        data = {
            "Recursos": {"aceite": 5, "queso": 1, "trigo": 3},
            "Objetivo": {"aceite": 2, "queso": 4, "trigo": 3},
        }
        result = ButlerService.process_resources_information(data)

        assert result["sobrante"] == {"aceite": 3}
        assert result["faltante"] == {"queso": 3}
        assert result["actual"]   == {"aceite": 5, "queso": 1, "trigo": 3}

    def test_sin_sobrante_ni_faltante_cuando_objetivo_alcanzado(self):
        from services.butler_service import ButlerService

        data = {
            "Recursos": {"aceite": 3},
            "Objetivo": {"aceite": 3},
        }
        result = ButlerService.process_resources_information(data)

        assert result["sobrante"] == {}
        assert result["faltante"] == {}

    def test_recurso_solo_en_recursos_sin_objetivo(self):
        from services.butler_service import ButlerService

        data = {
            "Recursos": {"aceite": 5, "extra": 2},
            "Objetivo": {"aceite": 3},
        }
        result = ButlerService.process_resources_information(data)

        assert result["sobrante"]["aceite"] == 2
        assert result["sobrante"]["extra"] == 2


class TestGetConnectedUsers:

    @resp_mock.activate
    def test_devuelve_lista_de_usuarios(self):
        from services.butler_service import ButlerService
        import core.config as cfg_module

        resp_mock.add(resp_mock.GET, f"{cfg_module.config.URL_BUTLER_SERVER}/gente",
                      json=USERS_PAYLOAD, status=200)

        users = ButlerService.get_connected_users()
        assert len(users) == 2
        assert users[0]["alias"] == "gato"

    @resp_mock.activate
    def test_get_alias_by_ip_encuentra_alias(self):
        from services.butler_service import ButlerService
        import core.config as cfg_module

        resp_mock.add(resp_mock.GET, f"{cfg_module.config.URL_BUTLER_SERVER}/gente",
                      json=USERS_PAYLOAD, status=200)

        alias = ButlerService.get_alias_by_ip("172.0.0.2")
        assert alias == "perro"

    @resp_mock.activate
    def test_get_alias_by_ip_devuelve_none_si_no_existe(self):
        from services.butler_service import ButlerService
        import core.config as cfg_module

        resp_mock.add(resp_mock.GET, f"{cfg_module.config.URL_BUTLER_SERVER}/gente",
                      json=USERS_PAYLOAD, status=200)

        alias = ButlerService.get_alias_by_ip("999.999.999.999")
        assert alias is None



class TestSendMessageToAlias:

    @respx.mock
    async def test_envia_mensaje_correctamente(self):
        from services.butler_service import ButlerService
        import core.config as cfg_module

        # Usamos "vecino" (alias sin override hardcodeado) para evitar la sustitución
        # Docker que ocurre con "gato" y "perro" en butler_service.py
        users_with_vecino = [{"alias": "vecino", "ip": "172.0.0.5"}]
        with patch.object(ButlerService, 'get_connected_users', return_value=users_with_vecino):
            route = respx.post(f"http://172.0.0.5:{cfg_module.config.EXTERNAL_AGENT_PORT}/buzon")
            route.mock(return_value=httpx.Response(200, json=True))

            result = await ButlerService.send_message_to_alias("vecino", "Te doy 1 aceite por 1 queso")

        assert route.called

    @respx.mock
    async def test_rechaza_mensaje_json(self):
        """Un mensaje que es un dump de JSON schema se rechaza."""
        from services.butler_service import ButlerService

        garbage = '{"type": "object", "properties": {"value": "test"}}'
        with patch.object(ButlerService, 'get_connected_users', return_value=USERS_PAYLOAD):
            result = await ButlerService.send_message_to_alias("perro", garbage)

        assert "ERROR" in str(result)

    @respx.mock
    async def test_devuelve_error_si_alias_no_existe(self):
        """Si el alias no está en /gente, la IP es None y se devuelve un string de error."""
        from services.butler_service import ButlerService

        # IP no encontrada → http://None:7720/buzon → falla la conexión → string "Error: ..."
        with patch.object(ButlerService, 'get_connected_users', return_value=[]):
            respx.post("http://none:7720/buzon").mock(side_effect=httpx.ConnectError("no route"))
            result = await ButlerService.send_message_to_alias("fantasma", "Hola")

        assert "Error" in str(result)


class TestSendPackage:

    @respx.mock
    async def test_envia_paquete_correctamente(self):
        from services.butler_service import ButlerService
        import core.config as cfg_module

        with patch.object(ButlerService, 'get_connected_users', return_value=USERS_PAYLOAD):
            route = respx.post(f"{cfg_module.config.URL_BUTLER_SERVER}/paquete/perro")
            route.mock(return_value=httpx.Response(200, json={"ok": True}))

            butler = ButlerService()
            result = await butler.send_package("perro", {"aceite": 1})

        assert route.called

    @respx.mock
    async def test_acepta_package_como_string_json(self):
        """El paquete puede llegar como string JSON (caso llama3.2:3b) y debe parsearse."""
        from services.butler_service import ButlerService
        import core.config as cfg_module

        with patch.object(ButlerService, 'get_connected_users', return_value=USERS_PAYLOAD):
            route = respx.post(f"{cfg_module.config.URL_BUTLER_SERVER}/paquete/perro")
            route.mock(return_value=httpx.Response(200, json={"ok": True}))

            butler = ButlerService()
            result = await butler.send_package("perro", '{"aceite": 1}')

        assert route.called

    @respx.mock
    async def test_rechaza_paquete_vacio(self):
        from services.butler_service import ButlerService

        with patch.object(ButlerService, 'get_connected_users', return_value=USERS_PAYLOAD):
            butler = ButlerService()
            result = await butler.send_package("perro", {})

        assert "Error" in str(result)



class TestSanitizeMensaje:

    def test_texto_limpio_se_devuelve_igual(self):
        from services.butler_service import ButlerService
        msg = "Te doy 1 aceite por 1 queso, ¿aceptas?"
        assert ButlerService._sanitize_mensaje(msg) == msg

    def test_elimina_espacios_no_separadores(self):
        from services.butler_service import ButlerService
        msg = "Hola\xa0amigo"
        result = ButlerService._sanitize_mensaje(msg)
        assert "\xa0" not in result

    def test_rechaza_schema_dump(self):
        from services.butler_service import ButlerService
        garbage = '{"type": "object", "description": "...", "properties": {}}'
        assert ButlerService._sanitize_mensaje(garbage) is None

    def test_extrae_valor_interior_de_schema_dump(self):
        from services.butler_service import ButlerService
        wrapped = '{"type": "string", "description": "test", "value": "Te doy 1 aceite"}'
        result = ButlerService._sanitize_mensaje(wrapped)
        assert result == "Te doy 1 aceite"
