"""H5 baseline runner: Opus and a generic small model, prompted like our SLM.

    python -m rescue_evals.baselines --system opus|slm --split dev|test [--mock] [--limit N]

Env: opus uses ANTHROPIC_API_KEY (+ OPUS_MODEL_ID, default claude-opus-5-5, + ANTHROPIC_BASE_URL);
slm uses SLM_ENDPOINT (OpenAI-compatible chat completions URL), SLM_MODEL_ID, optional SLM_API_KEY.
Every output is validated against the ``extracted`` contract. Rows go to
``<results-dir>/<system>/<model-id>.jsonl`` (id, set, split, model_id, model_version, timestamp,
raw_output, extracted, valid, error) and the file is then scored with the stored-baseline harness
and the standard runner report (``<results-dir>/<system>__<set>.json``).
Set E is refused (EV10 guard).
``--mock`` never touches the network; CI uses only ``--mock``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from rescue_evals.baselines.stored import run_stored_set
from rescue_evals.loaders import EvalCase, EvalSetError, load_cases
from rescue_evals.runner import (
    DEFAULT_RESULTS_DIR,
    DEFAULT_SETS_DIR,
    REPO_ROOT,
    _set_files,
    refuse_sealed,
    write_result,
)
from rescue_shared.models import Extracted

log = logging.getLogger("rescue_evals.baselines")

DEFAULT_OPUS_MODEL = "claude-opus-5-5"
SYSTEMS = ("opus", "slm")
SCHEMA_PATH = REPO_ROOT / "contracts" / "payloads" / "extracted.schema.json"


class ConfigError(Exception):
    """Missing or bad configuration; reported as a one-line message, never a traceback."""


def build_prompt() -> str:
    """Same task and output contract as the field SLM: one JSON object matching the schema."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else "{}"
    return (
        "You read one messy rescue request and extract structured facts. Reply with ONE JSON "
        "object and nothing else, matching this JSON Schema exactly (no extra keys). Use "
        "confidences between 0 and 1. Do not decide priority; only extract.\n\n" + schema
    )


