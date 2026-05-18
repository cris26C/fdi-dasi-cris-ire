"""
Fixtures compartidos para todos los tests de integración.
Cada fixture mockea una capa externa (Butler, Ollama) de forma aislada.
"""
import sys
import os
import pytest
from unittest.mock import AsyncMock, MagicMock

# Permite importar desde fdi-dasi/ sin instalar el paquete
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fdi-dasi"))


# ---------------------------------------------------------------------------
# Recursos de ejemplo reutilizables
# ---------------------------------------------------------------------------

RESOURCES_GATO = {
    "actual":   {"aceite": 5, "queso": 1},
    "objetivo": {"aceite": 2, "queso": 4},
    "sobrante": {"aceite": 3},
    "faltante": {"queso": 3},
}

RESOURCES_PERRO = {
    "actual":   {"queso": 5, "aceite": 1},
    "objetivo": {"queso": 2, "aceite": 4},
    "sobrante": {"queso": 3},
    "faltante": {"aceite": 3},
}

USERS_CONNECTED = [
    {"alias": "gato",  "ip": "172.0.0.1"},
    {"alias": "perro", "ip": "172.0.0.2"},
]

@pytest.fixture
def mock_butler():
    """Butler con recursos de 'gato' y métodos HTTP simulados."""
    butler = MagicMock()
    butler.get_actual_resources_and_objectives.return_value = RESOURCES_GATO
    butler.send_message_to_alias = AsyncMock(return_value=True)
    butler.send_package = AsyncMock(return_value={"ok": True})
    # _sanitize_mensaje devuelve el texto tal cual (sin cambios)
    butler._sanitize_mensaje = MagicMock(side_effect=lambda msg: msg)
    return butler


# ---------------------------------------------------------------------------
# Fixture: Agent con butler mockeado
# ---------------------------------------------------------------------------

@pytest.fixture
def agent(mock_butler):
    """Agent listo para probar sin Ollama ni Butler reales."""
    from services.agent import Agent
    return Agent("gato", mock_butler)

def make_llm_response_message(mensaje: str, alias: str = "perro"):
    """Simula que el LLM llamó a send_message_to_alias."""
    tc = MagicMock()
    tc.function.name = "send_message_to_alias"
    tc.function.arguments = {"alias": alias, "mensaje": mensaje}

    response = MagicMock()
    response.message.tool_calls = [tc]
    response.message.content = ""
    return response


def make_llm_response_package(resource: str, alias: str = "perro"):
    """Simula que el LLM llamó a send_package."""
    tc = MagicMock()
    tc.function.name = "send_package"
    tc.function.arguments = {"alias": alias, "package": {resource: 1}}

    response = MagicMock()
    response.message.tool_calls = [tc]
    response.message.content = ""
    return response


def make_llm_response_empty():
    """Simula que el LLM no llamó ninguna herramienta (solo texto)."""
    response = MagicMock()
    response.message.tool_calls = []
    response.message.content = "No sé qué hacer."
    return response
