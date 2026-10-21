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
- Routing: one LLM understanding call per turn (`understand.py`) classifies the turn
  (service_question / small_talk / off_topic / unclear) and writes self-contained search queries;
  code validates its JSON, filters any model-written reply or clarifying question (no links, no
  figures) and keeps every fact boundary (facts only from retrieved records). `disambiguate.py` +
  `concept_registry.json` remain the service catalogue and the fallback router
  (`answer.handle_turn_registry`). Adding a service = corpus → embed → registry entry, not a
  prompt or router rewrite. Never put suite or held-out test questions into the understanding
  prompt.

## Repository safety rules

- Never `git add .` / `git add -A`. The working tree holds untracked review bundles and zips
  (`*_review_bundle*/`, `freshness_v21_final_review*`) that must not be committed. Stage explicit
  paths only.
- Never push, merge, rebase shared history or delete branches without the owner's explicit OK.
- `setenv.bat` (GLM, legacy) and `setenv_deepseek.bat` (primary) are gitignored (`setenv_*.bat`)
  and hold LLM API keys: never commit, print, open or overwrite them.
- `.env` (made by `deploy/make_env.py`) holds the stack's database passwords and is gitignored:
  never commit, print or overwrite it (`.env.example` holds placeholders only).
- Published ports: only `caddy` (`80:80`) and the `web` admin listener (`${GSN_ADMIN_PUBLISH}`,
  always written `<ip>:8081`; a bare `8081` would expose the admin panel publicly) may have
  `ports:`. `deploy/tests/test_compose_exposure.py` enforces this; keep it passing.
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

- Windows; Python 3.12.10. Run `setenv_deepseek.bat` (DeepSeek `deepseek-flash`, thinking
  disabled) before `answer.py` / `app.py` (LLM needed); not needed for `embed_*.py`, the four
  retrieval evals (`eval_nid.py`, `eval_epassport.py`, `eval_ereturn.py`, `eval_bdris.py`) or
  `build_*.py`; `eval_conversations.py` calls the LLM.
- Chatbot: `python answer.py` (CLI), `streamlit run app.py` (web UI).
- Routing tests (no DB/LLM): `python test_understand.py`, `python test_turn_routing.py`,
  `python test_multiturn.py` (fallback router), `python test_eval_conversations.py`.
- Retrieval evals: `python eval_nid.py`, `eval_epassport.py`, `eval_ereturn.py`, `eval_bdris.py`.
- Conversation suite (calls the LLM, ≈US$0.05 per run):
  `python eval_conversations.py --route llm --env-bat setenv_deepseek.bat` (40 turns); held-out
  set: add `--cases conversation_cases_heldout.json` (7 turns); `--route registry` measures the
  fallback router. Set `PYTHONIOENCODING=utf-8` on Windows.
- Retrieval-eval baseline (2026-10-21, unchanged by the routing change): NID 11 pass / 4 warn / 1
  fail / 2 observe of 18; ePassport 19/0/0/2 of 21; eReturn 15/0/1/1 of 17; BDRIS 15/2/1/0 of 18.
  The 3 fails are Banglish gold queries embedded raw (the eval scripts skip transliteration).
- Freshness/acquisition legacy suites: `test_freshness.py`, `test_freshness_v21.py`,
  `test_acquisition.py`.
- Offline test runner (only on `codex/tracker-g0-*` branches):
  `python -B tools/run_offline_checks.py --legacy --pattern "test_p*.py"`.
- Embeddings: BAAI/bge-m3; vector store: ChromaDB, one collection per service; live collections
  are allowlisted in `answer.py::ALLOWED_COLLECTIONS` (Visa is NOT live yet).

### New system (milestone M0, branch `feat/m0-foundations`)

The legacy Python above stays at the repo root. The public-site system lives in new folders; the
full PowerShell guide is `docs/DEVELOPMENT.md`.

| Folder | Holds |
|---|---|
| `web/` | Rust `gsn-web`: public site, admin listener, API edge (`rust-toolchain.toml` pins 1.99.1) |
| `engine/` | Python package `gsn` (engine app, worker, CLI); `constraints.txt` pins the whole tree |
| `frontend/` | SvelteKit apps `public/` (Bangla) and `admin/` (English) |
| `db/` | `init/` (roles, first start only) and `migrations/` (numbered SQL, never edit applied ones) |
| `deploy/` | `Caddyfile`, `make_env.py`, `smoke.py`, `check_exposure.py` and their tests |
| `tools/` | `cargo_docker.py` (runs cargo in Docker) |
| `contracts/` | shared JSON API examples that every implementation's tests assert against |
| `tests/integration/` | tests that run inside the stack (the `itest` job) |

- Stack (`compose.yaml`: `caddy`, `web`, `engine`, `worker`, `postgres`, plus one-shot `migrate`):
  `python deploy/make_env.py` once, then `docker compose build`, `docker compose up -d`,
  `python deploy/smoke.py --url http://localhost/readyz --timeout 300`, `docker compose down`
  (`-v` also wipes the local database).
- Tests: Python in `.venv` (never the global Python):
  `.venv\Scripts\python -m pytest engine/tests deploy/tests tools/tests -q`; Rust, only via Docker:
  `python tools/cargo_docker.py test` (also `fmt --check`, `clippy --all-targets --locked -- -D
  warnings`); frontend, in each of `frontend/public` and `frontend/admin`: `npm ci --cache
  G:/gsn-cache/npm`, `npm run lint`, `npm run check`, `npm run test:build`; stack:
  `docker compose -f compose.yaml -f compose.test.yaml run --rm itest` (local stack only; it adds
  `audit_log` rows). `.github/workflows/ci.yml` runs all of this on every push and pull request.
- Rust is compiled only inside Docker on this machine (no MSVC): never run host `cargo`. If `docker`
  is not on PATH, add `G:\Docker\DockerDesktop\resources\bin`. Keep at least 10 GB free on the
  Docker drive (`G:`); `docker builder prune -f` reclaims the build cache.
