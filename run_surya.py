from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import re
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
from surya.common.blank import is_blank_region
from surya.inference import SuryaInferenceManager
from surya.inference.backends import llamacpp as llamacpp_backend
from surya.inference.backends import openai_client
from surya.inference.backends import spawn as spawn_backend
from surya.input.load import load_pdf
from surya.recognition import RecognitionPredictor
from surya.settings import settings

INPUT = ROOT / "input"
OUTPUT = ROOT / "output" / "surya"
PAGE_OUTPUT = ROOT / "output" / "surya_pages"


class PageStalledError(TimeoutError):
    pass


def event(message: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {message}", flush=True)


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


def normalized_block_text(block: dict) -> str:
    """Canonical text used only to remove overlap copies; OCR text is not rewritten."""
    return re.sub(r"\s+", " ", html_text(block.get("html", ""))).strip().casefold()


def deduplicate_overlap_blocks(blocks: list[dict], overlap: int) -> list[dict]:
    blocks.sort(key=lambda block: (block.get("bbox", [0, 0])[1], block.get("bbox", [0, 0])[0]))
    deduplicated: list[dict] = []
    seen: dict[str, list[float]] = {}
    for block in blocks:
        key = normalized_block_text(block)
        y = float(block.get("bbox", [0, 0])[1])
        previous = seen.setdefault(key, [])
        if key and any(abs(y - old_y) <= overlap * 2 for old_y in previous):
            continue
        previous.append(y)
        block["reading_order"] = len(deduplicated)
        deduplicated.append(block)
    return deduplicated


def update_run_status(cache_dir: Path, page_number: int, status: str, detail: str = "") -> None:
    path = cache_dir / "run_status.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"pages": {}}
    except (OSError, json.JSONDecodeError):
        state = {"pages": {}}
    state.setdefault("pages", {})[str(page_number)] = {
        "status": status,
        "detail": detail,
        "updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    atomic_text(path, json.dumps(state, ensure_ascii=False, indent=2))


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


def recognize_page(predictor, pdf: Path, page_index: int, split: bool, split_parts: int = 3) -> dict:
    event(f"Page {page_index + 1}: loading PDF image")
    images, _ = load_pdf(str(pdf), page_range=[page_index])
    image = images[0]
    event(f"Page {page_index + 1}: image loaded ({image.size[0]}x{image.size[1]})")
    if not split:
        event(f"Page {page_index + 1}: sending full page to OCR backend")
        results = predictor([image], full_page=True)
        if len(results) != 1:
            raise RuntimeError(f"Expected one page result, received {len(results)}")
        data = results[0].model_dump()
        data["source_page_index"] = page_index
        event(f"Page {page_index + 1}: full-page response received ({len(data.get('blocks', []))} blocks)")
        return data

    width, height = image.size
    overlap = min(48, height // 20)
    boundaries = [round(height * part / split_parts) for part in range(split_parts + 1)]
    regions = [
        (max(0, boundaries[part] - (overlap if part else 0)),
         min(height, boundaries[part + 1] + (overlap if part + 1 < split_parts else 0)))
        for part in range(split_parts)
    ]
    merged_blocks: list[dict] = []
    event(f"Page {page_index + 1}: split recovery prepared ({split_parts} overlapping regions)")
    for region_number, (top, bottom) in enumerate(regions, 1):
        event(
            f"Page {page_index + 1}: split region {region_number}/{split_parts} "
            f"started (y={top}:{bottom})"
        )
        crop = image.crop((0, top, width, bottom))
        results = predictor([crop], full_page=True)
        if len(results) != 1:
            raise RuntimeError(f"Expected one split-page result, received {len(results)}")
        crop_blocks = results[0].model_dump().get("blocks", [])
        event(
            f"Page {page_index + 1}: split region {region_number}/{split_parts} "
            f"received ({len(crop_blocks)} blocks)"
        )
        if not crop_blocks and not is_blank_region(crop):
            raise RuntimeError(f"Surya returned no OCR blocks for non-blank split region {top}:{bottom}")
        for block in crop_blocks:
            for point in block.get("polygon", []):
                point[1] += top
            bbox = block.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                bbox[1] += top
                bbox[3] += top
            merged_blocks.append(block)

    deduplicated = deduplicate_overlap_blocks(merged_blocks, overlap)
    event(
        f"Page {page_index + 1}: split regions merged; "
        f"{len(merged_blocks) - len(deduplicated)} overlap duplicate(s) removed"
    )
    return {
        "blocks": deduplicated,
        "image_bbox": [0, 0, float(width), float(height)],
        "source_page_index": page_index,
    }


def recognize_split_adaptive(
    predictor,
    pdf: Path,
    page_index: int,
    split_parts: int,
    region_timeout: int,
    region_tokens: int,
    max_depth: int,
) -> tuple[dict, object]:
    """OCR bounded regions; recursively halve only a region that fails or stalls."""
    event(f"Page {page_index + 1}: loading image for adaptive split recovery")
    images, _ = load_pdf(str(pdf), page_range=[page_index])
    image = images[0]
    width, height = image.size
    overlap = min(48, height // 20)
    boundaries = [round(height * part / split_parts) for part in range(split_parts + 1)]
    base_regions = [
        (
            max(0, boundaries[part] - (overlap if part else 0)),
            min(height, boundaries[part + 1] + (overlap if part + 1 < split_parts else 0)),
        )
        for part in range(split_parts)
    ]
    session = [predictor]
    collected: list[dict] = []

    def infer_region(top: int, bottom: int, label: str, depth: int) -> None:
        crop = image.crop((0, top, width, bottom))
        event(
            f"Page {page_index + 1}: region {label} started | y={top}:{bottom} | "
            f"depth {depth}/{max_depth} | token cap {region_tokens} | timeout {region_timeout}s"
        )
        result_queue: queue.Queue = queue.Queue(maxsize=1)
        previous_tokens = settings.SURYA_MAX_TOKENS_FULL_PAGE
        settings.SURYA_MAX_TOKENS_FULL_PAGE = region_tokens

        def worker() -> None:
            try:
                result_queue.put((True, session[0]([crop], full_page=True)))
            except BaseException as error:
                result_queue.put((False, error))

        thread = threading.Thread(
            target=worker,
            name=f"surya-page-{page_index + 1}-region-{label}",
            daemon=True,
        )
        started = time.monotonic()
        thread.start()
        try:
            while thread.is_alive() and time.monotonic() - started < region_timeout:
                thread.join(min(30.0, region_timeout - (time.monotonic() - started)))
                if thread.is_alive():
                    elapsed = int(time.monotonic() - started)
                    event(
                        f"Page {page_index + 1}: region {label} active | "
                        f"elapsed {elapsed // 60:02d}:{elapsed % 60:02d} | "
                        f"remaining {max(0, region_timeout - elapsed)}s"
                    )
            if thread.is_alive():
                event(f"Page {page_index + 1}: region {label} timed out; restarting backend")
                stop_stalled_local_backend()
                thread.join(15)
                session[0] = RecognitionPredictor(SuryaInferenceManager())
                raise PageStalledError(f"region {label} exceeded {region_timeout}s")
            succeeded, value = result_queue.get_nowait()
            if not succeeded:
                raise value
            if len(value) != 1:
                raise RuntimeError(f"region {label}: expected one result, received {len(value)}")
            blocks = value[0].model_dump().get("blocks", [])
            if not blocks and not is_blank_region(crop):
                raise RuntimeError(f"region {label}: no blocks returned for non-blank image")
        except KeyboardInterrupt:
            raise
        except BaseException as error:
            if depth >= max_depth or bottom - top < 160:
                event(f"Page {page_index + 1}: region {label} failed permanently: {error}")
                raise RuntimeError(f"region {label} failed after adaptive splitting: {error}") from error
            event(f"Page {page_index + 1}: region {label} failed ({error}); subdividing automatically")
            # A fresh lazy client attaches to a healthy server or spawns one if
            # the prior connection disappeared. Model files remain cached.
            session[0] = RecognitionPredictor(SuryaInferenceManager())
            midpoint = (top + bottom) // 2
            infer_region(top, min(bottom, midpoint + overlap), f"{label}.1", depth + 1)
            infer_region(max(top, midpoint - overlap), bottom, f"{label}.2", depth + 1)
            return
        finally:
            settings.SURYA_MAX_TOKENS_FULL_PAGE = previous_tokens

        for block in blocks:
            for point in block.get("polygon", []):
                point[1] += top
            bbox = block.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                bbox[1] += top
                bbox[3] += top
            collected.append(block)
        event(f"Page {page_index + 1}: region {label} completed ({len(blocks)} blocks)")

    event(
        f"Page {page_index + 1}: adaptive recovery prepared | {split_parts} base regions | "
        f"up to {max_depth} subdivision level(s)"
    )
    for region_number, (top, bottom) in enumerate(base_regions, 1):
        infer_region(top, bottom, str(region_number), 0)
    deduplicated = deduplicate_overlap_blocks(collected, overlap)
    event(
        f"Page {page_index + 1}: adaptive regions merged | {len(collected)} blocks | "
        f"{len(collected) - len(deduplicated)} duplicate(s) removed"
    )
    return (
        {
            "blocks": deduplicated,
            "image_bbox": [0, 0, float(width), float(height)],
            "source_page_index": page_index,
        },
        session[0],
    )


def stop_stalled_local_backend() -> None:
    """Terminate only Surya's registered llama.cpp server after a hard timeout."""
    sentinel = spawn_backend._read_sentinel("llamacpp")
    if not sentinel or not sentinel.get("pid"):
        event("Backend watchdog: no registered llama.cpp PID was found")
        return
    event(f"Backend watchdog: stopping stalled llama.cpp PID {sentinel['pid']}")
    spawn_backend._stop_process(int(sentinel["pid"]), "llamacpp")
    spawn_backend._delete_sentinel("llamacpp")
    event("Backend watchdog: old backend stopped; replacement will start on the next attempt")


def windows_safe_stop_process(pid: int, name: str) -> None:
    """Stop a verified llama.cpp process tree on Windows.

    Surya 0.22.1 calls os.kill with Unix signals, which produces WinError 87
    on this Windows host. PID verification prevents terminating an unrelated
    process if a stale sentinel's PID has been reused.
    """
    if os.name != "nt":
        return spawn_backend._original_stop_process(pid, name)
    listing = subprocess.run(
        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if str(pid) not in listing.stdout:
        return
    first_field = listing.stdout.strip().split(",", 1)[0].strip('"').lower()
    if first_field != "llama-server.exe":
        raise RuntimeError(f"Refusing to stop PID {pid}: expected llama-server.exe, found {first_field!r}")
    stopped = subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if stopped.returncode != 0 and "not found" not in stopped.stderr.lower():
        raise RuntimeError(f"Failed to stop llama.cpp PID {pid}: {stopped.stderr.strip()}")


def recognize_with_deadline(
    predictor, pdf: Path, page_index: int, split: bool, split_parts: int, timeout_seconds: int
) -> dict:
    """Run page recognition behind a hard wall-clock watchdog."""
    result_queue: queue.Queue = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put((True, recognize_page(predictor, pdf, page_index, split, split_parts)))
        except BaseException as error:
            result_queue.put((False, error))

    thread = threading.Thread(target=worker, name=f"surya-page-{page_index + 1}", daemon=True)
    thread.start()
    started = time.monotonic()
    mode = "split" if split else "full"
    event(f"Page {page_index + 1}: {mode} OCR watchdog started ({timeout_seconds}s limit)")
    while thread.is_alive():
        elapsed = time.monotonic() - started
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            break
        thread.join(min(30.0, remaining))
        if thread.is_alive():
            event(
                f"Page {page_index + 1}: {mode} OCR active | "
                f"elapsed {int(elapsed + min(30.0, remaining)) // 60:02d}:"
                f"{int(elapsed + min(30.0, remaining)) % 60:02d} | "
                f"watchdog remaining {max(0, int(remaining - min(30.0, remaining)))}s"
            )
    if thread.is_alive():
        event(f"Page {page_index + 1}: {mode} OCR declared stuck by hard watchdog")
        stop_stalled_local_backend()
        thread.join(15)
        raise PageStalledError(
            f"hard page timeout after {timeout_seconds}s; local backend restarted for recovery"
        )
    succeeded, value = result_queue.get_nowait()
    if succeeded:
        event(f"Page {page_index + 1}: {mode} OCR worker completed")
        return value
    raise value


def configure_bounded_inference(timeout_seconds: int, retries: int) -> None:
    """Limit one page's wait without changing prompts, model, images, or decoding."""
    settings.SURYA_INFERENCE_TIMEOUT_SECONDS = float(timeout_seconds)
    # Current llama.cpp rejects Surya's generated layout grammar on some
    # fallback pages. Unguided layout still uses the same model and prompt,
    # then Surya's normal parser validates the returned JSON.
    settings.SURYA_GUIDED_LAYOUT = False
    if not hasattr(spawn_backend, "_original_stop_process"):
        spawn_backend._original_stop_process = spawn_backend._stop_process
    if os.name == "nt":
        spawn_backend._stop_process = windows_safe_stop_process
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
        "--page-timeout",
        type=int,
        default=300,
        help="Hard wall-clock seconds before a stuck page is stopped and split (default: 300)",
    )
    parser.add_argument(
        "--inference-retries",
        type=int,
        default=0,
        help="Hidden backend retries; adaptive recovery handles failures (default: 0)",
    )
    parser.add_argument(
        "--max-page-tokens",
        type=int,
        default=4096,
        help="Full-page generation cap; prevents long repetition loops (default: 4096)",
    )
    parser.add_argument(
        "--split-pages",
        default="",
        help="Comma-separated one-based PDF pages to OCR in split mode immediately",
    )
    parser.add_argument(
        "--split-parts",
        type=int,
        default=4,
        help="Initial overlapping regions used by automatic recovery (default: 4)",
    )
    parser.add_argument(
        "--region-timeout",
        type=int,
        default=180,
        help="Hard wall-clock limit for each recovery region (default: 180 seconds)",
    )
    parser.add_argument(
        "--region-tokens",
        type=int,
        default=1024,
        help="Maximum tokens for each smaller recovery region (default: 1024)",
    )
    parser.add_argument(
        "--split-depth",
        type=int,
        default=2,
        help="How many times a failed region may be halved (default: 2)",
    )
    parser.add_argument("--status", action="store_true", help="Show resumable cache status without running OCR")
    args = parser.parse_args()
    if args.request_timeout < 30:
        parser.error("--request-timeout must be at least 30 seconds")
    if args.page_timeout < 60:
        parser.error("--page-timeout must be at least 60 seconds")
    if args.region_timeout < 60:
        parser.error("--region-timeout must be at least 60 seconds")
    if args.inference_retries < 0 or args.page_retries < 0:
        parser.error("retry counts cannot be negative")
    if args.max_page_tokens < 1024:
        parser.error("--max-page-tokens must be at least 1024")
    if not 512 <= args.region_tokens <= args.max_page_tokens:
        parser.error("--region-tokens must be between 512 and --max-page-tokens")
    if not 0 <= args.split_depth <= 3:
        parser.error("--split-depth must be between 0 and 3")
    if not 2 <= args.split_parts <= 6:
        parser.error("--split-parts must be between 2 and 6")
    try:
        split_pages = {int(value.strip()) for value in args.split_pages.split(",") if value.strip()}
    except ValueError:
        parser.error("--split-pages must contain comma-separated page numbers")
    if any(page < 1 for page in split_pages):
        parser.error("--split-pages values must be one-based positive page numbers")
    configure_bounded_inference(args.request_timeout, args.inference_retries)
    settings.SURYA_MAX_TOKENS_FULL_PAGE = args.max_page_tokens

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
        status_path = cache_dir / "run_status.json"
        if status_path.exists():
            print(status_path.read_text(encoding="utf-8"))
        return 0

    predictor = None
    durations: list[float] = []
    run_started = time.monotonic()
    completed = 0
    for ordinal, page_index in enumerate(indexes, 1):
        if page_index not in pending:
            completed += 1
            update_run_status(cache_dir, page_index + 1, "cached")
            progress(completed, len(indexes), ordinal, time.monotonic() - run_started, durations, "cached")
            continue
        if predictor is None:
            predictor = RecognitionPredictor(SuryaInferenceManager())
        success = False
        page_started = time.monotonic()
        force_split = page_index + 1 in split_pages
        modes = ["split"] if force_split else ["full", "split"]
        final_error = ""
        result_mode = ""
        for mode in modes:
            if mode == "split" and not force_split:
                update_run_status(cache_dir, page_index + 1, "split", "full-page attempts failed; trying split recovery")
                print(f"Page {page_index + 1}: full-page OCR failed or stalled; automatic split recovery started.", flush=True)
            mode_attempts = 1 if mode == "split" else args.page_retries + 1
            for attempt in range(mode_attempts):
                status_name = "split" if mode == "split" else ("retried" if attempt else "processing")
                update_run_status(cache_dir, page_index + 1, status_name, f"attempt {attempt + 1}")
                event(
                    f"Page {page_index + 1}: {mode} attempt {attempt + 1}/{mode_attempts} starting"
                )
                try:
                    if mode == "split":
                        data, predictor = recognize_split_adaptive(
                            predictor,
                            pdf,
                            page_index,
                            args.split_parts,
                            args.region_timeout,
                            args.region_tokens,
                            args.split_depth,
                        )
                    else:
                        data = recognize_with_deadline(
                            predictor,
                            pdf,
                            page_index,
                            False,
                            args.split_parts,
                            args.page_timeout,
                        )
                    if not isinstance(data.get("blocks"), list) or not data["blocks"]:
                        raise RuntimeError("Surya returned no OCR blocks")
                    atomic_text(page_path(cache_dir, page_index), json.dumps(data, ensure_ascii=False, indent=2))
                    event(
                        f"Page {page_index + 1}: validation passed; "
                        f"{len(data['blocks'])} blocks written atomically to cache"
                    )
                    error_file = cache_dir / f"page_{page_index + 1:04d}.error.json"
                    if error_file.exists():
                        error_file.unlink()
                    success = True
                    result_mode = mode
                    break
                except KeyboardInterrupt:
                    update_run_status(cache_dir, page_index + 1, "interrupted", "safe to resume")
                    print(f"\nStopped safely. Pages already written to {cache_dir}", file=sys.stderr)
                    return 130
                except Exception as error:
                    final_error = str(error)
                    if isinstance(error, PageStalledError) or mode == "full":
                        predictor = None
                    update_run_status(cache_dir, page_index + 1, "retried", final_error)
                    print(
                        f"Page {page_index + 1} {mode} attempt {attempt + 1} failed: {error}",
                        file=sys.stderr,
                    )
                    if predictor is None and (attempt + 1 < mode_attempts or mode != modes[-1]):
                        event(f"Page {page_index + 1}: constructing a fresh OCR backend client")
                        predictor = RecognitionPredictor(SuryaInferenceManager())
            if success:
                break
        duration = time.monotonic() - page_started
        durations.append(duration)
        if success:
            completed += 1
            final_status = "split" if result_mode == "split" else "processed"
            update_run_status(cache_dir, page_index + 1, final_status, f"saved in {duration:.1f}s")
            status = f"{final_status}; saved ({duration:.1f}s)"
        else:
            atomic_text(
                cache_dir / f"page_{page_index + 1:04d}.error.json",
                json.dumps({"page": page_index + 1, "error": final_error}, indent=2),
            )
            update_run_status(cache_dir, page_index + 1, "failed", final_error)
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
