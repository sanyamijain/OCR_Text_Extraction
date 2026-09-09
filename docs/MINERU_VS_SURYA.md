# MinerU vs Surya OCR

This document compares the two local projects as they are configured on this
computer. The observed speed and accuracy differences are affected by their
models, backends, hardware settings, retries, and export requirements.

## 1. Executive summary

```text
MinerU = several smaller specialized models working in stages
Surya  = a larger vision-language model understanding the complete page
```

MinerU is normally faster and more predictable. Surya is normally slower on the
current CPU configuration but can produce better reading order and contextual
Hindi-English recognition on difficult pages.

## 2. Current local configurations

| Area | MinerU project | Surya OCR project |
|---|---|---|
| Installed version | MinerU 3.4.5 | Surya OCR 0.22.1 |
| Backend | Pipeline | Full-page recognition |
| Device | CPU | CPU through llama.cpp |
| OCR style | Detection followed by recognition | Generative vision-language recognition |
| Main text recognition | PP-OCRv5 Devanagari | Surya full-page model |
| Layout | PP-DocLayoutV2 | Interpreted with page content |
| Formula handling | Dedicated models currently disabled | Inline math in full-page output |
| Table handling | Dedicated models enabled internally | No separate table predictor used here |
| Difficult-page recovery | Normal pipeline | Timeout, retry and adaptive splitting |
| Relative local speed | Faster | Slower |
| Hallucination risk | Lower | Higher because output is generative |

## 3. MinerU processing flow

```text
PDF page
  -> PP-DocLayoutV2 layout detection
  -> text-region detection
  -> PP-OCRv5 Devanagari recognition
  -> optional specialized table/formula processing
  -> reading-order/content assembly
  -> Markdown, JSON and images
```

The current MinerU application uses:

```text
backend: pipeline
method: OCR
language: devanagari
table analysis: enabled
formula recognition: disabled
device: CPU
```

MinerU separates tasks among models. The layout model finds regions, OCR models
find/read text, table models analyze tables, and formula models can process
equations when enabled.

## 4. Why MinerU is faster here

1. Specialized models perform narrower tasks than a full-page VLM.
2. PP-OCRv5 is relatively lightweight and efficient.
3. Formula recognition is disabled with `-f False`.
4. Normal pages are usually processed once without recursive recovery.
5. Traditional OCR does not generate a long HTML response token by token.
6. Specialized detection/recognition operations can be batched efficiently.

MinerU roughly performs:

```text
locate text -> recognize text -> arrange blocks
```

## 5. Surya processing flow

```text
PDF page
  -> render complete page image
  -> RecognitionPredictor
  -> SuryaInferenceManager
  -> local llama.cpp model server
  -> generate structured HTML blocks
  -> return labels, confidence, coordinates and reading order
  -> page cache and consolidated JSON
```

The current Surya project uses:

```text
surya-ocr: 0.22.1
backend: llama.cpp
device: CPU
GPU layers: 0
parallel inference: 1
context size: 16384
mode: full-page recognition
```

## 6. Why Surya takes longer

1. A larger vision-language model interprets the complete page.
2. It runs entirely on CPU; no layers are offloaded to GPU.
3. Structured HTML is generated token by token.
4. Dense bilingual pages require longer outputs.
5. Only one inference request runs at a time.
6. A difficult page may trigger timeout, restart, retry, four-region recovery,
   recursive subdivision, coordinate restoration, and duplicate removal.

One difficult page can therefore require several model calls rather than one.

## 7. Why Surya can appear more accurate

### Complete-page context

Surya sees headings, Hindi text, English translations, options, columns, and
nearby questions together. This can help it understand which blocks belong to
one another.

### Language context

When a Devanagari character is unclear, surrounding words may help the model
select a plausible reading. Traditional line OCR has less page-level context.

### Joint structure and recognition

Surya returns structured HTML and can keep lists, paragraphs and inline math
connected to surrounding content.

### Better visual relationships

Surya can reason about indentation, headings, option placement, bilingual
grouping and multi-column pages in one recognition request.

## 8. Surya accuracy risk

Surya is generative. A readable result is not automatically an exact
transcription. It can potentially:

- normalize spelling or punctuation;
- complete unclear wording from context;
- combine nearby lines;
- omit repetitive content;
- produce a plausible but incorrect character or word.

MinerU's traditional OCR is more directly tied to detected visual text and
normally has lower hallucination risk. Important Surya results should still be
compared with the original page.

## 9. Why MinerU Hindi can be weaker

1. If text detection misses a faint line, recognition never receives it.
2. Small cropped lines contain less context.
3. One question can be fragmented into several blocks.
4. Layout reconstruction can mix Hindi and English columns.
5. Old scans damage Devanagari headlines, matras and joined characters.
6. The efficient Devanagari recognition model is smaller than a contextual VLM.

