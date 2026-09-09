"""Backward-compatible launcher for code/evaluate_output.py."""
from pathlib import Path
import runpy
import sys

code = Path(__file__).resolve().parent / "code"
sys.path.insert(0, str(code))
runpy.run_path(str(code / "evaluate_output.py"), run_name="__main__")
