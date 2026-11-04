"""Load the standalone tools without leaving the repository on pytest's import path."""

import importlib
import sys
from pathlib import Path

_root = Path(__file__).resolve().parents[3]
_modules = (
    "feasibility_fetch_image",
    "feasibility_gateway",
    "feasibility_image",
    "feasibility_lifecycle",
    "feasibility_workspace",
)
sys.path.insert(0, str(_root))
try:
    for _name in _modules:
        _module = importlib.import_module(f"scripts.{_name}")
        if Path(_module.__file__).resolve() != _root / "scripts" / f"{_name}.py":
            raise ImportError(f"unexpected source for scripts.{_name}")
finally:
    sys.path.remove(str(_root))