def parse_output(text: str) -> Extracted:
    """Raw model text -> validated Extracted. Raises ValueError/ValidationError on any problem."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("model output contains no JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("model output is not a JSON object")
    return Extracted.model_validate(data)


def _post(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise RuntimeError(f"cannot reach {url}: {e}") from e


Generate = Callable[[str], str]


def opus_client(env: dict[str, str]) -> tuple[str, Generate]:
    key = env.get("ANTHROPIC_API_KEY")
    if not key:
        raise ConfigError("ANTHROPIC_API_KEY is not set; export it to run the opus baseline")
    model = env.get("OPUS_MODEL_ID") or DEFAULT_OPUS_MODEL
    base = (env.get("ANTHROPIC_BASE_URL") or "https://api.anthropic.com").rstrip("/")
    system = build_prompt()

    def generate(text: str) -> str:
        out = _post(
            f"{base}/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {
                "model": model,
                "max_tokens": 1024,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": text}],
            },
        )
        return "".join(b.get("text", "") for b in out.get("content", []))

    return model, generate


def slm_client(env: dict[str, str]) -> tuple[str, Generate]:
    endpoint, model = env.get("SLM_ENDPOINT"), env.get("SLM_MODEL_ID")
    missing = [n for n, v in (("SLM_ENDPOINT", endpoint), ("SLM_MODEL_ID", model)) if not v]
    if missing:
        raise ConfigError(f"{' and '.join(missing)} not set; needed for the slm baseline")
    assert endpoint and model
    headers = {"authorization": f"Bearer {env['SLM_API_KEY']}"} if env.get("SLM_API_KEY") else {}
    system = build_prompt()

    def generate(text: str) -> str:
        out = _post(
            endpoint,
            headers,
            {
                "model": model,
                "temperature": 0,
                "max_tokens": 1024,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
            },
        )
        return out["choices"][0]["message"]["content"]

    return model, generate


def mock_client(system: str, cases: Sequence[EvalCase]) -> tuple[str, Generate]:
    """Canned outputs, no network: every 4th reply is schema-invalid, every 7th is not JSON."""
    gold = {c.raw_text: c.gold_extracted for c in cases}
    order = {c.raw_text: i for i, c in enumerate(cases)}

    def generate(text: str) -> str:
        i = order.get(text, 0)
        ex = gold.get(text)
        if i % 7 == 6 or ex is None:
            return "sorry, I cannot help with that"
        data = ex.model_dump(mode="json")
        if i % 4 == 3:
            data["people_count"]["value"] = "three"  # wrong type
            data.pop("request_kind", None)
        return "```json\n" + json.dumps(data) + "\n```"

    return f"mock-{system}", generate


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)


def run_baseline(
    system: str,
    cases: Sequence[EvalCase],
    generate: Generate,
    model_id: str,
    out_path: Path,
    *,
    model_version: str | None = None,
) -> tuple[int, int]:
    """Query every case, write one JSONL row each. Returns (valid, invalid)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    version = model_version or model_id
    ok = bad = 0
    with out_path.open("w", encoding="utf-8") as fh:
        for c in cases:
            raw, extracted, error = None, None, None
            try:
                raw = generate(c.raw_text)
                extracted = parse_output(raw).model_dump(mode="json")
            except (ValueError, ValidationError) as e:  # invalid output: a counted failure
                error = f"invalid output: {str(e).splitlines()[0]}"
            except RuntimeError as e:  # transport failure: also counted, run continues
                error = str(e)
            ok, bad = (ok + 1, bad) if error is None else (ok, bad + 1)
            row = {
                "id": c.id,
                "set": c.set,
                "split": c.split,
                "system": system,
                "model_id": model_id,
                "model_version": version,
                "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "raw_output": raw,
                "extracted": extracted,
                "valid": error is None,
                "error": error,
            }
            fh.write(json.dumps(row) + "\n")
    return ok, bad


def main(argv: Sequence[str] | None = None, env: dict[str, str] | None = None) -> int:
    env = dict(os.environ) if env is None else env
    ap = argparse.ArgumentParser(description="Run the H5 baselines (opus / generic slm)")
    ap.add_argument("--system", required=True, choices=SYSTEMS)
    ap.add_argument("--split", required=True, choices=("dev", "test"))
    ap.add_argument("--sets", default="A,B,C,D")
    ap.add_argument("--mock", action="store_true", help="canned outputs, no network")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sets-dir", type=Path, default=DEFAULT_SETS_DIR)
    ap.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)

    try:
        set_names = [s.strip().upper() for s in args.sets.split(",") if s.strip()]
        by_set: dict[str, list[EvalCase]] = {}
        for name in set_names:
            refuse_sealed([], name)  # set E is never read here; no open-sealed option exists
            files = _set_files(args.sets_dir, name)
            if not files:
                raise ConfigError(f"no files for set {name} in {args.sets_dir}")
            by_set[name] = [
                c for f in files for c in load_cases(f, split=args.split) if c.set == name
            ]
        cases = [c for name in set_names for c in by_set[name]]
        if args.limit is not None:
            cases = cases[: max(args.limit, 0)]
        if args.mock:
            model_id, generate = mock_client(args.system, cases)
        elif args.system == "opus":
            model_id, generate = opus_client(env)
        else:
            model_id, generate = slm_client(env)

        out_path = args.results_dir / args.system / f"{_safe(model_id)}.jsonl"
        ok, bad = run_baseline(args.system, cases, generate, model_id, out_path)
        print(f"wrote {out_path} ({ok} valid, {bad} schema-invalid or failed)")
        for name in set_names:
            subset = [c for c in cases if c.set == name]
            if not subset:
                continue
            result = run_stored_set(subset, name, out_path, name=args.system)
            result["model_version"] = model_id
            print(f"wrote {write_result(result, args.results_dir)} (n={result['n']})")
    except (ConfigError, EvalSetError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
