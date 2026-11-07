"""Spanish lines for the engine's follow-up questions (UI-10, UC-04).

The engine only writes English (`contracts/engine_rules.md`, "Follow-up questions (exact
strings)"); its text is never changed. For a Spanish request the board shows the matching
Spanish line under the English one, so a volunteer can read it out. Demo translations,
approved by Paawan on 2026-11-07. `field/tests/test_words_es.py` parses the contract, so a
changed engine string fails the tests instead of silently losing its Spanish line.
"""

from __future__ import annotations

import re
from typing import Any

_DANGER = "¿Puede confirmar qué está pasando ahora mismo: está subiendo el agua, hay alguien herido"

SPANISH: dict[str, str] = {
    "What's the street address, or the nearest cross streets or landmark?": (
        "¿Cuál es la dirección, o las calles o el lugar conocido más cercano?"
    ),
    "Is this a request for help? If so, what do you need and where are you?": (
        "¿Está pidiendo ayuda? Si es así, ¿qué necesita y dónde está?"
    ),
    "Can you confirm this request is real and tell us exactly what help you need?": (
        "¿Puede confirmar que esta solicitud es real y decirnos exactamente qué ayuda necesita?"
    ),
    "Can you confirm what is happening right now: is water rising, is anyone injured, "
    "trapped or in danger?": f"{_DANGER}, atrapado o en peligro?",
    "Can you confirm what is happening right now: is water rising, is anyone injured, "
    "trapped or in danger, and is a child, elderly or disabled person with you?": (
        f"{_DANGER}, atrapado o en peligro, y hay con usted un niño, una persona mayor o una "
        "persona con discapacidad?"
    ),
    "We have different counts for this location. How many people need help right now?": (
        "Tenemos cifras distintas para este lugar. ¿Cuántas personas necesitan ayuda ahora mismo?"
    ),
    "Please confirm: is anyone in immediate danger (rising water, injury, fire, trapped), "
    "and is a child, elderly or disabled person with you?": (
        "Por favor confirme: ¿hay alguien en peligro inmediato (agua subiendo, herido, incendio, "
        "atrapado) y hay con usted un niño, una persona mayor o una persona con discapacidad?"
    ),
}

# Words that are common in Spanish rescue messages and rare in English ones.
SPANISH_WORDS = frozenset(
    {
        "ayuda", "ayúdenos", "ayudenos", "auxilio", "socorro", "agua", "necesitamos",
        "necesito", "somos", "estamos", "calle", "atrapado", "atrapados", "atrapada",
        "inundación", "inundacion", "personas", "niños", "ninos", "urgente",
        "techo", "casa", "por", "nuestra", "abuela", "abuelo",
    }
)  # fmt: skip
_WORD = re.compile(r"[a-záéíóúüñ]+")
# Two hits, so a stray "casa" or "por" in an English message is not enough on its own.
_MIN_HITS = 2


def spanish_for(question: str | None) -> str | None:
    """The Spanish line for an exact engine follow-up string, else None."""
    return SPANISH.get(question) if question else None


def is_spanish(lang_hint: str | None, raw_text: str | None) -> bool:
    """True when the intake said "es", or the text reads as Spanish (two or more common
    Spanish words). The extra line is harmless on a mixed message, so a wrong hint never
    hides it."""
    if lang_hint and lang_hint.lower() == "es":
        return True
    words = _WORD.findall((raw_text or "").lower())
    return sum(w in SPANISH_WORDS for w in words) >= _MIN_HITS


def spanish_line(rec: dict[str, Any], question: str | None) -> str | None:
    """Template helper: the Spanish line to show under `question` on a board row, or None."""
    if not is_spanish(rec.get("lang_hint"), rec.get("raw_text")):
        return None
    return spanish_for(question)
