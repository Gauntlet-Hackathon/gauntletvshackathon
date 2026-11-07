"""Demo seed: `python -m rescue_field.seed --count 200 --seed 7 [--data-dir PATH]`.

Fills the local field DB with templated, messy rescue requests (typos, ALL CAPS, SMS
shorthand, Spanish and mixed-language text, missing locations, duplicate reports, very short
and very long messages, emoji). Requests go through `process_batch`, the same code path the
intake API uses, so outbox rows are created exactly as in real use. No network, no model
calls; all text is generated from the templates below (nothing is taken from eval sets).
Output is deterministic for a given seed.
"""

from __future__ import annotations

import argparse
import random
import sys
from collections.abc import Sequence
from pathlib import Path

from rescue_field.pipeline import BatchDecider, IntakeItem, PipelineConfig, process_batch
from rescue_field.slm import Extractor
from rescue_field.store import EventStore

CHANNELS = ("sms", "social", "form", "call_note")
STREETS = (
    "Elm Street", "Cedar Lane", "Harbor Road", "Mill Creek Drive", "Pine Avenue",
    "Bayou Bend", "Oak Terrace", "Lakeview Court", "Ridge Road", "Willow Way",
    "Foundry Street", "Magnolia Boulevard",
)  # fmt: skip
SITUATIONS_EN = (
    "water is up to the porch and rising",
    "we are stuck on the roof",
    "the car is flooded and we cannot get out",
    "ground floor is under water",
    "trapped in the attic, water coming in fast",
    "tree fell on the house and the basement is flooding",
)
SITUATIONS_ES = (
    "el agua esta entrando a la casa",
    "estamos atrapados en el techo",
    "el carro se inundo y no podemos salir",
    "la planta baja esta llena de agua",
    "necesitamos ayuda, sube el agua",
)
EXTRAS_EN = (
    "we have {n} people here",
    "{n} of us including a baby",
    "my elderly mother is with us and needs insulin",
    "no food or water since yesterday",
    "there is a gas smell",
    "one person is in a wheelchair",
    "phone battery is almost dead",
)
EXTRAS_ES = (
    "somos {n} personas",
    "hay un bebe y una abuela",
    "no tenemos agua potable",
    "huele a gas",
)
SMS_SWAPS = (
    ("people", "ppl"), ("please", "plz"), ("you", "u"), ("are", "r"), ("because", "bc"),
    ("with", "w/"), ("and", "&"), ("at", "@"), ("we", "wi"), ("help", "hlp"),
)  # fmt: skip
EMOJI = ("🆘", "🌊", "🙏", "😭", "🚨", "🏠")
LONG_FILLER = (
    "I do not know who else to call because the line was busy",
    "the water started around noon and it keeps getting higher",
    "neighbors have left already and the street is empty",
    "we tried the boat but the current is too strong",
    "please send someone as soon as you can",
    "I am writing this from my phone on the second floor",
)
DRY_EN = (
    "we are safe and dry but would like to be evacuated when you can",
    "no danger here, just need a ride out of the area",
    "we are fine, please check on us when you get a chance",
)
STYLES = (
    "plain", "caps", "sms", "typo", "spanish", "mixed", "no_location", "short", "long", "emoji",
    "dry",
)  # fmt: skip
DUPLICATE_RATE = 0.12


