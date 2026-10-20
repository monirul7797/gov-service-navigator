# Government Service Navigator — project instructions

Bilingual (Bangla / English / Banglish) RAG assistant for Bangladeshi government services.
Started as a CSE299 (Junior Design Project) course project; the owner is now developing it into a
long-term public system. Current state, history, goals and open decisions:
@docs/PROJECT_STATUS.md

## Owner's long-term goals (summary — full text in PROJECT_STATUS.md)

1. Complete and expand the dataset (5 services incl. Visa → more services), collected manually.
2. A tracker that continuously monitors government sites and leading newspapers, ranks changes and
   notifies the admin; admin collects evidence and hands it to the system to clean and add.
3. A public website where people ask questions and get grounded answers, hosted on the owner's own
   server. Backend in **Rust**; everything else to be decided together.
4. Anything else that makes it a genuinely working, low-maintenance system.

The owner is new to web development, servers and security. Explain choices and trade-offs, teach as
you go, and ask before anything hard to reverse.

## Data-integrity rules (non-negotiable, carried over from the original build spec)

- Never invent a `source_url`, fee, date or document requirement. Unverified → leave
  `TODO(verify)` and skip the record.
- Bangla source text is authoritative and kept verbatim; English is a labelled translation. Never
  store an English-only version of a Bangla-only source.
- Exclude pages that need login, wizard/transaction pages and anything containing real personal
  data; record exclusions in a manifest instead of silently dropping them.
- Fees: VAT-inclusive amount as primary, "(includes 15% VAT)" where applicable; one authoritative
  full-tier record per fee type. Figures always stored/rendered as numerals (Bengali numerals in
  Bangla answers, Western in English).
- "How do I apply" answers cite the actual portal URL, not a static info page (`url_kind()` in
  `answer.py`).
- Do not evade site blocks (no UA spoofing, proxies, CAPTCHA bypass, login automation). Blocked →
  manual screenshot by the owner.
- Dedup against the existing corpus before adding any record.
- Routing is deterministic (`disambiguate.py` + `concept_registry.json`). Do not replace it with
  LLM judgment. Adding a service = corpus → embed → registry entry, not a router rewrite.

## Repository safety rules

- Never `git add .` / `git add -A`. The working tree holds untracked review bundles and zips
  (`*_review_bundle*/`, `freshness_v21_final_review*`) that must not be committed. Stage explicit
  paths only.
- Never push, merge, rebase shared history or delete branches without the owner's explicit OK.
- `setenv.bat` is gitignored and holds the LLM API key: never commit, print or overwrite it.
- `data/vectordb/` is generated; write to it only through `embed_*.py` scripts. If corrupted,
  delete and re-embed.
- Do not run the legacy `freshness/reembed.py` executor on the working project (audited as unsafe).
- `master` has three uncommitted edits in `acquisition/` (`acquire.py`, `crawler.py`,
  `transport.py`). They are already carried into the `codex/tracker-g0-*` branches. Do not
  discard or overwrite them.
- The Codex/GLM tracker plan in `docs/superpowers/` is **paused at packet P02B** (implemented,
  failing tests, never reviewed). Do not execute or "continue" packets unless the owner decides to
  resume that plan. Whether to continue, re-scope or replace it is an open decision.

## Environment and commands

- Windows; Python 3.12.10. Run `setenv.bat` before `answer.py` / `app.py` (LLM needed); not needed
  for `embed_*.py`, `eval_*.py`, `build_*.py`.
- Chatbot: `python answer.py` (CLI), `streamlit run app.py` (web UI).
- Routing regression tests (no DB/LLM): `python test_multiturn.py`.
- Retrieval evals: `python eval_nid.py`, `eval_epassport.py`, `eval_ereturn.py`, `eval_bdris.py`.
- Freshness/acquisition legacy suites: `test_freshness.py`, `test_freshness_v21.py`,
  `test_acquisition.py`.
- Offline test runner (only on `codex/tracker-g0-*` branches):
  `python -B tools/run_offline_checks.py --legacy --pattern "test_p*.py"`.
- Embeddings: BAAI/bge-m3; vector store: ChromaDB, one collection per service; live collections
  are allowlisted in `answer.py::ALLOWED_COLLECTIONS` (Visa is NOT live yet).
