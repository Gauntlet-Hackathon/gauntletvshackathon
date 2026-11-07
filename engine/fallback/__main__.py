"""CLI: engine_input JSON (object or list) on stdin -> engine_output JSON on stdout."""

from __future__ import annotations

import json
import sys

from .engine import decide


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        result = decide(payload)
    except (json.JSONDecodeError, KeyError, TypeError, NotImplementedError) as exc:
        print(f"engine.fallback: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
