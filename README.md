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

## Commands

```powershell
.\.venv\Scripts\python.exe run_surya.py --start 2 --end 5
.\.venv\Scripts\python.exe export_documents.py
.\.venv\Scripts\python.exe evaluate_output.py --expected 8
```

Page indexes are zero-based and `--end` is inclusive. Omit both page arguments
to process the complete PDF.

The first run downloads Surya model weights into `.cache` inside this project.
Later runs can use those local weights.
