from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache"
os.environ.setdefault("HF_HOME", str(CACHE / "huggingface"))
os.environ.setdefault("HF_HUB_CACHE", str(CACHE / "huggingface" / "hub"))
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_OFFLINE", "1")
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
from pypdfium2 import PdfDocument
from surya.inference import SuryaInferenceManager
from surya.inference.backends import llamacpp as llamacpp_backend
from surya.inference.backends import openai_client
from surya.input.load import load_pdf
from surya.recognition import RecognitionPredictor
from surya.settings import settings

INPUT = ROOT / "input"
OUTPUT = ROOT / "output" / "surya"
PAGE_OUTPUT = ROOT / "output" / "surya_pages"


def html_text(value: str) -> str:
    return BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def pdf_fingerprint(pdf: Path) -> str:
    digest = hashlib.sha256()
    with pdf.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_directory(pdf: Path, fingerprint: str) -> Path:
    safe_stem = "".join(character if character.isalnum() or character in " .-_()" else "_" for character in pdf.stem)
    return PAGE_OUTPUT / f"{safe_stem}_{fingerprint[:12]}"


def page_path(cache_dir: Path, page_index: int) -> Path:
    return cache_dir / f"page_{page_index + 1:04d}.json"


def valid_cached_page(path: Path, page_index: int) -> dict | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("source_page_index") != page_index or not isinstance(data.get("blocks"), list):
        return None
    return data


