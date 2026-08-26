from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from document_parser import parse_questions, section_kind, text_of
from excel_math import readable_math
from word_math import WordMathRenderer

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "output" / "surya"
DESTINATION = ROOT / "output" / "documents"
EVALUATION = ROOT / "evaluation"
HEADERS = [
    "Q.No",
    "Question (English)",
    "Question (हिंदी)",
    "Option A",
    "Option B",
    "Option C",
    "Option D",
]


def safe_sheet_name(name: str, existing: set[str]) -> str:
    base = re.sub(r"[\\/*?:\[\]]", "-", name)[:31] or "Questions"
    candidate = base
    index = 2
    while candidate in existing:
        suffix = f" ({index})"
        candidate = base[: 31 - len(suffix)] + suffix
        index += 1
    existing.add(candidate)
    return candidate


def style_sheet(ws) -> None:
    fill = PatternFill("solid", fgColor="1F4E78")
    for cell in ws[1]:
        cell.font = Font(color="FFFFFF", bold=True)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    widths = [9, 52, 52, 28, 28, 28, 28]
    for index, width in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(index)].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def build_excel(questions, destination: Path) -> None:
    wb = Workbook()
    wb.remove(wb.active)
    grouped = defaultdict(list)
    for question in questions:
        grouped[question.section].append(question)
    names: set[str] = set()
    for section, items in grouped.items():
        ws = wb.create_sheet(safe_sheet_name(section, names))
        ws.append(HEADERS)
        for question in items:
            ws.append([
                question.number,
                readable_math(question.english),
                readable_math(question.hindi),
                readable_math(question.option("A")),
                readable_math(question.option("B")),
                readable_math(question.option("C")),
                readable_math(question.option("D")),
            ])
        style_sheet(ws)
    if not wb.sheetnames:
        ws = wb.create_sheet("Questions")
        ws.append(HEADERS)
        style_sheet(ws)
    wb.save(destination)


def write_validation(questions, stem: str) -> dict:
    sections = defaultdict(list)
    for question in questions:
        sections[question.section].append(question)
    report = {"document": stem, "total_questions": len(questions), "sections": {}}
    for name, items in sections.items():
        numbers = [item.number for item in items]
        missing_numbers = []
        if numbers:
            present = set(numbers)
            missing_numbers = [number for number in range(min(numbers), max(numbers) + 1) if number not in present]
        report["sections"][name] = {
            "questions": len(items),
            "number_range": [min(numbers), max(numbers)] if numbers else None,
            "missing_numbers": missing_numbers,
            "missing_english": [item.number for item in items if not item.english],
            "missing_hindi": [item.number for item in items if not item.hindi],
            "incomplete_options": [
                item.number
                for item in items
                if "MCQ" in name and any(not item.option(letter) for letter in "ABCD")
            ],
        }
    EVALUATION.mkdir(parents=True, exist_ok=True)
    (EVALUATION / f"{stem}_structure.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def build_word(pages: list[dict], title: str, destination: Path) -> int:
    doc = Document()
    section = doc.sections[0]
    section.top_margin = Inches(0.65)
    section.bottom_margin = Inches(0.65)
    section.left_margin = Inches(0.75)
    section.right_margin = Inches(0.75)
    heading = doc.add_heading(title, level=0)
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    renderer = WordMathRenderer()
    failures = 0
    last_section = ""
    for page in pages:
        blocks = sorted(page.get("blocks", []), key=lambda b: b.get("reading_order", 0))
        for block in blocks:
            if block.get("skipped") or block.get("label") in {"PageHeader", "PageFooter", "Picture"}:
                continue
            text = text_of(block.get("html", ""))
            if not text:
                continue
            detected_section = section_kind(text)
            if detected_section and detected_section != last_section:
                doc.add_heading(text, level=1)
                last_section = detected_section
                continue
            paragraph = doc.add_paragraph()
            failures += renderer.add_block(paragraph, text, block.get("label") == "Equation")
            for run in paragraph.runs:
                run.font.size = Pt(10.5)
    doc.save(destination)
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Create structured Word and Excel documents from saved Surya JSON")
    parser.add_argument("json", nargs="?", type=Path, help="Specific *_surya.json file; default exports all")
    args = parser.parse_args()
    files = [args.json] if args.json else sorted(SOURCE.glob("*_surya.json"))
    files = [path.resolve() for path in files if path and path.exists()]
    if not files:
        print("No Surya JSON output found. Run run_surya.py first.")
        return 1
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for index, source in enumerate(files, 1):
        print(f"[{index}/{len(files)}] Reading preserved OCR: {source.name}")
        pages = json.loads(source.read_text(encoding="utf-8"))
        stem = source.stem.removesuffix("_surya")
        questions = parse_questions(pages)
        print(f"[{index}/{len(files)}] Parsed {len(questions)} question records; creating Word...")
        failures = build_word(pages, stem, DESTINATION / f"{stem}.docx")
        print(f"[{index}/{len(files)}] Creating section-wise Excel...")
        build_excel(questions, DESTINATION / f"{stem}.xlsx")
        report = write_validation(questions, stem)
        review_count = sum(
            len(details["missing_english"])
            + len(details["missing_hindi"])
            + len(details["incomplete_options"])
            for details in report["sections"].values()
        )
        print(
            f"[{index}/{len(files)}] Completed {stem}: {len(questions)} questions, "
            f"{failures} equation(s) kept as original text, {review_count} structural review item(s)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
