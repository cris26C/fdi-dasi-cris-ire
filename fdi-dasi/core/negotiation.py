from typing import Optional
import json
import re


def normalize(text: str) -> str:
    s = (text or '').lower()
    s = re.sub(r"[^\w\sñáéíóúü]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


_OFFER_SIGNALS = ['te doy', 'ofrezco', 'cambio']


def has_offer_signal(text: str) -> bool:
    """True if text contains an offer verb — simple substring check, no regex."""
    low = normalize(text)
    return any(s in low for s in _OFFER_SIGNALS)


def detect_close_resource(incoming: str, surplus: list, missing: list) -> Optional[str]:
    """Return the surplus resource to send if incoming is a closeable offer, else None."""
    if not surplus:
        return None
    low = normalize(incoming)
    words = set(low.split())

    # Direct acceptance without negation ("no acepto" must NOT close)
    if ('acepto' in words or 'trato' in words or 'de acuerdo' in low) and 'no' not in words:
        return surplus[0]

    # Offer signal required for resource-based matching
    if not has_offer_signal(incoming):
        return None

    # Words after "por" = what they request FROM us; check if it's our surplus
    after_por = low.split('por', 1)[1].split() if 'por' in low else []
    for s in surplus:
        if normalize(s) in after_por:
            return s

    # Words between offer signal and "por" = what they offer TO us; check if it's our missing
    after_offer_raw = ''
    for sig in _OFFER_SIGNALS:
        if sig in low:
            after_offer_raw = low.split(sig, 1)[1]
            break
    after_offer = (after_offer_raw.split('por')[0] if 'por' in after_offer_raw else after_offer_raw).split()
    for m in missing:
        if normalize(m) in after_offer:
            return surplus[0]

    return None


def is_echo(clean: str, incoming: Optional[str]) -> bool:
    return bool(incoming and normalize(clean) == normalize(incoming))


def mentions_any(text: str, targets: list) -> bool:
    low = normalize(text)
    return any(normalize(t) in low for t in targets)


def parse_package(raw: str) -> Optional[dict]:
    """Try to parse a JSON string into a dict; returns None on failure or wrong type."""
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else None
    except Exception:
        return None


def valid_package_keys(pkg: dict, surplus: list) -> list:
    return [k for k, v in pkg.items() if k in surplus and isinstance(v, int) and v >= 1]


def format_history(msgs: list, agent_name: str, alias: str) -> str:
    return "\n".join(
        f"{alias if m['role'] == 'user' else agent_name}: {m['content']}"
        for m in msgs
    )


def build_greeting_user_prompt(alias: str, surplus: list, missing: list,
                                ex_s: str, ex_m: str, context: str) -> str:
    prefix = f"{context}\n\n" if context else ""
    return (
        f"{prefix}"
        f"Negocia con {alias}. "
        f"Tus sobrantes: {', '.join(surplus)}. "
        f"Tus faltantes: {', '.join(missing) or 'ninguno'}. "
        f"Saluda a {alias}, menciona un recurso que tienes de sobra, "
        f"menciona un recurso que necesitas y propone un intercambio 1 por 1. "
        f"Termina con una pregunta. "
        f"Ejemplo: 'Hola {alias}, tengo {ex_s} de sobra y necesito {ex_m}. ¿Te interesa intercambiar?'"
    )


def build_negotiate_user_prompt(alias: str, incoming: str, close_resource: Optional[str],
                                 ex_s: str, ex_m: str, context: str,
                                 incompatible: bool = False) -> str:
    if close_resource:
        return (
            f"Mensaje de {alias}: \"{incoming}\"\n"
            f"CIERRA EL TRATO. Llama:\n"
            f"send_package(alias=\"{alias}\", package={{\"{close_resource}\": 1}})"
        )
    prefix = f"{context}\n\n" if context else ""
    hint = (
        f"\nATENCION: {alias} no menciono {ex_m}. Reitera tu oferta exacta."
        if incompatible else ""
    )
    return (
        f"{prefix}"
        f"Mensaje de {alias}: \"{incoming}\"\n"
        f"Propón: \"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\"{hint}"
    )


def build_retry_nudge(attempt: int, reason: str, close_resource: Optional[str],
                       alias: str, ex_s: str, ex_m: str) -> str:
    if close_resource:
        return [
            f"Llama send_package(alias=\"{alias}\", package={{\"{close_resource}\": 1}}). Solo eso.",
            f"OBLIGATORIO send_package: alias=\"{alias}\", package={{\"{close_resource}\": 1}}. Sin texto.",
            f"Tool call AHORA — send_package, alias={alias!r}, package={{\"{close_resource}\": 1}}.",
        ][attempt - 1]
    if reason in ('echo', 'no_target_resource'):
        return [
            f"Nueva oferta: \"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\"",
            f"send_message_to_alias(alias=\"{alias}\", mensaje=\"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\").",
            f"OBLIGATORIO send_message_to_alias: alias=\"{alias}\", mensaje=\"Te doy 1 {ex_s} por 1 {ex_m}\".",
        ][attempt - 1]
    return [
        f"Llama send_message_to_alias: \"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\"",
        f"send_message_to_alias(alias=\"{alias}\", mensaje=\"Te doy 1 {ex_s} por 1 {ex_m}, ¿aceptas?\").",
        f"OBLIGATORIO send_message_to_alias: alias=\"{alias}\", mensaje=\"Te doy 1 {ex_s} por 1 {ex_m}\".",
    ][attempt - 1]
