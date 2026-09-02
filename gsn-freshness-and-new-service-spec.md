# GSN Build Spec: Freshness Pipeline + New-Service Extraction

**For:** GLM 5.3 Flash, executing autonomously inside zcode
**Repo:** gov-service-navigator (bilingual RAG chatbot, 4 BD gov services live)
**Author of this spec:** planning session with Claude, prior to handoff

## How to use this document
Drop this file in the repo root. Tell the coding agent to read it fully before
touching anything, then execute **Part A first, Part B second**, in the phase
order given in Part D. Do not skip the Context Primer — the agent has no
memory of prior sessions and will make convention mistakes (invented URLs,
wrong VAT handling, schema drift) without it.

---

## 0. Context Primer — read before writing any code

**What exists today:** a Streamlit + CLI RAG chatbot (`answer.py` /
`handle_turn()` shared orchestration) over 4 ChromaDB collections — `nid`
(102 records), `epassport` (66), `ereturn` (83), `bdris` (42) — using
BAAI/bge-m3 embeddings. Routing is deterministic (`disambiguate.py` +
`concept_registry.json`), not LLM-judgment, by deliberate design: an earlier
LLM-based clarifier looped and invented options. **Do not replace
deterministic routing with LLM judgment anywhere in this build.**

**Non-negotiable conventions carried over from the existing 4 services —
apply these to every new record you create or modify:**

1. **Never invent a `source_url`, fee, or date.** If you can't verify a fact
   against fetched/screenshotted source content, leave a `# TODO(verify):`
   comment and skip the record rather than guess. This project has twice
   caught guessed URLs (a wrong `notices/161` that was actually `notices/158`)
   — verification against real source is mandatory, not optional.
2. **Bilingual, Bangla-authoritative.** Verbatim Bangla is the authoritative
   text; English is a labeled translation. Never store an English-only
   version of a Bangla-only source page.
3. **Exclude PII and logged-in/wizard pages.** Any page requiring login, or
   containing real personal data (one prior visa/MRV page had real PII), is
   excluded — note it in a manifest, don't process it.
4. **Fee convention:** state the VAT-inclusive amount as primary with
   "(includes 15% VAT)" where VAT applies; one authoritative full-tier record
   per fee type, not split partials. Bengali numerals in Bangla answers,
   Western numerals in English (GLM models mangle Bangla number-words, so
   figures are always stored/rendered as numerals, never spelled out).
5. **URL convention:** application/portal URLs vs informational/static pages
   are different things (`url_kind()` in `answer.py` classifies by
   `_INFO_URL_MARKERS`). A new service's "how do I apply" records must cite
   the actual portal URL, not a static info page.
6. **Screenshots are the fallback of record for blocked sites.** Known
   blockers: `epassport.gov.bd` (Akamai), `orgbdr.gov.bd` (robots-disallowed),
   `bdlaws` (broken charset). **Do not attempt to evade blocks** (no
   fake user-agents, no proxy tricks) — this is explicitly framed as
   "reliable fetch with a verify gate," not "get past protections." If a
   target site blocks Playwright, log it as `needs_manual_screenshot` and
   move on; a human will supply the screenshot.
7. **Dedup against existing corpus before adding anything.** This project has
   nearly duplicated records twice before, caught only by reading the actual
   jsonl. Before adding a new record, grep the target corpus for near-duplicate
   `heading`/`category` content.

**Schema quick reference** (do not invent new fields — match whichever
existing schema is the closer fit for the new content):

- `epassport.jsonl` style (process/fee/FAQ mix): `id, section, category,
  subcategory, heading, source_url, last_updated, content, topics[], service,
  lang(bn+en), last_crawled, official, text, char_len`. FAQ records add
  `faq_no, question_en/bn, answer_en/bn, source_confidence`.
- `ereturn.jsonl` style (pure FAQ): `id, text, category("faq"),
  subcategory, heading, lang, source_url, official, question_bn/en,
  answer_bn/en`.
- `bdris.jsonl` style (mixed content, multiple source files combined by a
  build script): `id, category, title_bn, title_en, content_bn, content_en,
  source_url, source_url_unverified`.
- `nid.jsonl` style: `id, service, category, category_confidence, text,
  heading, lang, source_url, official, fetched_at, source_sha256, bangla_ok,
  bangla_issues`.

**Files you must not break:** `disambiguate.py`, `concept_registry.json`,
`answer.py::ALLOWED_COLLECTIONS`, `test_multiturn.py` (33 checks/9 tests,
hermetic — no DB/LLM needed, run it after any routing change), and the
per-service `eval_<svc>.py` harnesses (hybrid strictness: record-level where
precise, category-level where records legitimately overlap).

