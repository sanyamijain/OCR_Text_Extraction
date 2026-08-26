from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
os.environ.setdefault("HF_HOME", str(CACHE / "huggingface"))
os.environ.setdefault("HF_HUB_CACHE", str(CACHE / "huggingface" / "hub"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("TORCH_HOME", str(CACHE / "torch"))
os.environ.setdefault("XDG_CACHE_HOME", str(CACHE))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
os.environ.setdefault("TORCH_DEVICE", "cpu")
os.environ.setdefault("SURYA_INFERENCE_BACKEND", "llamacpp")
os.environ.setdefault("LLAMA_CPP_BINARY", str(ROOT / "tools" / "llama.cpp" / "llama-server.exe"))
os.environ.setdefault("LLAMA_CPP_NGL", "0")
os.environ.setdefault("SURYA_INFERENCE_PARALLEL", "1")
os.environ.setdefault("SURYA_INFERENCE_CTX_SIZE", "16384")

from bs4 import BeautifulSoup
from surya.inference import SuryaInferenceManager
from surya.input.load import load_pdf
from surya.recognition import RecognitionPredictor

INPUT = ROOT / "input"
OUTPUT = ROOT / "output" / "surya"

def html_text(value: str) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)

def main() -> int:
    parser = argparse.ArgumentParser(description="Run local Surya OCR on an exam PDF")
    parser.add_argument("pdf", nargs="?", type=Path)
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    args = parser.parse_args()
    pdf = args.pdf
    if pdf is None:
        pdfs = sorted(INPUT.glob("*.pdf"))
        if not pdfs:
            print(f"No PDF found in {INPUT}", file=sys.stderr)
            return 2
        pdf = pdfs[0]
    pdf = pdf.resolve()
    page_range = None
    if args.start is not None or args.end is not None:
        start = args.start or 0
        end = args.end if args.end is not None else start
        page_range = list(range(start, end + 1))

    images, _ = load_pdf(str(pdf), page_range=page_range)
    predictor = RecognitionPredictor(SuryaInferenceManager())
    results = predictor(images, full_page=True)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    stem = pdf.stem
    raw = []
    text_pages = []
    for offset, result in enumerate(results):
        page_index = page_range[offset] if page_range else offset
        data = result.model_dump()
        data["source_page_index"] = page_index
        raw.append(data)
        ordered = sorted(result.blocks, key=lambda block: block.reading_order)
        lines = [html_text(block.html) for block in ordered if not block.skipped]
        text_pages.append(f"===== PAGE {page_index + 1} =====\n" + "\n\n".join(filter(None, lines)))
    (OUTPUT / f"{stem}_surya.json").write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUTPUT / f"{stem}_surya.txt").write_text("\n\n".join(text_pages), encoding="utf-8")
    print(f"Processed {len(results)} page(s). Results: {OUTPUT}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
