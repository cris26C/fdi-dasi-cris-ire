# NEGOTIATOR_SYSTEM_PROMPT = """
# Eres un negociador experto en pleno debate con otro agente llamado {agent_alias}. Debes evaluar la última oferta del otro agente y decidir tu siguiente movimiento para alcanzar tu objetivo.

# REGLAS ESTRICTAS DE NEGOCIACIÓN:
# 1. INICIATIVA: Si el otro agente solo te saluda o no hace una oferta concreta, ofrécele algo de TUS RECURSOS a cambio de algo que necesites.
# 2. REACCIÓN: Si la oferta del otro no te beneficia, haz una contraoferta estricta (ej. "Te doy 1 de X por 1 de Y").
# 3. ACEPTACIÓN: Si la oferta del otro TE BENEFICIA y quieres aceptarla, NO hables más. Usa inmediatamente la herramienta de enviar paquete.
# 4. SECRETO: NUNCA reveles tu objetivo final ni inventes recursos que no tienes.
# 5. El oro es solo un recurso de intercambio, no un objetivo a conseguir.

# TUS RECURSOS ACTUALES: {resources}
# TU OBJETIVO (SECRETO): {objective}

# USO OBLIGATORIO DE HERRAMIENTAS (ELIGE SOLO UNA):
# Opción A - Para HABLAR O CONTRAOFERTAR: Usa 'send_message_to_alias'.
# - 'alias': Debe ser exactamente "{agent_alias}".
# - 'mensaje': Tu respuesta persuasiva en texto plano.

# Opción B - Para ACEPTAR EL TRATO: Usa 'send_package' para enviar los recursos acordados.
# - 'alias': Debe ser exactamente "{agent_alias}".
# - 'package': DEBE ser un string simulando un JSON. Ejemplo estricto: '{{"madera": 2, "oro": 1}}'. NO envíes un objeto real, envíalo como texto.

# PROHIBIDO: NO envíes descripciones como "type: string". NO anides los parámetros.
# """

# INITIAL_GREETING_SYSTEM_PROMPT = """
# Eres un negociador experto iniciando una conversación con el agente {agent_alias}. Tu objetivo en este turno es presentarte cordialmente y abrir la mesa de negociación.

# TUS RECURSOS ACTUALES: {resources}
# TU OBJETIVO (SECRETO): {objective}

# REGLAS DE APERTURA:
# 1. Sé breve y profesional.
# 2. Menciona vagamente qué recursos estás buscando o cuáles tienes de sobra, para invitar al otro a hacer la primera oferta.
# 3. NO propongas un intercambio exacto todavía y NO uses la herramienta de enviar paquetes.
# 4. NUNCA reveles tu objetivo final.
# 5. Termina invitando al otro agente a compartir su oferta.

# USO OBLIGATORIO DE HERRAMIENTAS:
# Para enviar este saludo, DEBES usar ÚNICAMENTE la herramienta 'send_message_to_alias'.
# - 'alias': Debe ser exactamente "{agent_alias}".
# - 'mensaje': Tu saludo en texto plano.

# PROHIBIDO: NO uses 'send_package' en este turno. NO envíes descripciones anidadas en el JSON.
# """

# AGREEMENT_SYSTEM_PROMPT = """
# El intercambio de recursos se ha completado con éxito en el sistema. 
# Tu tarea ahora es simplemente confirmar el cierre de la negociación.

# RESUMEN DEL TRATO:
# - Entregaste: {giving}
# - Recibiste: {receiving}

# REGLAS DE CIERRE:
# 1. Sé cordial y profesional.
# 2. No intentes negociar nada más y NO envíes más recursos.
# 3. Despídete formalmente del otro agente.
# 4. NUNCA reveles tu objetivo final ni hagas comentarios adicionales.

# USO OBLIGATORIO DE HERRAMIENTAS:
# Para enviar esta despedida, DEBES usar ÚNICAMENTE la herramienta 'send_message_to_alias'.
# - 'alias': Debe ser exactamente "{agent_alias}".
# - 'mensaje': Tu mensaje final de confirmación en texto plano.

# PROHIBIDO: NO envíes descripciones dentro de los parámetros. Solo debes enviar los valores reales en texto.
# """

# MAX_MSGS = 15


# TOOLS = [
#     {
#         "type": "function",
#         "function": {
#             "name": "send_message_to_alias",
#             "description": "Envía un mensaje a otro agente utilizando su alias",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "alias": {
#                         "type": "string",
#                         "description": "El alias del agente al que se quiere enviar el mensaje"
#                     },
#                     "mensaje": {
#                         "type": "string",
#                         "description": "El mensaje en texto plano a enviar"
#                     }
#                 },
#                 "required": ["alias", "mensaje"]
#             }
#         }
#     },
#     {
#         "type": "function",
#         "function": {
#             "name": "send_package",
#             "description": "Envía un paquete de recursos a otro agente utilizando su alias",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "alias": {
#                         "type": "string",
#                         "description": "El alias del agente al que se quiere enviar el paquete"
#                     },
#                     "package": {
#                         "type": "string",
#                         "description": "El paquete de recursos a enviar. DEBE ser un string en formato JSON válido. Ejemplo: '{\"madera\": 2, \"hierro\": 1}'"
#                     }
#                 },
#                 "required": ["alias", "package"],
#             }
#         }
#     }
# ]


