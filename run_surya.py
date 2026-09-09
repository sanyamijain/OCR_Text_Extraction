"""Backward-compatible launcher for code/run_surya.py."""
from pathlib import Path
import runpy
import sys

code = Path(__file__).resolve().parent / "code"
sys.path.insert(0, str(code))
runpy.run_path(str(code / "run_surya.py"), run_name="__main__")
