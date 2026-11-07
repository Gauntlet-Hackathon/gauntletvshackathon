"""CLI: engine_input JSON (object or list) on stdin -> engine_output JSON on stdout.

Error shape (the field engine client relies on it): exit code 1, empty stdout, and a single
`rescue_engine.fallback: <ErrorType>: <message>` line on stderr. Never a traceback.
"""

from __future__ import annotations

import json
import sys

from .engine import decide


def _fail(exc: BaseException) -> int:
    message = " ".join(str(exc).split())[:300]
    print(f"rescue_engine.fallback: {type(exc).__name__}: {message}", file=sys.stderr)
    return 1


def main() -> int:
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if isinstance(payload, list):
            valid = all(isinstance(item, dict) for item in payload)
        else:
            valid = isinstance(payload, dict)
        if not valid:
            raise TypeError("input must be an engine_input object or a list of them")
        result = json.dumps(decide(payload))
    except Exception as exc:  # noqa: BLE001 - the CLI contract is "never a traceback"
        return _fail(exc)
    sys.stdout.write(result + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
