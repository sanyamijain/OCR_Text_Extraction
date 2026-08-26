from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "output" / "surya"
REPORT = ROOT / "evaluation" / "structural_report.json"

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", type=int, default=None)
    args = parser.parse_args()
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in SOURCE.glob("*_surya.txt"))
    numbers = re.findall(r"(?m)^\s*(\d{1,3})[.)](?=\s|\d)", text)
    report = {
        "metric_type": "structural completeness, not character accuracy",
        "characters": len(text),
        "devanagari_characters": len(re.findall(r"[\u0900-\u097f]", text)),
        "question_number_candidates": len(set(numbers)),
        "expected_questions": args.expected,
        "question_number_recall_percent": round(100 * len(set(numbers)) / args.expected, 2) if args.expected else None,
        "unicode_replacement_characters": text.count("\ufffd"),
        "accuracy_note": "Manual ground truth is required for Hindi, English, formula, and option-pairing accuracy.",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if text else 1

if __name__ == "__main__":
    raise SystemExit(main())
