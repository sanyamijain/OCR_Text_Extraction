"""Backward-compatible launcher for code/export_documents.py."""
from pathlib import Path
import runpy
import sys

code = Path(__file__).resolve().parent / "code"
sys.path.insert(0, str(code))
runpy.run_path(str(code / "export_documents.py"), run_name="__main__")
