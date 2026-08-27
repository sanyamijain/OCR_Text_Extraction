# Surya OCR Exam Extractor

Isolated local experiment for extracting scanned, multilingual exam PDFs with
Surya OCR. It does not import from or write to the existing `Pdf Extractor` or
`MInerU Pdf Extractor` projects.

## Design

- Surya full-page OCR with layout blocks and reading order
- Raw coordinate-preserving JSON
- Plain UTF-8 text for inspection
- Reviewable Word and Excel exports
- Project-local virtual environment and model caches on H
- Project-local llama.cpp CPU server on H (no Docker required)
- The Surya recognition configuration remains unchanged for accuracy
- Native editable Microsoft Word equations for detected LaTeX math
- Section-wise Excel sheets with exactly seven requested columns
- Original coordinate-preserving JSON remains the source of truth
- Elapsed OCR status every 30 seconds without changing inference batching
- Exact per-page progress, atomic page cache, safe resume, and isolated failures

## Commands

```powershell
.\.venv\Scripts\python.exe run_surya.py --start 2 --end 5
.\.venv\Scripts\python.exe export_documents.py
.\.venv\Scripts\python.exe evaluate_output.py --expected 8
```

Page indexes are zero-based and `--end` is inclusive. Omit both page arguments
to process the complete PDF. Partial runs use a page-range suffix in their output
filename so they cannot overwrite a completed full-document extraction.

OCR pages are saved under `output/surya_pages` immediately. Running the same
command resumes from the first missing page. The default request policy allows
four minutes plus one backend retry, so one difficult page cannot hold the entire
book indefinitely. A failed page is recorded and later pages continue. Retry a
specific PDF page (example: displayed page 24, zero-based index 23) with:

```powershell
.\.venv\Scripts\python.exe run_surya.py ".\input\book.pdf" --start 23 --end 23 --request-timeout 600 --inference-retries 2
```

Full-page output is capped at 4096 tokens and supervised by a five-minute hard
watchdog. A failed or stalled page automatically switches to four overlapping
regions. Each recovery region has its own 1024-token cap and three-minute
watchdog; only a failing region is recursively subdivided. Successful regions
are merged into the original page coordinate system and de-duplicated. Use
`--split-pages 24` (comma-separated, one-based page numbers) only to send a
known difficult page directly to adaptive recovery. Detailed timestamped
heartbeats report loading, inference, backend restarts, subdivision, validation,
and atomic cache writes. Guided layout grammar is disabled because the local
llama.cpp build rejects it on some fallback pages; Surya's standard JSON parser
continues to validate the unguided layout response.

The Excel question sheets use exactly these columns:

```text
MCQ: Q.No | Question (English) | Question (हिंदी) | Option A | Option B | Option C | Option D
Short/Long: Q.No | Question (English) | Question (हिंदी) | Marks | Answer
```

Each workbook includes a bilingual `Cover Page` and separate styled sheets for
MCQ, short-answer, and long-answer sections. The `Answer` cells are intentionally
blank for later entry.

The exporter never edits the Surya JSON. Word equations are generated with free,
open-source local Python converters. When an equation cannot be converted,
its original recognized text is retained instead of being guessed or discarded.

The first run downloads Surya model weights into `.cache` inside this project.
Later runs can use those local weights.