def _typo(rng: random.Random, text: str) -> str:
    chars = list(text)
    for _ in range(max(2, len(chars) // 25)):
        i = rng.randrange(len(chars) - 1)
        if chars[i].isalpha() and chars[i + 1].isalpha():
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def _sms(text: str) -> str:
    out = " " + text.lower().replace(".", "").replace(",", "") + " "
    for long, short in SMS_SWAPS:
        out = out.replace(f" {long} ", f" {short} ")
    return out.strip()


def _address(rng: random.Random) -> str:
    return f"{rng.randrange(10, 9900)} {rng.choice(STREETS)}"


def _compose(rng: random.Random, style: str, address: str) -> str:
    n = rng.randrange(2, 9)
    en = rng.choice(SITUATIONS_EN)
    es = rng.choice(SITUATIONS_ES)
    extra_en = rng.choice(EXTRAS_EN).format(n=n)
    extra_es = rng.choice(EXTRAS_ES).format(n=n)
    if style == "spanish":
        return f"Ayuda por favor, {es}. Estamos en {address}. {extra_es}."
    if style == "mixed":
        return f"Please help, {es}. Estamos en {address}, {extra_en}"
    if style == "no_location":
        return f"Help, {en}. {extra_en}. I do not know the street name"
    if style == "dry":  # no hazard words: a low-urgency report (provisional P3 on the board)
        return f"Hello, {rng.choice(DRY_EN)}. We are {n} people at {address}."
    if style == "short":
        return rng.choice(("help flooding", "need rescue", "socorro agua", "stuck, send boat"))
    if style == "long":
        filler = ". ".join(rng.sample(LONG_FILLER, k=len(LONG_FILLER)))
        return f"Hello, {en} at {address}. {filler}. {extra_en}. {extra_es}. {filler}."
    base = f"Please help, {en} at {address}. {extra_en}."
    if style == "caps":
        return base.upper()
    if style == "sms":
        return _sms(base)
    if style == "typo":
        return _typo(rng, base)
    if style == "emoji":
        return f"{rng.choice(EMOJI)} {base} {rng.choice(EMOJI)}{rng.choice(EMOJI)}"
    return base


def generate(count: int, seed: int) -> list[IntakeItem]:
    """`count` messy requests, identical for a given seed. Some are repeat reports of an
    earlier incident (same address, reworded, often from another channel)."""
    rng = random.Random(seed)
    items: list[IntakeItem] = []
    incidents: list[tuple[str, str | None]] = []  # (address, contact of the first report)
    while len(items) < count:
        if incidents and rng.random() < DUPLICATE_RATE:
            address, first_contact = rng.choice(incidents)
            repeat = True
            style = rng.choice(("plain", "sms", "caps", "typo", "spanish"))
        else:
            address, first_contact, repeat = _address(rng), None, False
            style = rng.choice(STYLES)
        raw = _compose(rng, style, address)
        contact = f"+1555{rng.randrange(1000000, 9999999)}" if rng.random() < 0.6 else None
        if repeat:
            contact = first_contact  # a repeat report from the same phone, so it can match
        else:
            incidents.append((address, contact))
        lang = "es" if style == "spanish" else None
        items.append(IntakeItem(raw, rng.choice(CHANNELS), contact, lang))
    return items


def seed_store(
    store: EventStore,
    count: int,
    seed: int,
    *,
    extractor: Extractor | None = None,
    decide_batch: BatchDecider | None = None,
    config: PipelineConfig | None = None,
    batch_size: int = 50,
) -> int:
    """Insert generated requests via the intake pipeline. Returns the number inserted."""
    items = generate(count, seed)
    for start in range(0, len(items), batch_size):
        process_batch(
            items[start : start + batch_size],
            store=store,
            extractor=extractor,
            decide_batch=decide_batch,
            config=config,
        )
    return len(items)


def main(argv: Sequence[str] | None = None) -> int:
    from rescue_field import api

    parser = argparse.ArgumentParser(prog="rescue_field.seed", description=__doc__)
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--data-dir", help="directory holding field.db (default $FIELD_DATA_DIR)")
    args = parser.parse_args(argv)
    if args.count < 0:
        parser.error("--count must be >= 0")
    env = {"FIELD_DATA_DIR": args.data_dir} if args.data_dir else None
    store = api.open_store(env)
    try:
        n = seed_store(
            store,
            args.count,
            args.seed,
            decide_batch=api.default_decide_batch(),
            config=PipelineConfig.from_env(),
        )
    finally:
        store.close()
    print(f"seeded {n} requests into {Path(api.data_dir(env)) / api.DB_FILENAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
