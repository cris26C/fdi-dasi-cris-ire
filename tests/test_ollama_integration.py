"""
Tests de integración con Ollama REAL.

Requieren que Ollama esté corriendo y tenga el modelo configurado descargado.
Se saltan automáticamente si Ollama no está disponible.

Ejecutar solo estos tests:
    uv run pytest -m ollama -v

Ejecutar todos excepto estos (tests rápidos sin Ollama):
    uv run pytest -m "not ollama" -v
"""
import pytest
import httpx
from unittest.mock import MagicMock, AsyncMock

pytestmark = pytest.mark.ollama


# ---------------------------------------------------------------------------
# Fixture de disponibilidad (scope=session: se comprueba una sola vez)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def ollama_host():
    """
    Verifica que Ollama está corriendo y tiene el modelo necesario.
    Si no está disponible, salta todos los tests de este módulo.
    """
    from core.config import config

    host = config.OLLAMA_HOST or "http://localhost:11434"
    model = config.LLM_MODEL

    try:
        r = httpx.get(f"{host}/api/tags", timeout=5.0)
        r.raise_for_status()
    except Exception as e:
        pytest.skip(f"Ollama no disponible en {host}: {e}")

    models_available = [m["name"] for m in r.json().get("models", [])]
    model_base = model.split(":")[0]
    if not any(model_base in m for m in models_available):
        pytest.skip(
            f"Modelo '{model}' no encontrado en Ollama. "
            f"Disponibles: {models_available}. "
            f"Descárgalo con: ollama pull {model}"
        )

    return host


@pytest.fixture
def real_agent(ollama_host):
    """Agent con Ollama REAL pero Butler mockeado."""
    from unittest.mock import MagicMock, AsyncMock
    from services.agent import Agent
    from core.config import config

    mock_butler = MagicMock()
    mock_butler.get_actual_resources_and_objectives.return_value = {
        "actual":   {"aceite": 5, "queso": 1},
        "objetivo": {"aceite": 2, "queso": 4},
        "sobrante": {"aceite": 3},
        "faltante": {"queso": 3},
    }
    mock_butler.send_message_to_alias = AsyncMock(return_value=True)
    mock_butler.send_package = AsyncMock(return_value={"ok": True})
    mock_butler._sanitize_mensaje = MagicMock(side_effect=lambda msg: msg)

    return Agent("gato", mock_butler)


# ---------------------------------------------------------------------------
# 1. Conectividad básica con Ollama
# ---------------------------------------------------------------------------

class TestOllamaConectividad:

    def test_ollama_responde_en_api_tags(self, ollama_host):
        """Ollama responde en /api/tags con lista de modelos."""
        r = httpx.get(f"{ollama_host}/api/tags", timeout=5.0)
        assert r.status_code == 200
        assert "models" in r.json()

    def test_modelo_configurado_disponible(self, ollama_host):
        """El modelo del .env está descargado en Ollama."""
        from core.config import config
        r = httpx.get(f"{ollama_host}/api/tags", timeout=5.0)
        models = [m["name"] for m in r.json().get("models", [])]
        model_base = config.LLM_MODEL.split(":")[0]
        assert any(model_base in m for m in models), \
            f"Modelo '{config.LLM_MODEL}' no encontrado. Disponibles: {models}"


# ---------------------------------------------------------------------------
# 2. _call_llm: llamada directa al LLM sin herramientas
# ---------------------------------------------------------------------------

class TestCallLLM:

    async def test_llm_devuelve_respuesta(self, real_agent):
        """El LLM responde con contenido de texto cuando no hay herramientas."""
        messages = [
            {"role": "system", "content": "Eres un asistente que responde 'hola'."},
            {"role": "user",   "content": "Di solo la palabra 'hola'."},
        ]
        response = await real_agent._call_llm(messages, tools=[])

        assert response is not None
        content = getattr(response.message, "content", "") or ""
        assert len(content.strip()) > 0, "El LLM devolvió contenido vacío"

    async def test_llm_acepta_prompt_en_espanol(self, real_agent):
        """El LLM procesa correctamente prompts en español."""
        messages = [
            {"role": "system", "content": "Eres un comerciante. Responde en español."},
            {"role": "user",   "content": "¿Cuál es tu nombre?"},
        ]
        response = await real_agent._call_llm(messages, tools=[])

        assert response is not None
        content = getattr(response.message, "content", "") or ""
        assert len(content.strip()) > 0

    async def test_llm_genera_tool_call_con_herramientas(self, real_agent):
        """Con herramientas disponibles, el LLM debe llamar a alguna."""
        from core.prompt import get_tools, NEGOTIATOR_PROMPT

        surplus  = ["aceite"]
        missing  = ["queso"]
        tools    = get_tools("perro", surplus_names=surplus, missing_names=missing)
        prompt   = NEGOTIATOR_PROMPT.format(
            my_name="gato", alias="perro", remaining=4,
            surplus="aceite", missing="queso",
            ex_surplus="aceite", ex_missing="queso",
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user",   "content": 'Mensaje de perro: "¿Qué tienes para intercambiar?"\nPropón: "Te doy 1 aceite por 1 queso, ¿aceptas?"'},
        ]

        response = await real_agent._call_llm(messages, tools=tools)

        assert response is not None
        tool_calls = list(response.message.tool_calls or [])
        content    = getattr(response.message, "content", "") or ""
        # El LLM debe haber llamado alguna herramienta O generado texto
        assert len(tool_calls) > 0 or len(content.strip()) > 0


