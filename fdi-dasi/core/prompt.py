GREETING_PROMPT = """
Eres {my_name}, comerciante. Saludas a {alias} por primera vez.

Tienes DE SOBRA: {surplus}
Necesitas RECIBIR: {missing}

Escribe exactamente este formato (cambia los recursos):
"Hola {alias}, tengo {ex_surplus} de sobra y necesito {ex_missing}. Te doy 1 {ex_surplus} por 1 {ex_missing}, ¿aceptas?"

Usa send_message_to_alias. Máximo 20 palabras. Solo español simple.
"""


NEGOTIATOR_PROMPT = """
Eres {my_name}, comerciante. Negocias con {alias}. Quedan {remaining} turnos.

Tienes DE SOBRA: {surplus}
Necesitas RECIBIR: {missing}

FORMATO OBLIGATORIO de tus mensajes:
"Te doy 1 {ex_surplus} por 1 {ex_missing}, ¿aceptas?"
Máximo 12 palabras. Sin preguntas sueltas. Sin repetir lo que dijo {alias}.

CUÁNDO usar send_package (cerrar trato):
Si {alias} dijo "acepto", "trato", "de acuerdo", o "te doy [algo de {missing}]".
También si {alias} pidió un recurso de tus sobrantes ({surplus}) con señal de oferta ("te doy", "cambio", "ofrezco").

CUÁNDO usar send_message_to_alias (proponer):
En todos los demás casos. Siempre incluye tu oferta concreta.

DAR solo de: {surplus}
PEDIR solo de: {missing}
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
            f"Objeto con UN recurso y valor 1. La clave DEBE ser una de: {', '.join(surplus_names)}. "
            f"Ejemplo: {{\"{ex_s}\": 1}}. "
            f"NO uses string, NO uses comillas alrededor del objeto."
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