## 10. Layout comparison

### MinerU

Strengths:

- explicit semantic regions and bounding boxes;
- dedicated table and formula architecture;
- predictable structured JSON;
- lower hallucination risk.

Weaknesses:

- an incorrect initial region affects later OCR;
- bilingual reading order may be incorrect;
- one logical question may become disconnected blocks.

### Surya

Strengths:

- complete-page context;
- often better bilingual and column relationships;
- natural structured HTML;
- inline mathematics can stay with surrounding text.

Weaknesses:

- slower generative inference;
- possible rewriting/hallucination;
- large pages can stall;
- split recovery can affect grouping or reading order.

## 11. Formula comparison

MinerU has dedicated UniMERNet/PP-FormulaNet recognition models, but the current
application disables them with `-f False`. If enabled, formula regions are
detected and recognized separately, increasing time and memory usage.

Surya has no separately configured formula model in this project. Its full-page
model may return inline math as LaTeX inside `<math>` tags. Export code then
converts:

```text
LaTeX -> MathML -> OMML -> editable Word equation
```

Neither system guarantees perfect recognition of complicated chemistry,
handwriting, faint equations, or unusual notation.

## 12. Table comparison

MinerU has dedicated table classification, orientation, bordered-table and
borderless-table models. It is the stronger architectural choice for editable
table extraction. The current MinerU Word exporter nevertheless flattens table
regions because `RECONSTRUCT_TABLES=False`, preventing false question-paper
tables.

The current Surya project does not initialize a separate table-recognition
predictor. Its full-page model may understand table-like content, but the local
exporter does not currently rebuild every detected table as an editable Word
table.

## 13. Selection matrix

| Requirement | Preferred starting choice | Reason |
|---|---|---|
| Many simple PDFs | MinerU | Faster throughput |
| Lower hallucination risk | MinerU | Traditional OCR pipeline |
| Complex bilingual question paper | Surya | Better page context |
| Hindi/English columns are mixed | Surya | Better relationship understanding |
| Editable tables | MinerU | Dedicated table models |
| Formula-heavy document | MinerU with formulas enabled | Dedicated formula models |
| Inline equations within prose | Surya | Math returned with surrounding HTML |
| Limited CPU resources | MinerU | Smaller specialized models |
| Strict exact transcription | MinerU plus validation | Less generative rewriting |
| Difficult visual arrangement | Surya plus review | Stronger full-page reasoning |

## 14. When to choose MinerU

Choose MinerU when priority is:

- speed and processing volume;
- predictable processing time;
- lower RAM/CPU cost;
- lower hallucination risk;
- dedicated tables or formulas;
- reliable structured JSON;
- reasonably clear printed documents.

MinerU is the stronger default production engine for bulk processing.

## 15. When to choose Surya

Choose Surya when priority is:

- difficult Hindi-English pages;
- multi-column or visually complicated layouts;
- stronger contextual reading order;
- maintaining headings/questions/options as related content;
- inline mathematical context;
- accepting longer runtime and manual review.

Surya is the stronger accuracy/recovery engine for difficult pages.

## 16. Why results are configuration-dependent

It is not universally true that MinerU is always faster or Surya is always more
accurate. The local comparison currently means:

```text
MinerU:
CPU + pipeline + specialized models + formula disabled + normal single pass

Surya:
CPU + full-page VLM + llama.cpp + one request at a time + retries/splitting
```

A supported GPU could make Surya considerably faster. Enabling expensive
MinerU formula/table features or using a large MinerU VLM backend could make
MinerU slower.

## 17. Recommended combined architecture

For the current Hindi-English question-paper requirement:

```text
PDF
  -> MinerU first pass
  -> measure page completeness
       - text-block count
       - Devanagari presence
       - suspiciously short output
       - broken question boundaries
       - mixed reading order
  -> good page: retain MinerU
  -> weak page: run Surya recovery
  -> preserve both raw results
  -> select/merge only evidence-supported blocks
  -> generate Word/Excel
  -> flag uncertain pages for review
```

This combines MinerU speed with Surya's contextual capability while limiting
slow inference and hallucination exposure.

## 18. Final recommendation

Use **MinerU as the primary production engine** for speed, consistency,
structured extraction and lower generative risk.

Use **Surya as a selective recovery engine** when Hindi is weak, questions are
missing, columns are mixed, or reading order is incorrect.

If only one engine can be used:

- choose MinerU for volume, speed, tables and strict transcription;
- choose Surya for fewer difficult bilingual documents where contextual layout
  quality is more important than processing time.
