"""Tiny CLI: ``python3 -m server.cli NAME`` prints a greeting and a farewell."""

import argparse
import sys

from .greeting import farewell, greet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m server.cli")
    parser.add_argument("name", help="name to greet and bid farewell")
    args = parser.parse_args(argv)
    try:
        print(greet(args.name))
        print(farewell(args.name))
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