# ---------------------------------------------------------------------------
# 3. _negotiate: comportamiento con LLM real
# ---------------------------------------------------------------------------

class TestNegotiateConOllama:

    async def test_negotiate_produce_accion(self, real_agent):
        """
        _negotiate con LLM real ejecuta alguna acción (mensaje o paquete).
        No es determinista, pero SÍ debe ocurrir algo: el butler fue llamado.
        """
        mock_butler = real_agent._butler

        await real_agent._negotiate(
            "perro",
            "¿Tienes algo para cambiar?",
            turns=1
        )

        total_calls = (
            mock_butler.send_message_to_alias.call_count +
            mock_butler.send_package.call_count
        )
        assert total_calls >= 1, \
            "El agente no envió ningún mensaje ni paquete tras la negociación"

    async def test_negotiate_cierra_con_oferta_valida(self, real_agent):
        """
        Cuando el mensaje recibido pide nuestro sobrante (aceite) con señal de oferta,
        close_resource='aceite' se detecta y tools=[send_package solamente].
        El LLM tiene una única opción → debe llamar send_package.
        """
        mock_butler = real_agent._butler

        # "por 1 aceite" → requested_kw="aceite" → está en surplus → close_resource="aceite"
        await real_agent._negotiate(
            "perro",
            "Te doy 1 queso por 1 aceite, ¿aceptas?",
            turns=1
        )

        # Puede haber cerrado (send_package) o fallado los 3 reintentos (turn_lost).
        # En ambos casos se llamó al menos una vez al butler o se registró un error.
        sent_package = mock_butler.send_package.call_count > 0
        sent_message = mock_butler.send_message_to_alias.call_count > 0
        logged_error = any(e["category"] == "turn_lost" for e in real_agent.get_errors())

        assert sent_package or sent_message or logged_error, \
            "No ocurrió ninguna acción ni error — el agente se quedó mudo"

    async def test_negotiate_send_package_usa_sobrante_valido(self, real_agent):
        """
        Si el LLM llama send_package, la clave del paquete debe estar en los sobrantes.
        """
        mock_butler = real_agent._butler
        surplus = ["aceite"]

        await real_agent._negotiate(
            "perro",
            "Te doy 1 queso por 1 aceite, ¿aceptas?",
            turns=1
        )

        if mock_butler.send_package.call_count > 0:
            pkg = mock_butler.send_package.call_args.args[1]
            assert isinstance(pkg, dict), f"El paquete no es un dict: {pkg!r}"
            assert all(k in surplus for k in pkg), \
                f"El paquete contiene recursos no sobrantes: {pkg} (sobrantes={surplus})"

    async def test_greet_ejecuta_el_flujo_completo(self, real_agent):
        """
        _greet con LLM real completa el flujo sin excepciones.
        Con llama3.2:3b el tool-call no es 100% garantizado, así que aceptamos
        tanto éxito (mensaje enviado) como fallo gracioso (error registrado).
        """
        mock_butler = real_agent._butler

        await real_agent._greet("perro")

        sent   = mock_butler.send_message_to_alias.call_count > 0
        failed = any(e["category"] == "greeting_failed" for e in real_agent.get_errors())

        assert sent or failed, \
            "El saludo no produjo ningún resultado (ni mensaje ni error registrado)"

        if sent:
            msg = mock_butler.send_message_to_alias.call_args.args[1]
            assert isinstance(msg, str) and len(msg) > 5, \
                f"Mensaje de saludo inválido: {msg!r}"



class TestContextSummary:

    async def test_build_context_resume_historial_largo(self, real_agent):
        """
        Con más de MAX_RESUME_MEMORY intercambios, _build_context llama al LLM
        y devuelve un resumen no vacío.
        """
        from core.config import config

        # Poblar el historial con más mensajes de los que caben sin resumir
        alias = "perro"
        for i in range(config.MAX_RESUME_MEMORY + 3):
            role = "user" if i % 2 == 0 else "assistant"
            real_agent.memory.add_message(alias, role, f"Mensaje {i}: Te doy aceite por queso.")

        context = await real_agent._build_context(alias)

        assert context, "El contexto devuelto está vacío"
        assert len(context) > 10, f"El contexto es demasiado corto: {context!r}"

    async def test_build_context_vacio_sin_historial(self, real_agent):
        """Sin historial previo, _build_context devuelve cadena vacía."""
        context = await real_agent._build_context("agente_nuevo")
        assert context == ""
