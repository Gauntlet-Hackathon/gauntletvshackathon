import sys
from pathlib import Path

# `engine.fallback` is a namespace package under the repo root.
ROOT = str(Path(__file__).resolve().parents[2])
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
