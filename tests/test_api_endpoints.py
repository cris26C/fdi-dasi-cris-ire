"""
Tests de integración: endpoints FastAPI.

Cubre:
- GET  /       → devuelve 200 con el dashboard
- POST /buzon  → acepta mensaje y dispara agent.response
- WS   /ws/stats → devuelve JSON con estado del agente
"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from conftest import RESOURCES_GATO, USERS_CONNECTED


@pytest.fixture
def app_with_mocks():
    """
    Crea la app FastAPI inyectando un Agent y ButlerService falsos.
    Evita conectarse a Ollama ni al servidor Butler real.
    """
    from unittest.mock import MagicMock, AsyncMock

    mock_butler = MagicMock()
    mock_butler.get_actual_resources_and_objectives.return_value = RESOURCES_GATO
    mock_butler.get_or_create_alias = MagicMock(return_value="gato")

    mock_agent = MagicMock()
    mock_agent.name = "gato"
    mock_agent.response = AsyncMock()
    mock_agent.get_memory.return_value = {}
    mock_agent.get_errors.return_value = []
    mock_agent._initiated_aliases = set()

    # Parchamos la función lifespan para no arrancar el bucle de negociación real
    import main as app_module

    original_lifespan = app_module.lifespan

    from contextlib import asynccontextmanager
    from fastapi import FastAPI

    @asynccontextmanager
    async def mock_lifespan(app):
        app.state.butler_service = mock_butler
        app.state.agent = mock_agent
        yield

    app_module.app.router.lifespan_context = mock_lifespan

    return app_module.app, mock_agent, mock_butler


@pytest.fixture
def client(app_with_mocks):
    app, mock_agent, mock_butler = app_with_mocks
    with patch("main.ButlerService.get_alias_by_ip", return_value="perro"):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c, mock_agent, mock_butler



class TestDashboard:

    def test_dashboard_devuelve_200(self, client):
        c, _, __ = client
        response = c.get("/")
        assert response.status_code == 200

    def test_dashboard_contiene_html(self, client):
        c, _, __ = client
        response = c.get("/")
        assert "text/html" in response.headers["content-type"]



class TestBuzon:

    def test_acepta_mensaje_y_devuelve_202(self, client):
        c, mock_agent, _ = client
        response = c.post("/buzon", json={"msg": "Te doy 1 queso por 1 aceite, ¿aceptas?"})
        assert response.status_code == 202
        assert response.json()["status"] == "accepted"

    def test_dispara_agent_response(self, client):
        c, mock_agent, _ = client
        c.post("/buzon", json={"msg": "Hola gato"})
        mock_agent.response.assert_called_once()

    def test_pasa_alias_correcto_a_response(self, client):
        c, mock_agent, _ = client
        c.post("/buzon", json={"msg": "Hola"})
        call_args = mock_agent.response.call_args
        alias_arg = call_args.args[0] if call_args.args else call_args.kwargs.get("alias")
        assert alias_arg == "perro"

    def test_devuelve_500_si_falla_internamente(self, client):
        c, mock_agent, _ = client
        mock_agent.response.side_effect = Exception("fallo interno")

        with patch("main.ButlerService.get_alias_by_ip", side_effect=Exception("no ip")):
            response = c.post("/buzon", json={"msg": "Hola"})

        assert response.status_code == 500


class TestWebSocketStats:

    def test_ws_devuelve_estado_del_agente(self, client):
        c, mock_agent, mock_butler = client
        with c.websocket_connect("/ws/stats") as ws:
            data = ws.receive_json()

        assert data["agent_name"] == "gato"
        assert "resources" in data
        assert "memory" in data
        assert "errors" in data

    def test_ws_recursos_tienen_estructura_correcta(self, client):
        c, _, __ = client
        with c.websocket_connect("/ws/stats") as ws:
            data = ws.receive_json()

        resources = data["resources"]
        assert "actual"   in resources
        assert "objetivo" in resources
        assert "sobrante" in resources
        assert "faltante" in resources
