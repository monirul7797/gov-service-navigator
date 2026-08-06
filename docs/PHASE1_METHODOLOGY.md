# Phase 1 — Data Collection & Curation Methodology

**Project:** Government Service Navigator — a multilingual (Bangla + English) RAG
assistant that helps Bangladeshi citizens navigate four government services:
**NID**, **e-Passport**, **income-tax e-Return**, and **BDRIS** (birth/death
registration).

**Phase 1 goal:** produce a clean, verified, well-categorized knowledge base that
Phase 2 (embedding + vector DB) can index directly — and be able to *prove*, not
just claim, that the data is (a) from official sources, (b) complete across every
service, and (c) free of broken Bangla.

This document describes the rebuilt pipeline (the first version's files were lost
to a hardware failure; this is the cleaner second pass).

---

## 1. Design principles

Three rules drove every decision:

1. **Official sources only.** Every chunk must trace to a `*.gov.bd` URL (or, for
   a small number of policy facts, a reputable outlet reporting a specific
   government circular). This is *enforced in code*, not left to discipline —
   the fetcher refuses any host not on an allow-list.

2. **Completeness must be measurable.** "Did we collect everything?" is answered
   by a machine-checked **service × category coverage matrix**, not by eyeballing.
   If any *required* category for any service has zero chunks, QA fails loudly.

3. **Bangla integrity is a first-class check.** Bengali text silently corrupts in
   a dozen ways (mojibake, dropped base characters, legacy Bijoy encoding). Every
   chunk passes through a dedicated Bangla-health validator before it is accepted.

---

## 2. Project layout

```
gov-service-navigator/
├── config/
│   ├── sources.yaml      # verified source registry + allow-listed hosts
│   └── taxonomy.yaml     # 12 content categories + classifier keywords
├── src/
│   ├── paths.py          # BASE_DIR anchoring, config loaders, path helpers
│   ├── bangla.py         # Bangla normalization + corruption detection
│   ├── fetch.py          # cache-first fetcher; refuses non-gov.bd hosts
│   ├── extract.py        # HTML / PDF / txt -> clean text blocks
│   ├── chunk.py          # pack -> classify -> stamp provenance -> JSONL
│   ├── qa.py             # coverage matrix, gap + duplicate + Bangla report
│   └── pipeline.py       # orchestrator: fetch -> extract -> chunk -> QA
├── seed_corpus.py        # verified starter facts (same pipeline, hand-checked)
├── tests_bangla.py       # unit tests for the Bangla validator
├── data/
│   ├── raw/              # cached fetched pages (+ provenance, sha256)
│   ├── processed/        # intermediate extracted text
│   └── chunks/           # final JSONL, one file per service
└── docs/
    ├── PHASE1_METHODOLOGY.md   # this file
    └── SOURCES.md              # human-readable source registry
```

The established patterns from v1 are carried forward: `BASE_DIR` is anchored via
`os.path.abspath(__file__)` in `paths.py` (kills the path-doubling bug), and every
module keeps its tunables as configurable variables at the top of the file.

---

## 3. Verified source registry (`config/sources.yaml`)

Every service lists official **seed URLs**, each annotated with:

- `render`: how the page must be fetched — `static` (requests + BeautifulSoup),
  `js` (Playwright, for JavaScript single-page apps), or `pdf` (pdfplumber).
- `lang`: the language(s) expected on the page, so the Bangla validator knows
  when to *expect* Bengali and flag its absence.
- `verified_on`: the date a human confirmed the URL was live and official.

| Service | Authority | Primary official domains |
|---|---|---|
| **NID** | Bangladesh Election Commission — NID Wing | `services.nidw.gov.bd`, `ecs.gov.bd` |
| **e-Passport** | Dept. of Immigration & Passports | `epassport.gov.bd`, `dip.gov.bd` |
| **e-Return** | National Board of Revenue (NBR) | `nbr.gov.bd`, `etaxnbr.gov.bd` |
| **BDRIS** | Office of the Registrar General (LGD) | `bdris.gov.bd`, `everify.bdris.gov.bd`, `orgbdr.gov.bd` |

Unofficial third-party helper sites (e.g. `nidservices.net`, `epassbd.com`,
`etaxnbr.com`) were **explicitly excluded** even though they rank high in search
— they are not authoritative and often carry stale fees.

### How "official only" is enforced

`sources.yaml` has an `allowed_hosts` list. `src/fetch.py` calls `host_allowed()`
on every URL and **raises before making the request** if the host is not on that
list. Adding a source is therefore a deliberate, reviewable edit to one file —
there is no code path that silently pulls from a random domain.

---

## 4. Content taxonomy (`config/taxonomy.yaml`)