---

## Part A — Freshness / Change-Detection Pipeline

### A1. Goal
Detect when a source page behind an existing corpus record has changed.
**This pipeline detects and surfaces drift — it does not silently rewrite
the corpus.** Content edits stay human-reviewed; only the final re-embed
step (after a human finalizes the `.jsonl` edit) is automated.

### A2. New directory: `freshness/`

```
freshness/
  fetch.py              # Playwright re-fetch of each record's source_url
  hash_store.py          # persisted {record_id: {source_url, content_hash, last_checked, last_changed}}
  diff.py                 # text diff between last-known and newly-fetched content
  review.py              # CLI: human approval gate
  reembed.py              # Chroma upsert, by stable chunk id, for approved records only
  run_freshness_check.py  # orchestrator entrypoint
  freshness_store.json    # the persisted hash store (git-tracked, small)
```

### A3. `fetch.py`
- For each of the 4 corpora, build a per-service extraction function that
  pulls `(record_id, source_url, current_content)` — field names differ per
  schema (see §0), so write one mapper per service, not one generic one.
- Skip records with no `source_url` or a non-http value.
- Skip the three known-blocked domains; log them to
  `freshness_store.json` under `status: "needs_manual_screenshot"` instead
  of fetching.
- Fetch via Playwright (headless), extract main text (strip nav/footer/script
  — a simple readability-style strip is fine, doesn't need to be exact,
  since it's compared to itself hash-to-hash over time, not to the corpus
  text directly).
- Rate-limit fetches (e.g. 1/sec) — these are the same government sites the
  live product doesn't want to hammer either.

### A4. `hash_store.py`
```python
def load_store(path: str) -> dict: ...
def save_store(store: dict, path: str) -> None: ...
def get_hash(store: dict, record_id: str) -> str | None: ...
def update_hash(store: dict, record_id: str, source_url: str,
                 new_hash: str, changed: bool) -> dict: ...
```
Keyed by the record's stable `id` field (not by URL — several records can
share a source_url, e.g. multiple FAQ entries from one page).

### A5. `diff.py`
- SHA-256 hash of extracted text per record; compare to stored hash.
- On mismatch, use `difflib` to produce a human-readable diff and append it
  to a pending-review queue (a simple JSON list is fine — no DB needed).

### A6. `review.py` — the approval gate
CLI that:
1. Lists all pending diffs (record id, service, source_url, a short diff
   preview).
2. For each, prompts accept / reject / skip.
3. On accept: writes the record id to `freshness/approved_for_reembed.json`
   **and prints the new fetched text so Monirul can manually update the
   corresponding field(s) in the service's `.jsonl` file** — this step is
   NOT automated (see §A1: content correctness requires the same VAT/fee/
   dedup judgment as original corpus building, which an LLM re-fetch alone
   can't be trusted to apply).
4. Never writes to `data/vectordb/` or the corpus `.jsonl` files itself.

### A7. `reembed.py`
- Reads `approved_for_reembed.json`.
- For each approved record id, re-reads the (by now human-edited) `.jsonl`
  entry and does a **Chroma upsert of that single chunk by its stable id**
  — not a full corpus re-embed. Confirm the Chroma client's `upsert()` (not
  `add()`) is used so this is idempotent.
- Clears the record from the approved queue once re-embedded.

### A8. `run_freshness_check.py`
Orchestrates fetch → diff → writes a `freshness_report_<YYYY-MM-DD>.md`
summarizing: records checked, records changed, records skipped
(blocked/manual), records unchanged. This is the MVP output — run manually
for now. **Stretch goal, not required for done:** a Discord webhook or email
notification on the report; skip this unless Parts A and B below are both
fully done and evaluated green with time/tokens left over.

### A9. Definition of done — Part A
- [ ] Dry run against 5 known-stable NID records produces **zero** false
      diffs.
- [ ] Second consecutive run with no site changes produces zero new diffs
      (idempotent).
- [ ] The 3 known-blocked domains are logged as `needs_manual_screenshot`,
      not crashed on and not evaded.
- [ ] No code path writes to `data/vectordb/` or any `.jsonl` without an
      explicit `approved` entry from `review.py`.
- [ ] `freshness_report_*.md` is generated and human-readable.
- [ ] `test_multiturn.py` and all 4 existing `eval_<svc>.py` still pass
      (freshness code must not import-break anything in the live pipeline).

---

## Part B — New Service Extraction

