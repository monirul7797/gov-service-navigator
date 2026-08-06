# NID Data Integration — Manual Scrape → JSONL

This documents how the manually-scraped NID content (24 source files) became the
107-chunk NID knowledge base, and records the engineering decisions made along the
way — including an honest assessment of the third-party `Gemini_Recommendations.txt`.

## Why manual scraping

The NID portal (`services.nidw.gov.bd`) is a JavaScript app that returns an empty
shell to a plain HTTP fetch, and several of its documents sit behind flows the
automated scrapers can't drive. So the citizen-facing NID content was collected by
hand into 24 text files (FAQs, fee notices, Form-2/Form-6 guidelines, the special-area
protocol, portal/wizard screens, fee-calculator outputs). This pass turns those into
the same provenance-stamped JSONL the rest of the pipeline produces.

## How it was built

`build_nid_corpus.py` does the work. The important properties:

- **Byte-perfect Bangla.** The builder reads the source files from disk
  (`data/raw/nid_manual/`) and slices them — nothing is retyped, so no transcription
  corruption is introduced. Every source file separates logical units with a blank
  line, so a blank-line split yields one clean chunk per FAQ Q&A, per fee item, per
  process section. The first line of each block becomes the chunk heading (which also
  feeds the keyword classifier).
- **Same pipeline as everything else.** Each chunk runs through `normalize_text`
  (Unicode NFC + zero-width strip), the taxonomy `classify()` keyword vote, and the
  `check_bangla()` health validator. Output shape is identical to scraped output, so
  it passes the same `src/qa.py`.
- **Real provenance.** `source_url` is the official gov.bd page each file came from;
  `source_sha256` is the real SHA-256 of the originating source file, so every chunk
  is traceable back to the exact file it was extracted from.

Run it with:

```bash
python build_nid_corpus.py     # rebuilds data/chunks/nid.jsonl
python -m src.qa nid           # coverage + Bangla + duplicate report
```

## Result

**107 NID chunks.** Category coverage (every *required* category filled):

| Category | Chunks |
|---|---|
| process_steps | 28 |
| required_documents | 20 |
| corrections | 20 |
| fees | 15 |
| faq | 14 |
| verification | 4 |
| overview | 2 |
| contacts | 2 |
| eligibility | 1 |
| timelines | 1 |

Language mix: **71 Bangla, 24 English, 12 mixed.** Median chunk length 244
characters — a good size for retrieval.

### Bangla font/encoding health

This was the headline check: of the 83 Bangla/mixed chunks drawn from real government
text, **zero were flagged** by the validator — no mojibake, no dropped base characters
(dotted circles), no orphan matras, no legacy-font pages. The manually-scraped NID
content is clean UTF-8 throughout. Numerals like `৩,৫০,০০০` and `১৭` are handled
correctly by the multi-digit scanner.

### Classifier notes

The keyword classifier tags each chunk and reports a confidence. During this pass one
systematic miss surfaced and was fixed at the taxonomy level (a better fix than a
one-off override): the new-voter **qualifications** block was landing in `legal`
because its heading cites "Article 122 of the Constitution / Electoral Rolls Act",
whose legal keywords are heading-weighted. Since the block's actual content is
eligibility criteria (citizen, 18 years, sound mind, resident), the `eligibility`
keyword set was enriched with specific, low-collision terms (`qualification(s)`,
`citizen of bangladesh`, `years of age`, `unsound mind`, `electoral area`) so it wins.

About 18 short Bangla FAQ chunks sit below the 0.34 confidence line. That is expected —
a one-sentence Q&A gives the keyword voter little to work with — and they are surfaced
by QA for a human glance rather than silently mis-filed. They are not coverage gaps.

## Decision: the address hierarchy is a lookup table, not RAG chunks

File 23 and the `gemini-code-*.json` are the Division → District → Upazila dropdown
data (8 divisions, 64 districts, 495+ upazilas). This is **structured lookup data, not
knowledge**. Embedding hundreds of near-identical place-name rows would add noise to the
vector store and waste retrieval slots without answering real citizen questions. So:

- the raw hierarchy is saved as a structured asset at
  `data/reference/bd_admin_hierarchy.json`, and
- the corpus keeps a single *process* chunk explaining the actual knowledge — that
  claiming an account requires the selected address to exactly match NID records.

For the full 495-upazila layer, a maintained open geocode dataset (see the note below)
is a better source than either hand-typing or embedding it.

## Assessment of `Gemini_Recommendations.txt`

The file contains third-party (Gemini) advice. Most of it is about **Phase 2**
(embedding/retrieval), not Phase-1 chunk building, but it's worth recording a
considered take rather than adopting it wholesale:

- **"Don't hand-type the Upazila layer; use an open `bangladesh-geocode` JSON."**
  Sound — and adopted in spirit. The administrative hierarchy is kept as a structured
  reference asset, and pulling the complete union/upazila set from a maintained
  open dataset (verified against an official list before use) beats maintaining it by
  hand. Treat any third-party geo repo as *unofficial* and reconcile it with EC/official
  data before trusting it.

- **"Vanilla BanglaBERT is the wrong tool for embeddings; use a sentence-transformer
  like BGE-M3 / multilingual-E5 / a fine-tuned Bangla SBERT."** Agreed, and correct.
  BanglaBERT is an encoder trained for understanding tasks, not tuned for semantic
  similarity, so out of the box it clusters poorly for retrieval. BGE-M3 and
  multilingual-E5 are genuinely strong multilingual/cross-lingual retrievers and handle
  the Banglish-query → Bangla-chunk case well. This is a good Phase-2 default — but
  benchmark two or three on a small labelled set of real citizen questions before
  committing, rather than taking any single recommendation on faith.

- **"Concatenate each chunk's content with sample queries before generating vectors."**
  The underlying idea — augmenting a chunk's embedding with representative questions to
  close the query↔document gap — is legitimate (it's the family of query-augmented /
  hypothetical-question indexing tricks). Two cautions, though: (1) it references a
  `retrieval_tuning.sample_queries` field that doesn't exist in our schema, so it's not
  a drop-in; and (2) do it at **index time as a separate embedding-augmentation field**,
  not by editing the stored `text` — otherwise the text you cite back to the citizen
  gets polluted with synthetic questions. Worth trying in Phase 2, measured against a
  plain baseline; not part of Phase 1.

Net: the geo and embedding-model points are good and are reflected above; the
query-augmentation point is a reasonable Phase-2 experiment with implementation caveats.
None of it changes the Phase-1 JSONL, which stays clean, source-faithful, and
independently verifiable.

## What's next

- Embed `data/chunks/nid.jsonl` (Phase 2) with a multilingual sentence-transformer.
- Give epassport / etax / bdris the same manual-scrape → JSONL treatment so they move
  off the starter corpus onto full real content.
