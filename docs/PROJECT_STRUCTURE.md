# Surya OCR — Complete Project Guide

For a detailed architectural and operational comparison with the local MinerU
project, see [MINERU_VS_SURYA.md](MINERU_VS_SURYA.md).

Project location:

```text
H:\Documents\Surya OCR
```

This is an independent project. It does not use the separate `Pdf Extractor`,
`MInerU Pdf Extractor`, or `docling_ocr` projects.

## 1. Purpose

The project accepts scanned PDFs, runs Surya OCR locally, preserves each page as
structured JSON, and generates editable Word and Excel files. It supports page
caching, resume, timeouts, stalled-backend recovery, and adaptive page splitting.

## 2. Beginner terminology

- `PS` in a terminal means PowerShell; it is not a file.
- `.ps1` is a PowerShell script.
- `.py` is a Python source-code file.
- `.json` stores structured data.
- `.txt` stores plain text.
- `.docx` is an editable Word document.
- `.xlsx` is an editable Excel workbook.
- `.md` is a Markdown documentation file.
- `.exe` is a Windows executable program.
- `.venv` is a folder containing this project's private Python and libraries.
- `.cache` stores downloaded model/runtime files for reuse.

A **library** is reusable code. A **model** contains trained recognition
knowledge. A **backend** executes the model. A **pipeline** connects all stages.

## 3. Folder structure

```text
Surya OCR/
|-- code/                         Active production Python code
|   |-- run_surya.py              OCR, cache, resume and recovery
|   |-- surya_lan_server.py       Browser UI, API and queue
|   |-- export_documents.py       Word/Excel export controller
|   |-- document_parser.py        Question parser
|   |-- structured_output.py      HTML and reading-order processing
|   |-- word_math.py              Editable Word equations
|   |-- excel_math.py             Readable Excel math
|   `-- evaluate_output.py        Structural evaluation
|-- scripts/                      Operational PowerShell scripts
|-- tests/                        Automated tests
|-- docs/                         Documentation
|-- input/                        Terminal input PDFs
|-- output/
|   |-- surya/                    Consolidated OCR JSON/text
|   |-- surya_pages/              Resumable page JSON cache
|   `-- documents/                Word and Excel results
|-- runtime/lan_jobs/             Isolated browser jobs
|-- reports/evaluation/           Structural reports
|-- tools/llama.cpp/              Local inference server
|-- .venv/                        Private Python environment
|-- .cache/                       Local models and caches
|-- trash/                        Recoverable inactive material
|-- run_surya.py                  Compatibility launcher
|-- surya_lan_server.py           Compatibility launcher
|-- export_documents.py           Compatibility launcher
|-- evaluate_output.py            Compatibility launcher
|-- start_surya_lan_ui.ps1        Compatibility launcher
|-- requirements.txt              Direct dependencies
`-- README.md                     Quick instructions
```

Root Python files are small compatibility launchers. They add `code/` to
Python's import path and execute the corresponding real implementation. This
keeps old commands working while production code remains organized.

## 4. Complete browser flow

```text
start_surya_lan_ui.ps1
    -> scripts/start_surya_lan_ui.ps1
    -> code/surya_lan_server.py
    -> browser UI on port 8502
    -> POST /api/jobs
    -> runtime/lan_jobs/<job-id>/input
    -> background worker
    -> code/run_surya.py
    -> Surya RecognitionPredictor
    -> local llama.cpp backend
    -> page-level JSON cache
    -> consolidated Surya JSON/text
    -> code/export_documents.py
    -> editable Word + Excel + validation report