### B1. Priority order (already decided, don't relitigate)
1. **Visa** — material already partially collected in a prior session's
   excluded pile (one page was excluded for containing real PII — find and
   re-exclude that specific page; the rest of the visa material is usable).
   This is the pilot service for this build — do this one **fully**,
   end-to-end, before touching #2 or #3.
2. NBR family — Return-Verify/PSR, e-Tax, eBIN, eVAT (structurally similar
   to `ereturn`, should reuse most of its schema/eval pattern).
3. Ambitious/stretch — BRTA, Land, City Corp, Police GD. Only attempt if #1
   is fully done, evaluated, and committed, and tokens/time remain.

### B2. Step 1 — locate existing visa material
Search the repo (`data/raw/`, any `excluded/` or similarly named folder,
prior session outputs) for existing visa/MRV content. Read the CODEX audit
notes and prior session history in the repo's README/commit log for where
this was parked. **Identify and explicitly exclude the PII page** — do not
process it, just note its existence in a manifest under `excluded_pii: true`.

### B3. Step 2 — fetch any remaining gaps
For visa content not already collected, use Playwright the same way as
`freshness/fetch.py` (reuse that fetcher, don't rewrite it) against a
curated seed-URL list (visa portal, fee pages, FAQ pages). Bounded
link-following (depth ≤ 2) from each seed, same blocked-domain handling as
Part A (screenshot fallback, no evasion).

### B4. Step 3 — raw `.txt` staging
Write each fetched/screenshotted page to `data/raw/visa/` following the
existing convention: `SECTION:` / `SOURCE:` / `LANGUAGE:` header, blank-line
separators, no per-entry metadata footer (metadata belongs in the `.jsonl`
downstream, not the raw `.txt`).

### B5. Step 4 — structure into `visa.jsonl`
Use the **epassport schema** as the template (visa content is
process/fee/document/FAQ-shaped, same as epassport, not pure-FAQ like
ereturn): `id, section, category, subcategory, heading, source_url,
last_updated, content, topics[], service="visa", lang, last_crawled,
official, text, char_len`.
- Categories should mirror epassport's pattern (status, fees-payment,
  documents-eligibility, process, notices) adapted to visa content — don't
  invent a wildly different taxonomy without reason.
- Apply the fee/VAT convention from §0 to any visa fee record.
- Run the existing `check_encoding.py` mojibake scanner against the new file
  before embedding (NID's build caught a double-encoding bug this way —
  cheap insurance).

### B6. Step 5 — `embed_visa.py`
Model directly on the existing `embed_<svc>.py` scripts (same bge-m3 model,
same ChromaDB collection pattern, collection name `"visa"`).

### B7. Step 6 — wire it into the live pipeline
- Add `"visa"` to `ALLOWED_COLLECTIONS` in `answer.py`.
- Add a `visa` entry to `concept_registry.json`: per-service label,
  EN/Bangla/Banglish keywords, and update any shared concepts that should
  now route to visa too (e.g. if `documents`/`fee` concepts should include
  visa alongside the existing 4 — use judgment, but keep it deterministic,
  no LLM-based registry entries).
- Do **not** touch `disambiguate.py`'s core `decide()` logic — the registry
  is data, the router is code; adding a service should only ever mean
  editing the registry, per the project's own "adding a service = corpus →
  embed → registry, no rewrite" design goal.

### B8. Step 7 — eval harness
Create `visa_gold_queries.json` + `eval_visa.py`, modeled on `eval_ereturn.py`
(the most recently built one — closest in structure and freshest in style).
Include EN, Bangla, and Banglish gold queries, and at least one
out-of-scope probe. Use hybrid strictness (record-level where precise,
category-level where legitimate overlap exists) — same convention as the
other 4.

### B9. Step 8 — regression check
Run **all 5** eval harnesses and `test_multiturn.py` after the registry
change. A new service changing routing behavior for existing queries would
be a regression — check that NID/epassport/ereturn/bdris eval results are
unchanged before considering this done.

### B10. Definition of done — Part B (visa pilot)
- [ ] Visa PII page located and explicitly excluded (not silently dropped —
      noted in a manifest).
- [ ] `visa.jsonl` built, mojibake-checked, no invented `source_url`/fees.
- [ ] `embed_visa.py` run, `visa` collection populated.
- [ ] `ALLOWED_COLLECTIONS` and `concept_registry.json` updated.
- [ ] `eval_visa.py` + gold queries created and passing (or failures
      explained as eval-harshness artifacts, same convention as existing
      services).
- [ ] All 4 pre-existing eval harnesses + `test_multiturn.py` still green
      (no regression from the registry change).
