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

## Commands

```powershell
.\.venv\Scripts\python.exe run_surya.py --start 2 --end 5
.\.venv\Scripts\python.exe export_documents.py
.\.venv\Scripts\python.exe evaluate_output.py --expected 8
```

Page indexes are zero-based and `--end` is inclusive. Omit both page arguments
to process the complete PDF. Partial runs use a page-range suffix in their output
filename so they cannot overwrite a completed full-document extraction.

The Excel question sheets use exactly these columns:

```text
Q.No | Question (English) | Question (हिंदी) | Option A | Option B | Option C | Option D
```

The exporter never edits the Surya JSON. Word equations are generated with free,
open-source local Python converters. When an equation cannot be converted,
its original recognized text is retained instead of being guessed or discarded.

The first run downloads Surya model weights into `.cache` inside this project.
Later runs can use those local weights.