```

The browser only uploads, polls status, and downloads results. OCR runs on the
host computer.

## 5. PowerShell launchers

`start_surya_lan_ui.ps1` calls `scripts/start_surya_lan_ui.ps1`. The operational
script locates the project, uses `.venv\Scripts\python.exe`, and starts the LAN
server on port 8502.

`allow_surya_private_firewall.ps1` calls the operational firewall script, which
allows private-network TCP access to port 8502. It does not perform OCR.

## 6. LAN server — `code/surya_lan_server.py`

Responsibilities:

- Serve embedded HTML, CSS, and JavaScript.
- Generate/check the access token.
- Validate PDF signature and 200 MB limit.
- Generate a random job ID.
- Save every upload in an isolated folder.
- Queue one resource-heavy OCR job at a time.
- Run the OCR and exporter as child processes.
- Capture their logs and calculate UI progress.
- Expose only registered result files for download.

Important functions:

- `safe_pdf_name`: sanitizes uploaded filenames.
- `local_ip`: finds the host LAN address.
- `add_log`: stores processing output and updates progress.
- `run_command`: executes a child command and streams its output.
- `public_job`: returns browser-safe job information.
- `worker`: runs OCR, export, and final job-state handling.
- `Handler`: implements UI, upload, status, and download routes.
- `main`: starts the worker and threaded HTTP server.

Routes:

```text
GET  /?token=...                    Browser interface
POST /api/jobs                      Upload PDF
GET  /api/jobs/<id>                 Job status/log
GET  /api/jobs/<id>/files/<name>    Validated download
```

## 7. OCR runner — `code/run_surya.py`

This is the core recognition controller.

### Runtime configuration

| Setting | Purpose |
|---|---|
| `HF_HOME` | Store Hugging Face data under project `.cache` |
| `HF_HUB_CACHE` | Store model snapshots locally |
| `HF_HUB_OFFLINE=1` | Use cached models without internet |
| `TORCH_HOME` | Keep Torch cache in the project |
| `CUDA_VISIBLE_DEVICES=""` | Disable CUDA |
| `TORCH_DEVICE=cpu` | Use CPU |
| `SURYA_INFERENCE_BACKEND=llamacpp` | Select local llama.cpp |
| `LLAMA_CPP_BINARY` | Locate `llama-server.exe` |
| `LLAMA_CPP_NGL=0` | Put zero layers on GPU |
| `SURYA_INFERENCE_PARALLEL=1` | One inference request at a time |
| `SURYA_INFERENCE_CTX_SIZE=16384` | Local model context capacity |

The project does not need Docker. It runs `tools/llama.cpp/llama-server.exe`
locally on Windows.

### Model

The installed package is `surya-ocr==0.22.1`. Code creates:

```python
RecognitionPredictor(SuryaInferenceManager())
```

- `RecognitionPredictor` submits complete page images for structured OCR.
- `SuryaInferenceManager` manages the configured inference backend.
- The llama.cpp backend executes the local model on CPU.
- Model weights/configuration come from the project `.cache`.

The exact checkpoint is selected by Surya's installed settings and is not pinned
by name in project code.

### Functions

- `event`: prints timestamped progress immediately.
- `html_text`: converts recognized HTML to readable plain text.
- `atomic_text`: writes `.tmp`, then renames it to prevent corrupt partial JSON.
- `pdf_fingerprint`: calculates SHA-256 from PDF contents.
- `cache_directory`: gives each PDF/version an isolated cache directory.
- `page_path`: returns names such as `page_0001.json`.
- `valid_cached_page`: validates cached page index and block structure.
- `seed_from_consolidated`: rebuilds page cache from existing consolidated JSON.
- `page_text`: orders blocks and creates readable page text.
- `progress`: displays page count, percentage, elapsed time and ETA.
- `normalized_block_text`: prepares text only for duplicate comparison.
- `deduplicate_overlap_blocks`: removes copies caused by overlapping crops.
- `update_run_status`: records page state/error/time in `run_status.json`.
- `merge_pages`: combines all required page JSON files into final JSON/text.
- `recognize_page`: performs normal full-page or fixed-split OCR.
- `recognize_split_adaptive`: recursively divides only failing page regions.
- `stop_stalled_local_backend`: stops Surya's registered stalled llama server.
- `windows_safe_stop_process`: verifies PID/process name before Windows taskkill.
- `recognize_with_deadline`: runs OCR behind a hard wall-clock watchdog.
- `configure_bounded_inference`: applies backend timeout/retry limits.
- `main`: validates CLI input and controls the entire page-processing lifecycle.

## 8. Page recognition and recovery

Normal page flow:

```text
PDF page
  -> Surya load_pdf
  -> image
  -> predictor([image], full_page=True)
  -> structured blocks
  -> atomic page JSON