def seed_from_consolidated(cache_dir: Path, consolidated: Path, selected: set[int]) -> int:
    if not consolidated.exists():
        return 0
    try:
        pages = json.loads(consolidated.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    seeded = 0
    for data in pages if isinstance(pages, list) else []:
        page_index = data.get("source_page_index")
        if page_index not in selected or not isinstance(data.get("blocks"), list):
            continue
        target = page_path(cache_dir, page_index)
        if not target.exists():
            atomic_text(target, json.dumps(data, ensure_ascii=False, indent=2))
            seeded += 1
    return seeded


def page_text(data: dict) -> str:
    blocks = sorted(data.get("blocks", []), key=lambda block: block.get("reading_order", 0))
    lines = [
        html_text(block.get("html", ""))
        for block in blocks
        if not block.get("skipped") and html_text(block.get("html", ""))
    ]
    return f"===== PAGE {data['source_page_index'] + 1} =====\n" + "\n\n".join(lines)


def progress(completed: int, total: int, page_number: int, elapsed: float, durations: list[float], status: str) -> None:
    percent = 100.0 * completed / total if total else 100.0
    filled = round(percent / 5)
    bar = "#" * filled + "-" * (20 - filled)
    eta = "calculating"
    if durations and completed < total:
        remaining = sum(durations) / len(durations) * (total - completed)
        eta = f"{int(remaining // 60):02d}:{int(remaining % 60):02d}"
    print(
        f"Page {page_number}/{total} [{bar}] {percent:5.1f}% | "
        f"elapsed {int(elapsed // 60):02d}:{int(elapsed % 60):02d} | ETA {eta} | {status}",
        flush=True,
    )


def merge_pages(cache_dir: Path, indexes: list[int], json_path: Path, text_path: Path) -> list[int]:
    pages: list[dict] = []
    missing: list[int] = []
    for page_index in indexes:
        data = valid_cached_page(page_path(cache_dir, page_index), page_index)
        if data is None:
            missing.append(page_index)
        else:
            pages.append(data)
    if missing:
        return missing
    atomic_text(json_path, json.dumps(pages, ensure_ascii=False, indent=2))
    atomic_text(text_path, "\n\n".join(page_text(data) for data in pages))
    return []


def configure_bounded_inference(timeout_seconds: int, retries: int) -> None:
    """Limit one page's wait without changing prompts, model, images, or decoding."""
    settings.SURYA_INFERENCE_TIMEOUT_SECONDS = float(timeout_seconds)
    original = openai_client.chat_completions_batch

    def bounded_chat_completions_batch(*args, **kwargs):
        kwargs["max_retries"] = retries
        return original(*args, **kwargs)

    llamacpp_backend.chat_completions_batch = bounded_chat_completions_batch


def main() -> int:
    parser = argparse.ArgumentParser(description="Run resumable local Surya OCR on an exam PDF")
    parser.add_argument("pdf", nargs="?", type=Path)
    parser.add_argument("--start", type=int, default=None)
    parser.add_argument("--end", type=int, default=None)
    parser.add_argument("--force", action="store_true", help="Reprocess selected pages even when cached")
    parser.add_argument("--page-retries", type=int, default=0, help="Extra page-level attempts after backend retries")
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=240,
        help="Seconds allowed per inference attempt (default: 240)",
    )
    parser.add_argument(
        "--inference-retries",
        type=int,
        default=1,
        help="Backend retries for timeout/repetition (default: 1)",
    )
    parser.add_argument("--status", action="store_true", help="Show resumable cache status without running OCR")
    args = parser.parse_args()
    if args.request_timeout < 30:
        parser.error("--request-timeout must be at least 30 seconds")
    if args.inference_retries < 0 or args.page_retries < 0:
        parser.error("retry counts cannot be negative")
    configure_bounded_inference(args.request_timeout, args.inference_retries)

    pdf = args.pdf
    if pdf is None:
        pdfs = sorted(INPUT.glob("*.pdf"))
        if not pdfs:
            print(f"No PDF found in {INPUT}", file=sys.stderr)
            return 2
        pdf = pdfs[0]
    pdf = pdf.resolve()
    if not pdf.exists():
        print(f"PDF not found: {pdf}", file=sys.stderr)
        return 2

    document = PdfDocument(str(pdf))
    total_pdf_pages = len(document)
    document.close()
    start = args.start if args.start is not None else 0
    end = args.end if args.end is not None else total_pdf_pages - 1
    if start < 0 or end < start or end >= total_pdf_pages:
        print(
            f"Invalid page range {start}-{end}. This PDF has {total_pdf_pages} pages; "
            f"valid indexes are 0-{total_pdf_pages - 1}.",
            file=sys.stderr,
        )
        return 2
    indexes = list(range(start, end + 1))
    selected = set(indexes)

    fingerprint = pdf_fingerprint(pdf)
    cache_dir = cache_directory(pdf, fingerprint)
    cache_dir.mkdir(parents=True, exist_ok=True)
    atomic_text(
        cache_dir / "manifest.json",
        json.dumps(
            {"pdf": str(pdf), "sha256": fingerprint, "total_pages": total_pdf_pages},
            ensure_ascii=False,
            indent=2,
        ),
    )
    OUTPUT.mkdir(parents=True, exist_ok=True)
    base_stem = pdf.stem
    output_stem = base_stem if start == 0 and end == total_pdf_pages - 1 else f"{base_stem}_pages_{start + 1}-{end + 1}"
    json_path = OUTPUT / f"{output_stem}_surya.json"
    text_path = OUTPUT / f"{output_stem}_surya.txt"

    seeded = seed_from_consolidated(cache_dir, OUTPUT / f"{base_stem}_surya.json", selected)
    if seeded:
        print(f"Recovered {seeded} previously completed page(s) into the resumable cache.")

    cached = {index for index in indexes if valid_cached_page(page_path(cache_dir, index), index) is not None}
    pending = indexes if args.force else [index for index in indexes if index not in cached]
    print(
        f"PDF: {pdf.name}\nPages selected: {len(indexes)} | already complete: "
        f"{0 if args.force else len(cached)} | remaining: {len(pending)}",
        flush=True,
    )
    if args.status:
        return 0

    predictor = None
    durations: list[float] = []
    run_started = time.monotonic()
    completed = 0
    for ordinal, page_index in enumerate(indexes, 1):
        if page_index not in pending:
            completed += 1
            progress(completed, len(indexes), ordinal, time.monotonic() - run_started, durations, "cached")
            continue
        if predictor is None:
            predictor = RecognitionPredictor(SuryaInferenceManager())
        success = False
        page_started = time.monotonic()
        for attempt in range(args.page_retries + 1):
            try:
                images, _ = load_pdf(str(pdf), page_range=[page_index])
                results = predictor(images, full_page=True)
                if len(results) != 1:
                    raise RuntimeError(f"Expected one page result, received {len(results)}")
                data = results[0].model_dump()
                data["source_page_index"] = page_index
                if not isinstance(data.get("blocks"), list) or not data["blocks"]:
                    raise RuntimeError("Surya returned no OCR blocks")
                atomic_text(page_path(cache_dir, page_index), json.dumps(data, ensure_ascii=False, indent=2))
                error_file = cache_dir / f"page_{page_index + 1:04d}.error.json"
                if error_file.exists():
                    error_file.unlink()
                success = True
                break
            except KeyboardInterrupt:
                print(f"\nStopped safely. Pages already written to {cache_dir}", file=sys.stderr)
                return 130
            except Exception as error:
                atomic_text(
                    cache_dir / f"page_{page_index + 1:04d}.error.json",
                    json.dumps({"page": page_index + 1, "attempt": attempt + 1, "error": str(error)}, indent=2),
                )
                print(f"Page {page_index + 1} attempt {attempt + 1} failed: {error}", file=sys.stderr)
        duration = time.monotonic() - page_started
        durations.append(duration)
        if success:
            completed += 1
            status = f"saved ({duration:.1f}s)"
        else:
            status = "failed; continue and rerun to retry"
        progress(completed, len(indexes), ordinal, time.monotonic() - run_started, durations, status)

    missing = merge_pages(cache_dir, indexes, json_path, text_path)
    if missing:
        page_list = ", ".join(str(index + 1) for index in missing)
        print(f"Incomplete pages: {page_list}. Run the same command again to resume.", file=sys.stderr)
        return 1
    print(f"Processed {len(indexes)} page(s). Results: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