Every chunk is tagged with exactly one of **12 categories**. Seven are marked
`required` — these are the ones a citizen almost always needs, so QA insists each
service covers all seven.

| Category | Required? | What it captures |
|---|---|---|
| `overview` | ✅ | What the service is / who runs it |
| `eligibility` | ✅ | Who can apply |
| `required_documents` | ✅ | Documents needed |
| `process_steps` | ✅ | Step-by-step application |
| `fees` | ✅ | Fees & payment |
| `timelines` | ✅ | Processing time / deadlines |
| `contacts` | ✅ | Helpline / office / email |
| `corrections` | — | Correction / reissue / update |
| `verification` | — | Status check / verify |
| `fees_exemptions` | — | Exemptions / thresholds |
| `faq` | — | Frequently asked questions |
| `legal` | — | Governing act / rules |

Each category carries `keywords_en` and `keywords_bn` lists that drive the
auto-classifier.

### The classifier (`classify()` in `src/chunk.py`)

A keyword-vote model with two refinements added during this rebuild:

1. **Heading weighting.** A keyword hit in a chunk's *heading* counts as much as
   `HEADING_WEIGHT` (currently 3) body hits. A section titled "Processing time"
   is a far stronger signal than the word "process" buried in a paragraph. This
   alone resolved several misclassifications (e.g. BDRIS "Processing time" was
   being stolen into `process_steps` by the word "application" in its body).

2. **Word-boundary matching for English keywords.** Keywords are matched on word
   boundaries (`\bprocess\b`) so `process` no longer fires on **micro**process**or**,
   and `submit` no longer fires on `submitted`. Bangla keywords stay as substring
   tests (Bengali has no reliable word boundary, and the curated Bangla list is
   already whole-word phrases).

The classifier also returns a **confidence** score (winning category's share of
total votes). Chunks below 0.4 are surfaced in the QA report for human review —
the classifier is not assumed to be perfect; the confidence score plus the review
flag are the safety net.

---

## 5. Fetch → Extract → Chunk

- **Fetch** (`fetch.py`): cache-first. A page is fetched once, stored under
  `data/raw/` with a `provenance.json` and a **sha256** of the exact bytes. Reruns
  read the cache, so the corpus rebuild is deterministic and doesn't re-hammer
  government servers. UTF-8 is enforced on decode.

- **Extract** (`extract.py`): HTML is parsed with BeautifulSoup — navigation,
  scripts, styles, and boilerplate are dropped; headings are preserved and
  attached to the text under them. PDFs go through pdfplumber. Output is a list of
  `Block(text, heading, order)`.

- **Chunk** (`chunk.py`): consecutive blocks under the same heading are greedily
  packed to ~600 characters (900 hard cap), never splitting mid-block, and the
  heading is prefixed so each retrieved chunk is self-describing. Each chunk is
  classified, language-tagged, Bangla-checked, and written as one JSON object per
  line.

### Chunk record shape

```json
{
  "id": "…",                       // stable sha1 of service|url|order
  "service": "bdris",
  "category": "timelines",
  "category_confidence": 0.625,
  "text": "…normalized chunk text…",
  "heading": "Processing time",
  "lang": "en",                    // en | bn | mixed
  "source_url": "https://bdris.gov.bd/br/application",
  "official": true,
  "fetched_at": "…",
  "source_sha256": "…",            // integrity of the source page
  "bangla_ok": true,               // false => flagged for human review
  "bangla_issues": []
}
```

Every chunk therefore carries its own provenance and integrity metadata — Phase 2
retrieval can cite the exact official URL behind any answer.

---

## 6. Bangla font / encoding health (`src/bangla.py`)

This is the module that answers "are the Bangla fonts broken?" It runs on every
chunk and detects:

- **Mojibake** — UTF-8 bytes misread as Latin-1 (the classic `à¦ª...` garble). The
  validator both *flags* it and *attempts repair* by re-decoding latin1→utf8, but
  **only** if that increases the Bengali character count (so it never makes things
  worse).
- **U+FFFD replacement characters** — bytes lost during a bad decode.
- **U+25CC dotted circles** — combining vowel signs that failed to attach to a base
  consonant (a classic rendering break).
- **Orphan vowel signs (matras)** — a dependent sign with no base character before
  it. (A false positive here — the `ী` in `অনুযায়ী`, where the base is the nukta
  compound `য়` — was fixed with a negative-lookbehind so genuine orphans are still
  caught.)
- **Legacy / Bijoy (ASCII) fonts** — a page that should be Bangla but contains
  almost no Bengali codepoints is using a pre-Unicode font and is flagged.