```

A block can contain:

```json
{
  "label": "Text",
  "html": "<p>Recognized text</p>",
  "bbox": [100, 200, 900, 260],
  "reading_order": 0
}
```

- `label`: semantic content type.
- `html`: recognized structured content.
- `bbox`: left, top, right, bottom page coordinates.
- `reading_order`: predicted reading sequence.
- `source_page_index`: original zero-based PDF page.

If a full page stalls or fails:

1. Stop/restart only the verified local llama backend.
2. Divide the page into overlapping vertical regions.
3. Recognize each region independently.
4. Recursively halve only a failing region.
5. Translate crop coordinates back to page coordinates.
6. Remove duplicates created by overlap.
7. Save the recovered page normally.

This avoids losing the whole document because one page is difficult.

## 9. Cache and resume

Every successful page is saved immediately under `output/surya_pages`. A PDF
fingerprint prevents another PDF with the same filename from using the wrong
cache. Atomic writes prevent half-written final files.

On restart, valid pages are reused and only missing pages are processed. When
all requested pages exist, `merge_pages` writes:

```text
output/surya/<name>_surya.json
output/surya/<name>_surya.txt
```

The JSON is the preserved source of truth. Exporters do not rewrite it.

## 10. Question parser — `code/document_parser.py`

This module does not run OCR. It reads Surya blocks and creates `Question`
dataclass records.

- `DEVANAGARI`: detects Devanagari Unicode.
- `QUESTION_START`: detects starts such as `1.` or `2)`.
- `OPTION_MARKER`: detects `(A)` through `(D)`.
- `NUMBERED_ITEM`: finds multiple numbered entries inside one block.
- `text_of`: converts HTML to plain text.
- `has_hindi`: detects Hindi-script presence.
- `append_text`: joins content without unnecessary loss.
- `split_options`: separates the stem and MCQ choices.
- `section_kind`: infers question type from printed headings.
- `numbered_items`: splits combined numbered content.
- `descriptive_pair`: pairs Hindi and English descriptive text.
- `parse_questions`: produces the final question records.

This is heuristic classification, separate from OCR character accuracy.

## 11. Structured output — `code/structured_output.py`

Surya may return text, lists, options and math inside HTML. This module converts
HTML into Word-ready logical lines.

- `LogicalLine`: stores text plus its math fragments.
- `MATH_MARKER`: temporarily represents math as `[[MATH:n]]`.
- `_clean_spacing`: normalizes unnecessary spacing.
- `_marked_fragment`: extracts text/math from an HTML node.
- `_remap_markers`: keeps marker numbers valid after combining fragments.
- `_split_options`: places inline choices into logical lines.
- `arrange_text`: separates combined question content.
- `html_logical_lines`: main HTML-to-lines function.

## 12. Exporter — `code/export_documents.py`

The exporter reads `*_surya.json`, calls the parser, and creates Word, Excel and
structural validation outputs.

### Word

`build_word`:

- Creates a Word document and page margins.
- Processes pages and blocks by `reading_order`.
- Skips blocks marked skipped and labels PageHeader/PageFooter/Picture.
- Preserves section headings.
- Converts HTML into logical lines.
- Sends recognized math to `WordMathRenderer`.
- Adds a Word page break for each source PDF page.
- Keeps failed equations as readable source text.

### Excel

`build_excel` creates a cover sheet and section-based question sheets.

MCQ columns:

```text
Q.No | Question (English) | Question (Hindi) |
Option A | Option B | Option C | Option D
```

Short/long columns:

```text
Q.No | Question (English) | Question (Hindi) | Marks | Answer
```

The Answer column is intentionally blank. Short-answer marks (`2`) and
long-answer marks (`5`) are currently hardcoded export decisions.

Other functions style sheets, sanitize names, preserve cover/section text, and
write validation reports for missing language/options/numbers.

## 13. Formula handling

There is no separately configured formula-recognition model. Surya's full-page
recognition must first return math in its structured HTML.

`code/word_math.py` converts:

```text
recognized LaTeX -> MathML -> OMML -> editable Word equation
```

It uses `latex2mathml`, `mathml2omml`, and `lxml`. Failed conversions retain
original text.

`code/excel_math.py` converts math to readable Unicode/plain text because Excel
cells do not support Word equation objects, for example `x^2 -> x²`.

## 14. Evaluation

`code/evaluate_output.py` and export validation can report page/block/question
counts, missing languages, missing question numbers, and incomplete options.

These are structural measurements. True character accuracy requires a manually
corrected reference transcription.

## 15. Libraries

| Package | Purpose |
|---|---|
| `surya-ocr==0.22.1` | Full-page structured recognition APIs |
| `python-docx` | Editable Word generation |
| `openpyxl` | Editable Excel generation |
| `pypdfium2` | PDF inspection/rendering |
| `beautifulsoup4` | Surya HTML parsing |
| `latex2mathml` | LaTeX to MathML |
| `lxml` | XML handling |
| `mathml2omml` | MathML to editable Word equations |

Standard modules include `argparse` for CLI arguments, `pathlib` for paths,
`json` for structured data, `hashlib` for fingerprints, `threading` and `queue`
for background/watchdog work, `subprocess` for local processes, `secrets` for
tokens, and `http.server` for the LAN UI.

## 16. Commands

Start LAN UI:

```powershell
cd "H:\Documents\Surya OCR"
.\start_surya_lan_ui.ps1
```

Run a PDF:

```powershell
.\.venv\Scripts\python.exe .\run_surya.py "H:\path\document.pdf"
```

First five pages (`--end` is inclusive and indexes are zero-based):

```powershell
.\.venv\Scripts\python.exe .\run_surya.py "H:\path\document.pdf" --start 0 --end 4
```

Export one JSON:

```powershell
.\.venv\Scripts\python.exe .\export_documents.py "H:\Documents\Surya OCR\output\surya\document_surya.json"
```

## 17. Accuracy concepts

1. Recognition accuracy: were characters recognized correctly?
2. Layout accuracy: are blocks and reading order correct?
3. Parsing accuracy: were questions, options, sections and languages grouped
   correctly?

These must be measured separately.

## 18. Current strengths

- Local, independent and Docker-free.
- CPU-only processing with local llama.cpp.
- Token-protected LAN UI.
- Coordinate-preserving JSON source of truth.
- Page-level atomic caching and resume.
- Hard timeouts, backend restart and adaptive split recovery.
- Editable Word/Excel output.
- Editable Word equations where recognition/conversion succeeds.
- Structural review reports.

## 19. Current limitations

- CPU recognition can be slow.
- Accuracy depends on scan quality and the Surya model.
- The exact checkpoint name is not pinned in project code.
- Offline mode requires cached model files.
- Split recovery can affect block grouping/reading order.
- Question parsing remains heuristic.
- Short/long marks are hardcoded in Excel export.
- Picture blocks are omitted from Word.
- No dedicated formula-recognition model is configured.
- Editable Word cannot be pixel-identical to fixed PDF coordinates.
- Browser status is held in memory and resets when the server restarts.

## 20. Safe operation

- Use the tokenized URL only on a trusted network.
- Do not expose port 8502 publicly.
- Stop with `Ctrl+C` when finished.
- Do not delete `.cache`, `.venv`, or `tools/llama.cpp` unless rebuilding.
- Preserve `*_surya.json` as the source OCR result.
- Run tests after code changes.
- Use ground-truth samples before claiming an OCR accuracy percentage.

## 21. Technical-review summary

The system uses Surya OCR 0.22.1 through `RecognitionPredictor` and
`SuryaInferenceManager`, with local llama.cpp CPU inference. Each PDF page is
recognized into structured HTML blocks containing coordinates and reading order.
Pages are atomically cached for safe resume. Hard watchdogs restart stalled
backends, while adaptive overlapping-region subdivision recovers difficult
pages. Consolidated JSON remains immutable source data; separate parser and
export modules create editable Word/Excel files and validation reports.
