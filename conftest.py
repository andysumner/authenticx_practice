"""Pytest bootstrap: make the project root importable as `src.*`.

pytest's default import mode inserts the test file's directory on sys.path, not
the repo root, so `from src.api.main import app` would fail. Adding the directory
that holds this conftest (the repo root) to sys.path fixes that with no setup.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
