"""Plantillas de prompt para el agente negociador.

El LLM decide qué herramienta llamar (send_package o send_message_to_alias)
basándose en sus recursos y en el mensaje del partner. Los ejemplos en el
prompt usan los nombres reales de los recursos para que el modelo pueda
ver el patrón.
"""


GREETING_PROMPT = """
Eres {my_name}, un comerciante.

Hablas en español.
Tus mensajes son cortos y naturales.

Siempre debes usar herramientas.
En este turno usa send_message_to_alias.

No uses JSON.
No uses send_package.
Solo una acción por turno.
"""


NEGOTIATOR_PROMPT = """
Eres {my_name}, un comerciante negociando con {alias}.

Quedan {remaining} turnos.

Tus recursos sobrantes:
{surplus}

Tus recursos faltantes:
{missing}

Herramientas:
- send_message_to_alias
- send_package

IMPORTANTE:
Usa send_package INMEDIATAMENTE si el último mensaje de {alias}:
- acepta tu propuesta
- dice "acepto"
- dice "trato"
- dice "de acuerdo"
- dice "hagamos trato"
- ofrece uno de tus faltantes
- pide uno de tus sobrantes

Si ocurre cualquiera de esos casos:
USA send_package.
NO sigas negociando.

Si NO hay acuerdo:
usa send_message_to_alias.

Reglas:
- Solo puedes enviar recursos de {surplus}
- Solo puedes pedir recursos de {missing}
- No repitas la misma propuesta
- Usa mensajes cortos y naturales
- Habla en primera persona

Ejemplo de mensaje:
"Te doy 1 {ex_surplus} por 1 {ex_missing}, ¿aceptas?"

Ejemplo de cierre:
send_package(alias="{alias}", package={{"{ex_surplus}": 1}})
"""


def get_tools(alias: str, surplus_names: list = None, missing_names: list = None, greeting: bool = False) -> list:
    """Esquema de herramientas. El alias queda fijado y las claves del paquete se limitan a sobrantes reales."""
    surplus_names = surplus_names or []
    missing_names = missing_names or []
    ex_s = surplus_names[0] if surplus_names else 'recurso'
    ex_m = missing_names[0] if missing_names else 'recurso'

    if surplus_names:
        package_props = {k: {"type": "integer", "enum": [1]} for k in surplus_names}
        package_desc = (
            f"Un solo recurso con valor 1. La clave DEBE ser una de: {', '.join(surplus_names)}. "
            f"Ejemplo: {{\"{ex_s}\": 1}}"
        )
    else:
        package_props = {}
        package_desc = "No tienes sobrantes — no llames esta herramienta."

    if greeting:
        msg_hint = f"Saludo proponiendo trato. Ej: 'Hola {alias}, tengo {ex_s} y te doy 1 {ex_s} por 1 de tus {ex_m}, ¿hacemos trato?'"
    else:
        msg_hint = f"Contraoferta concreta. Ej: 'Te doy 1 {ex_s} por 1 de tus {ex_m}, ¿aceptas?'"

    return [
        {
            "type": "function",
            "function": {
                "name": "send_message_to_alias",
                "description": f"Envía un mensaje de texto plano (español) al agente '{alias}'. {msg_hint}",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "alias": {"type": "string", "enum": [alias]},
                        "mensaje": {
                            "type": "string",
                            "description": "Texto plano en español. NO pongas JSON ni llaves dentro."
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
                "description": f"Envía 1 unidad de UN recurso sobrante al agente '{alias}' para cerrar el trato.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "alias": {"type": "string", "enum": [alias]},
                        "package": {
                            "type": "object",
                            "description": package_desc,
                            "properties": package_props,
                            "additionalProperties": False,
                        },
                    },
                    "required": ["alias", "package"],
                }
            }
        }
    ]