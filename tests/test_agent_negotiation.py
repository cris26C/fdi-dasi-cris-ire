"""
Tests de integración: lógica de negociación del Agent.

Cubre:
- Detección de oferta cerrable (close_resource)
- Envío de paquete cuando hay acuerdo
- Envío de mensaje cuando no hay acuerdo
- Mecanismo de reintentos progresivos
- Límite máximo de turnos
"""
import pytest
from unittest.mock import AsyncMock, patch

from conftest import (
    make_llm_response_message,
    make_llm_response_package,
    make_llm_response_empty,
    RESOURCES_GATO,
)


pytestmark = pytest.mark.asyncio

class TestCloseResourceDetection:
    """Verifica que el parser regex identifica correctamente cuándo cerrar."""

    def _close_resource_for(self, incoming: str, surplus: list, missing: list) -> str | None:
        """Extrae close_resource usando la misma lógica que _negotiate."""
        import re
        from services.agent import _normalize

        incoming_low = _normalize(incoming)
        offer_signals = ['te doy', 'acepto', 'trato', 'de acuerdo', 'ofrezco', 'cambio']
        has_offer = any(s in incoming_low for s in offer_signals)

        rm = re.search(r'\bpor\s+(?:\d+\s+)?(?:de\s+\w+\s+)?(\w+)', incoming, re.IGNORECASE)
        om = re.search(r'(?:te\s+doy|ofrezco)\s+(?:\d+\s+)?(?:de\s+\w+\s+)?(\w+)', incoming, re.IGNORECASE)
        requested_kw = _normalize(rm.group(1)) if rm else ''
        offered_kw   = _normalize(om.group(1)) if om else ''

        if has_offer:
            if requested_kw and any(_normalize(s) == requested_kw for s in surplus):
                return requested_kw
            if offered_kw and any(_normalize(m) == offered_kw for m in missing):
                return surplus[0] if surplus else None
        return None

    def test_cierra_cuando_piden_nuestro_sobrante(self):
        # "por 1 aceite" → aceite está en nuestro surplus → cerrar dando aceite
        result = self._close_resource_for(
            "Te doy 1 queso por 1 aceite, ¿aceptas?",
            surplus=["aceite"], missing=["queso"]
        )
        assert result == "aceite"

    def test_cierra_cuando_ofrecen_nuestro_faltante(self):
        # "te doy 1 queso" → queso está en nuestro faltante → cerrar dando primer surplus
        result = self._close_resource_for(
            "Te doy 1 queso por 1 de tus aceite, ¿aceptas?",
            surplus=["aceite"], missing=["queso"]
        )
        assert result == "aceite"

    def test_no_cierra_sin_señal_de_oferta(self):
        result = self._close_resource_for(
            "¿Qué tienes de sobra?",
            surplus=["aceite"], missing=["queso"]
        )
        assert result is None

    def test_no_cierra_cuando_recursos_no_coinciden(self):
        # gato tiene aceite, le falta queso; perro ofrece trigo por madera → sin match
        result = self._close_resource_for(
            "Te doy 1 trigo por 1 madera, ¿aceptas?",
            surplus=["aceite"], missing=["queso"]
        )
        assert result is None

    def test_no_falso_positivo_recurso_en_posicion_incorrecta(self):
        # "te doy 1 aceite por 1 trigo" → aceite está en surplus PERO se está OFRECIENDO
        # a nosotros (no se está pidiendo de nosotros) → no debe cerrar por surplus
        result = self._close_resource_for(
            "Te doy 1 aceite por 1 trigo, ¿aceptas?",
            surplus=["aceite"], missing=["queso"]
        )
        # aceite está en surplus pero la oferta no cuadra (trigo no es nuestro surplus)
        # y aceite no es nuestro faltante
        assert result is None

    def test_cierra_con_formato_de_tus(self):
        result = self._close_resource_for(
            "Te doy 1 queso por 1 de tus aceite, ¿aceptas?",
            surplus=["aceite"], missing=["queso"]
        )
        assert result == "aceite"



class TestNegotiateClose:

    async def test_envia_paquete_cuando_llm_responde_send_package(self, agent, mock_butler):
        """Cuando el LLM llama send_package, el paquete se envía y se cierra el ciclo."""
        response = make_llm_response_package("aceite", alias="perro")

        with patch.object(agent, '_call_llm', return_value=response):
            await agent._negotiate("perro", "Te doy 1 queso por 1 aceite, ¿aceptas?", turns=1)

        mock_butler.send_package.assert_called_once_with("perro", {"aceite": 1})

    async def test_envia_despedida_positiva_tras_paquete(self, agent, mock_butler):
        """Tras enviar el paquete, el agente envía un mensaje de despedida."""
        response = make_llm_response_package("aceite", alias="perro")

        with patch.object(agent, '_call_llm', return_value=response):
            await agent._negotiate("perro", "Te doy 1 queso por 1 aceite, ¿aceptas?", turns=1)

        # El segundo send_message_to_alias es la despedida
        calls = mock_butler.send_message_to_alias.call_args_list
        assert len(calls) >= 1
        farewell_msg = calls[-1].args[1]  # (alias, mensaje)
        assert "trato" in farewell_msg.lower() or "placer" in farewell_msg.lower()

    async def test_resetea_estado_tras_cierre(self, agent, mock_butler):
        """Después del cierre el alias sale de _initiated y _turns."""
        agent._initiated.add("perro")
        agent._turns["perro"] = 2

        response = make_llm_response_package("aceite", alias="perro")
        with patch.object(agent, '_call_llm', return_value=response):
            await agent._negotiate("perro", "Te doy 1 queso por 1 aceite, ¿aceptas?", turns=2)

        assert "perro" not in agent._initiated
        assert "perro" not in agent._turns