- [ ] README's future-work section updated to reflect visa as done, NBR
      family as next.

---

## Part C — Guardrails (apply throughout, both parts)

- **Never auto-push to GitHub.** Commit locally with clear, incremental
  messages per phase (freshness skeleton, freshness dry-run pass, visa raw
  staging, visa corpus, visa embed+wire, visa eval). Leave the final
  `git push` for Monirul to do after his own review — this matches how the
  rest of the project has worked.
- **Never write to `data/vectordb/` except through an embed/upsert script.**
  It's gitignored and disposable by design; if it ever looks corrupted
  ("hnsw segment reader: Nothing found on disk"), delete and re-embed rather
  than trying to patch it.
- **`setenv.bat` is gitignored and holds the LLM provider config** — don't
  commit it, don't overwrite it, and remember it's needed before
  `answer.py`/`app.py` but not before `embed_*.py`/`eval_*.py`/`build_*.py`.
- If GLM's own output-language or instruction-following quirks show up
  (e.g., it "corrects" or translates content it shouldn't), **don't
  prompt-patch around it** — flag it and stop rather than working around a
  model limitation with fragile prompt tricks. This project already learned
  that lesson once (an off-topic-decline prompt rule proved unreliable and
  needed a routing-layer fix instead).
- If something is ambiguous (which schema to use, whether a concept should
  route to the new service, whether a page counts as PII), **leave a
  `# TODO(Monirul):` comment and move to the next task** rather than
  guessing and continuing silently.

---

## Part D — Execution order (token-budget aware)

Run in this order; each phase should end in a working, tested state so a
token cutoff mid-build doesn't leave things broken:

1. **Part A, §A2–A5** (fetch + hash store + diff, no review/reembed yet) —
   get change *detection* working and dry-run it.
2. **Part A, §A6–A9** (review gate + reembed + report) — finish and pass
   the Part A definition of done.
3. **Part B, §B2–B5** (locate material → raw staging → `visa.jsonl`) — the
   highest-value, lowest-risk chunk of new-service work.
4. **Part B, §B6–B9** (embed → wire into routing → eval → regression check)
   — finish and pass the Part B definition of done.
5. If tokens remain after visa is fully done and evaluated: start the NBR
   family (§B1 item 2) using the *exact same recipe* as visa, since
   `ereturn`'s schema is the closer template for that group.

Do not start Part B before Part A's definition of done is met, and do not
start the NBR family before visa's definition of done is met — partial,
unvalidated work across multiple services at once is how corpus quality
regressions slip in unnoticed on this project.

---

## Part E (addendum, Phase 3.5) — Acquisition Layer

Phase 3.5 added a shared, provenance-aware acquisition layer that supersedes
the assumption that every TLS-invalid or normally-unfetchable page must
immediately become a manual-screenshot task. It does not weaken the
verification gate. Full architecture note: `docs/ACQUISITION_LAYER.md`;
implementation: `acquisition/` + `crawl.py`; tests: `test_acquisition.py`.

Fallback hierarchy (verification status is NEVER inflated):

```
verified HTTP/browser fetch          → verified_normal / direct_pdf_verified / verified_browser
        ↓ failure or JS-only content
browser-rendered fallback            → verified_browser (Chromium strict context,
                                       certificate validation ON)
        ↓ only when the TLS certificate itself is invalid
invalid-TLS capture-only evidence    → capture_only_tls_invalid
        (Chromium evidence context, ignore_https_errors=True;
         requires_secondary_verification=true; original TLS error preserved)
        ↓ only when the browser itself is blocked
manual screenshot                    → needs_manual_screenshot (LAST fallback)
```

`ignore_https_errors=True` does **not** make a source verified. It exists only
on a dedicated evidence context. All anti-evasion rules (no proxy rotation, no
CAPTCHA bypass, no login automation, no UA spoofing, no rate-limit bypass) and
the PII/wizard exclusions remain fully in force; the safe-click policy
(`acquisition/safeclick.py`) never activates submit/apply/login/pay/save/next/
reprint/status actions, and ambiguous interactions are skipped with
`TODO(Monirul): review interaction`.

Integration: `freshness/fetch.py` now sources extraction, robots, and URL
primitives from `acquisition/` (one implementation — byte-identical
extraction verified before commit, so freshness hashes are unchanged). The
bounded crawler (`crawl.py`) is service-agnostic: seeds + allowlist + depth,
with evidence artifacts (HTML/text/PNG/PDF) and a full provenance manifest
under `data/evidence/<service>/`. Corpus construction still prefers real
HTML/text/PDF text; screenshots are evidence for human review only.
