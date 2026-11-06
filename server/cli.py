"""Tiny CLI: ``python3 -m server.cli NAME``."""
import sys

from server.greeting import farewell, greet


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python3 -m server.cli NAME", file=sys.stderr)
        return 2
    try:
        print(greet(args[0]))
        print(farewell(args[0]))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
