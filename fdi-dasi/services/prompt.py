NEGOTIATOR_SYSTEM_PROMPT = """Eres un comerciante. Negocias con {agent_alias}. Quedan {remaining_turns} turnos.

=================== TUS RECURSOS REALES ===================
SOBRANTES (los UNICOS que puedes DAR): {surplus_resources}
FALTANTES (los UNICOS que quieres RECIBIR): {missing_resources}
===========================================================

REGLAS OBLIGATORIAS:
1. Solo puedes hablar de los recursos listados arriba. No inventes nombres de recursos. No menciones recursos que viste en mensajes anteriores si no estan en tus SOBRANTES o FALTANTES.
2. Tu mensaje NO puede ser igual al ultimo mensaje de {agent_alias}. Usa palabras diferentes.
3. Habla con "te doy", "te ofrezco". Jamas "le doy" ni "me das".

QUE SIGNIFICA EL MENSAJE DE {agent_alias}:
Si {agent_alias} dijo "te doy X por Y" entonces te ofrece X y te pide Y.

ELIGE UNA DE ESTAS ACCIONES:

ACCION A — {agent_alias} te pide {example_surplus} (esta en tus SOBRANTES):
  Cierra el trato. Usa send_package enviando {example_surplus}.

ACCION B — {agent_alias} te ofrece {example_missing} (esta en tus FALTANTES):
  Acepta. Usa send_package enviando lo que te pide a cambio (debe ser de tus SOBRANTES).

ACCION C — {agent_alias} dijo "Acepto" o "trato hecho":
  Confirma. Usa send_package con el sobrante que ya prometiste.

ACCION D — {agent_alias} te pide o te ofrece algo que NO esta en tus SOBRANTES ni FALTANTES:
  Usa send_message_to_alias. Propón un trato real con TUS recursos reales:
  ofrece dar 1 {example_surplus} y pedir 1 {example_missing}. Escribelo con palabras distintas a las de {agent_alias}.

ACCION E — Sin oferta clara todavia:
  Usa send_message_to_alias para proponer: doy 1 {example_surplus}, quiero 1 {example_missing}.

RECORDATORIO FINAL: Solo {surplus_resources} (lo que das) y {missing_resources} (lo que recibes). Cualquier otro recurso no existe para ti.
"""

SUMMARY_PROMPT = """Resume en 2-3 frases esta negociacion: que ofrecio cada parte, que fue rechazado y en que punto quedaron. Solo español, sin listas."""

INITIAL_GREETING_SYSTEM_PROMPT = """Eres un comerciante entusiasta que quiere cerrar tratos.
Hablas con {agent_alias}. Tienes de sobra: {surplus_resources}. Necesitas: {missing_resources}.
Escribe 2 frases en español, dirigiendote con "te" a {agent_alias}:
Primero menciona que tienes mucho de {example_surplus} y que es de calidad.
Luego propón el intercambio: te doy 1 {example_surplus} por 1 de tus {example_missing}.
Ejemplo: "Tengo {example_surplus} de sobra y de la mejor calidad, te doy 1 {example_surplus} por 1 de tus {example_missing}. Es un trato justo para los dos!"
Solo texto natural, sin JSON, sin llaves, sin la palabra Acepto."""

AGREEMENT_SYSTEM_PROMPT = """Ciclo cerrado con {agent_alias}.

Ya enviaste: {giving}

Despidete naturalmente en 1 frase corta con entusiasmo.
Ejemplos: "Fue un placer!", "Excelente trato, hasta la proxima!", "Cerramos bien, nos vemos!"
"""

MAX_MSGS = 10
SUMMARY_THRESHOLD = 8  # summarize older history beyond this length


def get_tools(alias: str, surplus_names: list = None, missing_names: list = None, greeting: bool = False) -> list:
    """Return tool schemas with the alias locked AND the package keys constrained to real surplus names."""
    if surplus_names:
        example_key = surplus_names[0]
        alt_key = surplus_names[-1] if len(surplus_names) > 1 else example_key
        allowed = ", ".join(surplus_names)
        package_schema = {
            "type": "object",
            "description": (
                f"UN solo recurso con valor 1. "
                f"Ejemplo correcto: {{\"{example_key}\": 1}}. "
                f"La clave DEBE ser una de: {allowed}. "
                f"El valor DEBE ser el numero entero 1. "
                f"NO incluyas 'type', 'enum', 'alias' ni otros campos."
            ),
            "properties": {k: {"type": "integer", "enum": [1]} for k in surplus_names},
            "additionalProperties": False
        }
    else:
        example_key = "recurso"
        alt_key = "recurso"
        package_schema = {
            "type": "object",
            "description": "Los recursos a enviar. Claves: nombres de recursos. Valores: enteros positivos.",
            "additionalProperties": {"type": "integer"},
        }

    example_missing = missing_names[0] if missing_names else "lo que necesitas"

    if greeting:
        msg_examples = f"'Tengo {example_key} de sobra y es de primera. Te doy 1 {example_key} por 1 de tus {example_missing}. Cerramos?'"
    else:
        msg_examples = (
            f"'Te doy 1 {example_key} por 1 de tus {example_missing}, es un buen trato!', "
            f"'Acepto el trato!', "
            f"'No tengo eso. Te doy 1 {alt_key} por 1 de tus {example_missing}'."
        )

    return [
        {
            "type": "function",
            "function": {
                "name": "send_message_to_alias",
                "description": (
                    f"Envia un mensaje de texto al agente '{alias}'. "
                    f"Usa siempre 'te' (no 'le' ni 'me'). Solo texto plano en español. "
                    f"Ejemplos: {msg_examples}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "alias": {
                            "type": "string",
                            "enum": [alias],
                            "description": f"Debe ser exactamente '{alias}'"
                        },
                        "mensaje": {
                            "type": "string",
                            "description": "Texto plano en español. NO pongas JSON ni llaves aqui."
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
                "description": f"Envia 1 unidad de UN recurso al agente '{alias}' para cerrar el trato. Solo se permite 1 unidad de un sobrante real.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "alias": {
                            "type": "string",
                            "enum": [alias],
                            "description": f"Debe ser exactamente '{alias}'"
                        },
                        "package": package_schema,
                    },
                    "required": ["alias", "package"],
                }
            }
        }
    ]


# Keep TOOLS as a default (no alias locked) for backward compatibility
TOOLS = get_tools("AGENTE")