class TestNegotiatePropose:

    async def test_envia_mensaje_cuando_no_hay_acuerdo(self, agent, mock_butler):
        """Cuando el LLM devuelve send_message_to_alias, el mensaje se reenvía al alias."""
        respuesta = "Te doy 1 aceite por 1 queso, ¿aceptas?"
        response = make_llm_response_message(respuesta, alias="perro")

        with patch.object(agent, '_call_llm', return_value=response):
            await agent._negotiate("perro", "¿Qué tienes para ofrecer?", turns=1)

        mock_butler.send_message_to_alias.assert_called_once_with("perro", respuesta)

    async def test_guarda_mensaje_en_memoria(self, agent, mock_butler):
        respuesta = "Te doy 1 aceite por 1 queso, ¿aceptas?"
        response = make_llm_response_message(respuesta, alias="perro")

        with patch.object(agent, '_call_llm', return_value=response):
            await agent._negotiate("perro", "¿Qué tienes para ofrecer?", turns=1)

        history = agent.memory.get_history("perro")
        assistant_msgs = [m for m in history if m["role"] == "assistant"]
        assert any(respuesta in m["content"] for m in assistant_msgs)


class TestRetryMechanism:

    async def test_reintenta_hasta_3_veces_si_falla(self, agent, mock_butler):
        """Si el LLM no llama ninguna herramienta, se reintenta hasta 3 veces."""
        empty = make_llm_response_empty()

        with patch.object(agent, '_call_llm', return_value=empty) as mock_llm:
            await agent._negotiate("perro", "¿Tienes algo para cambiar?", turns=1)

        # 1 llamada inicial + 3 reintentos = 4 en total
        assert mock_llm.call_count == 4

    async def test_para_en_primer_exito_sin_agotar_reintentos(self, agent, mock_butler):
        """Si el segundo intento tiene éxito, no hace el tercero ni el cuarto."""
        empty = make_llm_response_empty()
        success = make_llm_response_message("Te doy 1 aceite por 1 queso, ¿aceptas?", alias="perro")

        responses = [empty, success]
        call_count = 0

        async def fake_llm(messages, tools, **kwargs):
            nonlocal call_count
            r = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return r

        with patch.object(agent, '_call_llm', side_effect=fake_llm):
            await agent._negotiate("perro", "¿Tienes algo para cambiar?", turns=1)

        assert call_count == 2  # 1 inicial + 1 reintento exitoso

    async def test_registra_error_tras_agotar_reintentos(self, agent, mock_butler):
        """Después de 3 reintentos fallidos queda un error registrado."""
        empty = make_llm_response_empty()

        with patch.object(agent, '_call_llm', return_value=empty):
            await agent._negotiate("perro", "¿Tienes algo?", turns=1)

        errors = agent.get_errors()
        assert any(e["category"] == "turn_lost" for e in errors)


class TestMaxTurns:

    async def test_envia_despedida_al_alcanzar_limite(self, agent, mock_butler):
        """Al alcanzar MAX_NEGOTIATION_TURNS el agente envía despedida sin negociar."""
        from core.config import config

        agent._initiated.add("perro")
        agent._turns["perro"] = 0

        # Simular llegada de mensajes hasta el límite
        for i in range(config.MAX_NEGOTIATION_TURNS):
            await agent._handle("perro", f"Mensaje {i}")

        calls = mock_butler.send_message_to_alias.call_args_list
        farewell_calls = [c for c in calls if "[[CICLO_CERRADO]]" in str(c)]
        assert len(farewell_calls) >= 1

    async def test_no_llama_al_llm_en_turno_limite(self, agent, mock_butler):
        """En el turno límite no se llama al LLM, se va directo a despedida."""
        from core.config import config

        agent._initiated.add("perro")
        agent._turns["perro"] = config.MAX_NEGOTIATION_TURNS - 1

        with patch.object(agent, '_call_llm', new_callable=AsyncMock) as mock_llm:
            await agent._handle("perro", "Un mensaje más")

        mock_llm.assert_not_called()

class TestGreeting:

    async def test_saluda_al_primer_contacto(self, agent, mock_butler):
        """Al detectar un nuevo agente, _greet envía un mensaje de presentación."""
        response = make_llm_response_message(
            "Hola perro, tengo aceite de sobra y necesito queso. Te doy 1 aceite por 1 queso, ¿aceptas?",
            alias="perro"
        )

        with patch.object(agent, '_call_llm', return_value=response):
            await agent._greet("perro")

        mock_butler.send_message_to_alias.assert_called_once()
        msg = mock_butler.send_message_to_alias.call_args.args[1]
        assert "aceite" in msg.lower() or "queso" in msg.lower()

    async def test_no_saluda_sin_sobrantes(self, agent, mock_butler):
        """Si el agente no tiene sobrantes, no genera saludo."""
        mock_butler.get_actual_resources_and_objectives.return_value = {
            "actual": {}, "objetivo": {}, "sobrante": {}, "faltante": {"queso": 2}
        }

        with patch.object(agent, '_call_llm', new_callable=AsyncMock) as mock_llm:
            await agent._greet("perro")

        mock_llm.assert_not_called()
        mock_butler.send_message_to_alias.assert_not_called()