It also **normalizes** text (Unicode NFC + zero-width-character stripping) and does
correct **Bangla numeral handling**: a negative-lookbehind regex
`(?<![০-৯0-9])[০-৯0-9][০-৯0-9,.]*` scans full multi-digit numbers (so `৩,৫০,০০০`
is one number, not five), with helpers to convert between Bengali and ASCII digits.

`tests_bangla.py` covers all of this with five unit tests: healthy text passes,
mojibake is flagged *and repaired*, broken characters are caught, legacy fonts are
caught, and multi-digit numbers are extracted correctly. **All five pass.**

---

## 7. Quality assurance (`src/qa.py`)

Running `python -m src.qa` produces the completeness report and **exits non-zero if
any required category is empty** (so it can gate a CI step). It checks:

1. **Coverage matrix** — chunks per (service × category). Any required cell with
   zero chunks is marked and listed as a gap to re-collect.
2. **Broken-Bangla list** — any chunk with `bangla_ok = false`.
3. **Exact duplicates** — identical chunk text across the corpus.

### Current QA result

```
PHASE 1 QA REPORT — 135 chunks total  (nid 107, epassport 9, etax 9, bdris 10)

service     overview eligibility required_docs process fees timelines corrections … faq …
nid            2         1            20         28    15      1         20      … (all covered)
epassport      1         1            1           1     1      1          -
etax           2         1            1           1     1      1          -
bdris          1         1            1           1     1      1          1

✓ No required-category gaps. Every service covers all required categories.
✓ No broken-Bangla chunks detected.
✓ No exact-duplicate chunks.
```

135 chunks across four services, every required category filled, zero encoding
problems, zero duplicates. The NID service holds 107 chunks built from real,
manually-scraped official content (see `docs/NID_DATA_INTEGRATION.md`); the other
three still run on the verified starter corpus. A handful of short Bangla FAQ chunks
sit below the confidence threshold and are correctly flagged for a human glance —
they are not gaps, just individually ambiguous.

---

## 8. On the starter corpus vs. the live scrapers

Two things produce data, and it's worth being precise about which is which:

- **`src/` pipeline** — the real, runnable scrapers. Point them at the seed URLs in
  `sources.yaml` and they fetch, extract, chunk, and QA live government pages.

- **`seed_corpus.py`** — a **hand-verified starter set** of factual statements, each
  carrying the exact official URL it was confirmed against on its `verified_on`
  date. It runs the *same* normalization / classification / Bangla-health / QA code
  as the live pipeline, so its output is identical in shape and passes the same
  checks. It gives Phase 2 a correct, attributable baseline immediately.

**Why both?** Several official portals are JavaScript single-page apps
(`epassport.gov.bd`, `etaxnbr.gov.bd`, the NID portal) that return an empty shell to
a plain HTTP fetch — they need Playwright, which runs on your machine. One official
domain (`orgbdr.gov.bd`) disallows automated crawling in its robots policy, so its
facts were transcribed by hand rather than scraped. The seed corpus bridges that gap
now; the scrapers fill in the long tail on your own machine.

### Running it on your machine

```bash
# 1. install deps
pip install requests beautifulsoup4 pdfplumber playwright regex pyyaml
python -m playwright install chromium      # for the JS SPA portals

# 2. build the verified starter corpus
python seed_corpus.py

# 3. run the live pipeline (all services, then QA)
python -m src.pipeline --qa
#    or a single service:
python -m src.pipeline nid --qa

# 4. run QA / Bangla tests any time
python -m src.qa
python tests_bangla.py
```

---

## 9. ⚠️ Year-sensitive data

Fees and tax thresholds change with each Finance Act / SRO / local-government
schedule. Every such fact in the corpus is tagged **YEAR-SENSITIVE** in its note
(e.g. the BDT 3,50,000 tax-free threshold, NID correction fees, passport delivery
categories). These must be re-verified against the source URL at the start of each
assessment year — the live scrapers plus a periodic re-verification pass are what
keep them current. Do not treat any fee figure as permanent.

---

## 10. Phase 1 status

- [x] Verified, official-only source registry with in-code host enforcement
- [x] 12-category taxonomy with required-category completeness gating
- [x] Fetch → extract → chunk pipeline with provenance + sha256 on every chunk
- [x] Bangla encoding/font validator (5/5 tests passing)
- [x] QA: coverage matrix, gap detection, duplicate detection, Bangla report
- [x] NID: 107 chunks from real scraped official data — **zero required gaps**, zero broken Bangla
- [x] epassport / etax / bdris: verified starter corpus (awaiting same manual-scrape pass)
- [ ] **Phase 2 →** embed chunks + load into a vector database for retrieval

The `data/chunks/*.jsonl` files are the Phase 1 deliverable and are ready to embed.