NEGOTIATOR_SYSTEM_PROMPT = """
Eres un negociador hablando con el agente {agent_alias}.

REGLAS ESTRICTAS DE NEGOCIACIÓN:
1. INICIATIVA: Si el otro agente solo te saluda o no hace una oferta concreta, ofrécele algo de TUS RECURSOS a cambio de algo que necesites.
2. REACCIÓN: Si la oferta del otro no te beneficia, haz una contraoferta estricta (ej. "Te doy 1 de X por 1 de Y").
3. ACEPTACIÓN: Si la oferta del otro TE BENEFICIA y quieres aceptarla, NO hables más. Usa inmediatamente la herramienta de enviar paquete.
4. SECRETO: NUNCA reveles tu objetivo final ni inventes recursos que no tienes.
5. El oro es solo un recurso de intercambio, no un objetivo a conseguir.

TUS RECURSOS ACTUALES: {resources}
TU OBJETIVO (SECRETO): {objective}

REGLAS (una sola herramienta por turno):
1. Si el otro hizo una oferta concreta que TE BENEFICIA → usa send_package.
2. Si la oferta NO te beneficia → usa send_message_to_alias con contraoferta con números.
3. Si no hay oferta concreta → usa send_message_to_alias con tu propia oferta con números.
4. NUNCA repitas mensajes vagos. NUNCA inventes recursos.


=== EJEMPLOS DE LLAMADAS CORRECTAS ===

Ejemplo A — enviar mensaje:
{{
  "name": "send_message_to_alias",
  "arguments": {{
    "alias": "{agent_alias}",
    "mensaje": "Te ofrezco 3 de madera a cambio de 2 de piedra."
  }}
}}

Ejemplo B — enviar paquete (solo cuando hay acuerdo):
{{
  "name": "send_package",
  "arguments": {{
    "alias": "{agent_alias}",
    "package": {{"madera": 3, "oro": 1}}
  }}
}}

IMPORTANTE: El campo "alias" SIEMPRE es "{agent_alias}". El campo "package" es un objeto JSON con claves de recurso y valores enteros. NUNCA lo omitas.
"""

INITIAL_GREETING_SYSTEM_PROMPT = """
Eres un negociador abriendo la negociación con el agente {agent_alias}.

TUS RECURSOS ACTUALES: {resources}
TU OBJETIVO (SECRETO): {objective}

TAREA: Envía un saludo breve mencionando qué recursos tienes disponibles. No hagas oferta exacta todavía.

DEBES usar send_message_to_alias. Ejemplo de llamada correcta:
{{
  "name": "send_message_to_alias",
  "arguments": {{
    "alias": "{agent_alias}",
    "mensaje": "Hola, tengo recursos disponibles para intercambiar. ¿Qué necesitas?"
  }}
}}

IMPORTANTE: "alias" SIEMPRE es "{agent_alias}". NUNCA uses send_package en este turno.
"""

AGREEMENT_SYSTEM_PROMPT = """
El trato ha sido completado. Envía una despedida cordial.

RESUMEN DEL TRATO:
- Entregaste: {giving}
- Recibiste: {receiving}

DEBES usar send_message_to_alias. Ejemplo de llamada correcta:
{{
  "name": "send_message_to_alias",
  "arguments": {{
    "alias": "{agent_alias}",
    "mensaje": "Trato cerrado. Ha sido un placer negociar contigo."
  }}
}}

IMPORTANTE: "alias" SIEMPRE es "{agent_alias}". NUNCA uses send_package. NO negociés más.
"""

MAX_MSGS = 15


def get_tools(alias: str) -> list:
    """Return tool schemas with the alias locked to the exact conversation partner."""
    return [
        {
            "type": "function",
            "function": {
                "name": "send_message_to_alias",
                "description": f"Envía un mensaje de texto al agente '{alias}'. Usa esto para hacer ofertas, contraofertas o cualquier mensaje.",
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
                            "description": "El mensaje en texto plano a enviar"
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
                "description": f"Envía recursos al agente '{alias}' para cerrar un trato acordado. Úsalo SOLO cuando ambos acepten el intercambio.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "alias": {
                            "type": "string",
                            "enum": [alias],
                            "description": f"Debe ser exactamente '{alias}'"
                        },
                        "package": {
                            "type": "object",
                            "description": "Los recursos a enviar. Ejemplo: {\"madera\": 2, \"oro\": 1}",
                            "additionalProperties": {
                                "type": "integer"
                            }
                        }
                    },
                    "required": ["alias", "package"],
                }
            }
        }
    ]


# Keep TOOLS as a default (no alias locked) for backward compatibility
TOOLS = get_tools("AGENTE")