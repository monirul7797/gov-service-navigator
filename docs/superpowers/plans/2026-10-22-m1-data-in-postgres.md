# M1 Data in PostgreSQL Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The knowledge base lives in PostgreSQL + pgvector as immutable, versioned releases,
built and published with `gsn release`, protected against tampering, and proven to retrieve
exactly what today's ChromaDB collections retrieve.

**Architecture:** New modules in the `gsn` package (`engine/src/gsn/`) run in a one-off `release`
container (image `gsn-release:dev`) that reads committed content from the repository's `.git`
(read-only) and writes to PostgreSQL as `gsn_worker`. Schema, state rules, publish/rollback and
engine views are SQL migrations run by `gsn-web migrate`; a superuser-owned watchdog
(`db/init/20-guard.sh`) and a hash chain protect the history. The legacy root code is untouched and
keeps serving from ChromaDB until M2.

**Tech Stack:** Python 3.12 (`gsn`: psycopg 3, stdlib; release image adds sentence-transformers
5.6.0, transformers 5.13.1, tokenizers 0.22.2, CPU torch 2.13.0), PostgreSQL 17 + pgvector 0.8.7,
sqlx migrations (Rust `gsn-web`), Docker Compose, GitHub Actions, git plumbing.

**Spec:** `docs/superpowers/specs/2026-10-22-m1-data-in-postgres-design.md` (revised after the
DeepSeek review; triage in `docs/superpowers/reports/2026-10-22-m1-design-review-deepseek.md`).
Parent: `docs/superpowers/specs/2026-10-20-system-architecture-design.md` §6, §10.2, §11.

## Global Constraints

- Branch `feat/m1-data-in-postgres` in the main checkout. Stage explicit paths only — never
  `git add .` / `-A`. Never stage `acquisition/*` (owner's uncommitted edits: never touch, stage or
  discard), `setenv*.bat`, `.env`, `.superpowers/`, review bundles or zips. Never open or print
  `setenv*.bat` or `.env`.
- Legacy root code, `data/chunks/*`, `*_gold_queries.json` and `concept_registry.json` are **not
  modified** (one exception: the stale docstring in `eval_epassport.py`, Task B3). No value in any
  committed input is invented; `known_failures` IDs come only from the baseline run (Task D1).
- Rust only inside Docker (`python tools/cargo_docker.py …`); new `.sql` files are compiled into
  `gsn-web`, so run `docker compose build web` after adding a migration. Applied migrations
  (`0001_audit_log.sql`, and each new one once merged) are never edited.
- Python tooling and tests run in `.venv` (`.venv\Scripts\python`); the global Python is used only
  for legacy scripts and the ChromaDB baseline (Task D1). **No ML packages in `.venv`** (C: has
  ~4.7 GB free): host tests use `FakeEmbedder`; the real model runs only in `gsn-release:dev`.
- Keep ≥ 10 GB free on G: (Docker). Docker CLI: prepend `G:\Docker\DockerDesktop\resources\bin` to
  `PATH` if needed.
- Model pin: `BAAI/bge-m3`, revision `5617a9f61b028005a4858fdac845db406aefb181`; files
  `1_Pooling/*`, `config.json`, `config_sentence_transformers.json`, `modules.json`,
  `pytorch_model.bin`, `sentence_bert_config.json`, `sentencepiece.bpe.model`,
  `special_tokens_map.json`, `tokenizer.json`, `tokenizer_config.json` (exactly what the PC's
  snapshot holds and loads). Model id stored on releases: `BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181`.
  Fake model id: `fake-sha256-v1`. Vectors: 1024 dimensions, unit length.
- Retrieval constants (copied from `answer.py`): `CANDIDATE_N = 8`, `PER_SERVICE_K = 2`,
  `SERVICE_MIN_SIM = 0.40`, `MAX_CONTEXT = 6`, `SCOPED_TOP_K = 8`, `GROUNDING_FLOOR = 0.42`.
  Eval out-of-scope ceilings: NID/e-Passport/e-Return `0.55`, BDRIS `0.62`. Parity tolerance
  `0.001`; stored-vector cosine floor `0.9999`.
- Every `SECURITY DEFINER` function: `SET search_path = pg_catalog, <its schema>`, every object
  name schema-qualified, `REVOKE EXECUTE … FROM PUBLIC`, `GRANT EXECUTE` only to the roles named
  in the task.
- Release content is read only from a commit via `gsn.gitsource.GitSource`, never the working
  tree. The `release` container mounts only `${GSN_REPO_GIT:-./.git}:/repo.git:ro` and
  `gsn-models:/models`; it has no `ports:` and receives only `GSN_DB_WORKER_PASSWORD`.
- Database settings as separate `GSN_DB_*` variables (never a URL); vectors are passed to
  PostgreSQL as text (`'[x,y,…]'::vector`) — no `pgvector` Python package, no numpy in the core
  image.
- `gsn` CLI exit codes: `0` success (build validated); `1` runtime error; `2` usage/configuration
  error (including embedder/model mismatch); `3` build finished but `rejected`; `4` integrity
  problems (nothing written); `5` parity failed.
- Audit actions written by M1: `release.build`, `release.validate`, `release.reject`,
  `release.override`, `release.publish`, `release.rollback`; actor from `GSN_ACTOR` (default
  `owner`); target `release:<id>`.
- After Task B2 lands, the local database must be recreated once: `docker compose down -v`.

## Review Focus

- Re-running `build` on the same commit (e.g. after a rejected build) must create a new draft that
  reuses every existing version and vector, not fail on unique keys — test in Task A6.
- Corpus files with a UTF-8 BOM, CRLF line endings or blank lines must load exactly like clean
  files — test in Task A2.
- `--commit` given as a short SHA, a branch name, or an unknown ref: the first two resolve to the
  full 40-hex SHA stored on the release; the third is a clear exit-2 error — test in Task A1.
- A release built with the fake embedder must never be published by accident: `publish` refuses
  `fake-*` models unless `--allow-fake` — test in Task A6.
- A gold query whose `expected_id` is not in its service's corpus (after a future data edit) is an
  integrity problem, not a silent eval FAIL — test in Task A2.

## File map

| Path | Task | Responsibility |
|---|---|---|
| `engine/src/gsn/gitsource.py` | A1 | `ContentSource`, `GitSource` (hardened git plumbing), `DictSource`, secret-path scan |
| `engine/src/gsn/hashing.py` | A1 | canonical JSON, `content_hash`, `text_hash`, `HASH_VERSION` |
| `engine/src/gsn/release/inputs.py` | A2 | manifest/thresholds/registry/corpora/gold loading |
| `engine/src/gsn/release/fields.py` | A2 | `url_kind` port, `parse_verified_at`, `version_fields` |
| `engine/src/gsn/release/integrity.py` | A2 | integrity rules |
| `data/release_manifest.json`, `data/release_thresholds.json` | A2 (D1 fills `known_failures`) | committed release inputs |
| `db/migrations/0002_releases.sql` | A3 | content tables, state triggers, publish/rollback, engine views, grants |
| `engine/src/gsn/embed.py` | A4 (real embedder C1) | `Embedder`, `FakeEmbedder`, `SentenceTransformerEmbedder`, `get_embedder`, `fetch_model` |
| `engine/src/gsn/store.py` | A4 | all SQL used by the CLI (worker role) |
| `engine/src/gsn/retrieval.py` | A5 | `Hit`, constants, `retrieve()` port |
| `engine/src/gsn/evals.py` | A5 | per-service verdict rules, gold runs, gate |
| `engine/src/gsn/release/build.py`, `engine/src/gsn/release/commands.py` | A6 | build orchestration; publish/rollback/list/show/eval commands |
| `engine/src/gsn/cli.py` | A6 (+B1, C1, D2) | subcommands |
| `engine/Dockerfile` (`release` target), `compose.yaml` (`release` service) | A6 (ML in C1) | release image and one-off service |
| `db/migrations/0003_audit_chain.sql`, `engine/src/gsn/audit.py` | B1 | hash chain, `audit_verify()`, sequence grant tidy-up |
| `db/init/20-guard.sh`, `db/migrations/0004_guard_registration.sql` | B2 | watchdog, DB ownership/revokes, registrations |
| `engine/constraints-ml.txt` | C1 | locked ML tree (generated in Linux) |
| `tools/m1/export_chroma_baseline.py`, `docs/reports/m1-parity/*` | D1 | ChromaDB baseline (global Python) |
| `engine/src/gsn/parity.py` | D2 | comparison rules and `gsn parity` |
| `.github/workflows/evals.yml` | D3 | real-model evals |

Tests: host unit tests in `engine/tests/` (run with `.venv\Scripts\python -m pytest engine/tests -q`);
stack tests in `tests/integration/` (run with `docker compose -f compose.yaml -f compose.test.yaml run --rm itest`).
The stack-test cycle used below as **"stack cycle"** is:
`docker compose --profile tools build && docker compose up -d && python deploy/smoke.py --url http://localhost/readyz --timeout 300 && docker compose -f compose.yaml -f compose.test.yaml run --rm itest`.

---

## Stage A — schema and release core (fake embedder)

### Task A1: Hardened git source and canonical hashing

**Files:**
- Create: `engine/src/gsn/gitsource.py`, `engine/src/gsn/hashing.py`
- Test: `engine/tests/test_gitsource.py`, `engine/tests/test_hashing.py`

**Interfaces:**
- Produces: `class ContentSource(Protocol)`: attribute `commit: str` (40-hex), `read(path: str) -> bytes` (raises `FileNotFoundError(path)`).
- Produces: `GitSource(git_dir: str, ref: str = "HEAD")` — resolves `ref` with `git rev-parse --verify <ref>^{commit}`; `read()` uses `git cat-file blob <commit>:<path>`. Every git call: argv list (no shell), `--git-dir=<git_dir>`, `-c safe.directory=<git_dir>`, `-c core.fsmonitor=false`, `-c core.hooksPath=/dev/null`, env adds `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=<os.devnull>`, `GIT_TERMINAL_PROMPT=0`. Failures raise `GitSourceError(message)` naming the ref/path, never dumping git's environment.
- Produces: `GitSource.secret_paths_in_history() -> list[str]` — every path in `git log --all --name-only --format=` matching `(^|/)setenv[^/]*\.bat$` or `(^|/)\.env$`.
- Produces: `DictSource(commit: str, files: dict[str, bytes])` for tests.
- Produces: `HASH_VERSION = 1`; `canonical_json(record: dict) -> bytes` = `json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")`; `content_hash(record: dict) -> str` (sha256 hex of `canonical_json`); `text_hash(text: str) -> str` (sha256 hex of UTF-8).

- [ ] **Step 1: Write the failing tests** (tests build a throw-away repo in `tmp_path` with the host `git`; skip with a clear reason if `git` is missing)

```python
# test_gitsource.py
def test_resolves_head_branch_and_short_sha_to_full_sha(tmp_repo):   # all three == the 40-hex commit
def test_unknown_ref_raises_gitsourceerror_naming_the_ref(tmp_repo):
def test_reads_committed_bytes_not_working_tree(tmp_repo):          # edit file after commit → read() returns committed bytes
def test_missing_path_raises_filenotfounderror(tmp_repo):
def test_reads_from_bare_git_dir_only(tmp_repo):                      # GitSource(str(tmp_repo / ".git"))
def test_secret_paths_found_in_history(tmp_repo):                     # commit setenv_x.bat, then delete it → still reported
def test_clean_history_reports_no_secret_paths(tmp_repo):
def test_dictsource_behaves_like_a_source():
# test_hashing.py
def test_canonical_json_is_key_order_independent_and_keeps_bangla():  # {"b":1,"a":"জন্ম"} → b'{"a":"জন্ম","b":1}' (UTF-8, not \u escapes)
def test_content_hash_changes_with_any_field():
def test_text_hash_is_sha256_of_utf8():
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest engine/tests/test_gitsource.py engine/tests/test_hashing.py -q` → FAIL (`ModuleNotFoundError: gsn.gitsource`).
- [ ] **Step 3: Implement both modules** as specified in Interfaces.
- [ ] **Step 4: Verify** — same command → all pass; then `.venv\Scripts\python -m pytest engine/tests -q` → no regressions.
- [ ] **Step 5: Commit**

```bash
git add engine/src/gsn/gitsource.py engine/src/gsn/hashing.py engine/tests/test_gitsource.py engine/tests/test_hashing.py
git commit -m "feat(m1): hardened git content source and canonical hashing"
```

---

### Task A2: Release inputs, record fields and integrity checks

**Files:**
- Create: `engine/src/gsn/release/__init__.py`, `engine/src/gsn/release/inputs.py`, `engine/src/gsn/release/fields.py`, `engine/src/gsn/release/integrity.py`, `data/release_manifest.json`, `data/release_thresholds.json`
- Test: `engine/tests/test_release_inputs.py`, `engine/tests/test_release_fields.py`, `engine/tests/test_release_integrity.py`

**Interfaces:**
- Consumes: `ContentSource`, `DictSource` (A1).
- Produces (`inputs.py`): `@dataclass(frozen=True) ServiceEntry(service_id, name_bn, name_en, status, corpus, gold, verified_at_field)`; `Thresholds(known_failures: dict[str, str])`; `ReleaseInputs(commit, manifest: list[ServiceEntry], manifest_json: dict, thresholds: Thresholds, thresholds_json: dict, registry: dict, registry_sha256: str, corpora: dict[str, list[dict]], gold: dict[str, dict])`; `load_inputs(source: ContentSource) -> ReleaseInputs` raising `InputError(problems: list[str])`. Paths: `MANIFEST_PATH = "data/release_manifest.json"`, `THRESHOLDS_PATH = "data/release_thresholds.json"`, `REGISTRY_PATH = "concept_registry.json"`. JSONL: decode `utf-8-sig`, accept CRLF, skip blank lines; a bad line → problem `"<path>:<line>: invalid JSON"`.
- Produces (`fields.py`): `url_kind(url: str) -> str` (exact port of `answer.url_kind`, same `_INFO_URL_MARKERS`); `parse_verified_at(value: object) -> date | None` (accepts ISO date or date-time string, returns the date part; `None` → `None`; anything else raises `ValueError`); `@dataclass(frozen=True) VersionFields(category, subcategory, heading, lang, source_url, url_kind, official, source_verified, verified_at)`; `version_fields(record: dict, entry: ServiceEntry) -> VersionFields` (`source_verified = not record.get("source_url_unverified", False)`; `verified_at` from `entry.verified_at_field` or `None`).
- Produces (`integrity.py`): `check_integrity(inputs: ReleaseInputs, stored_record_services: Mapping[str, str], stored_service_names: Mapping[str, tuple[str, str]], today: date) -> list[str]` — empty list means clean.
- `data/release_manifest.json`: `{"services": [...]}` with, in this order: `nid` (`data/chunks/nid.jsonl`, `nid_gold_queries.json`, `fetched_at`), `epassport` (`data/chunks/epassport.jsonl`, `epassport_gold_queries.json`, `last_crawled`), `ereturn` (`data/chunks/ereturn.jsonl`, `ereturn_gold_queries.json`, `null`), `bdris` (`data/chunks/bdris.jsonl`, `bdris_gold_queries.json`, `null`), all `"status": "live"`. Names: `nid` "জাতীয় পরিচয়পত্র (NID)" / "National ID (NID)"; `epassport` "ই-পাসপোর্ট" / "e-Passport"; `ereturn` "ই-রিটার্ন (আয়কর)" / "e-Return (income tax)"; `bdris` "জন্ম ও মৃত্যু নিবন্ধন" / "Birth and death registration".
- `data/release_thresholds.json`: `{"known_failures": {}}` (filled from the baseline in Task D1).

- [ ] **Step 1: Write the failing tests** (fixtures are tiny `DictSource`s; one test per integrity rule)

```python
# test_release_inputs.py
def test_loads_the_committed_inputs_of_this_repository():   # GitSource(repo .git, "HEAD") after this task's commit → 4 services, 293 records, 74 gold queries
def test_bom_crlf_and_blank_lines_load_like_clean_jsonl():  # same records either way
def test_missing_or_invalid_files_are_reported_together():   # two problems in one InputError
def test_registry_sha256_is_of_the_raw_bytes():
# test_release_fields.py
def test_url_kind_matches_answer_url_kind_on_every_corpus_url():   # answer.py imports chromadb (absent from .venv): extract `_INFO_URL_MARKERS` and `url_kind` from answer.py with `ast`, exec them in an empty namespace, compare with gsn's port on all 293 URLs
def test_parse_verified_at_accepts_date_and_datetime():   # "2026-07-12T23:04:19" → date(2026,7,12); "2026-07-13" → date(2026,7,13)
def test_parse_verified_at_rejects_garbage():             # "July", 5 → ValueError
def test_source_verified_is_the_negated_flag():           # flag True → False; flag False/missing → True
# test_release_integrity.py — each builds a clean fixture, breaks one thing, expects exactly one problem mentioning the record/query id
def test_clean_inputs_have_no_problems():
def test_live_service_without_registry_entry():
def test_record_missing_id_text_or_source_url():
def test_todo_verify_anywhere_in_record():                 # nested string value "TODO(verify)"
def test_duplicate_record_ids_across_services():
def test_duplicate_texts():
def test_bangla_lang_without_bengali_script():             # lang "bn+en", text all ASCII
def test_unparseable_and_future_verified_at():             # two problems
def test_record_moved_to_another_service():                # stored_record_services says nid, corpus says bdris
def test_service_names_changed_from_stored():
def test_gold_expected_id_missing_from_corpus():           # Review Focus
def test_the_committed_corpus_is_clean():                  # real inputs at HEAD → []
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest engine/tests/test_release_inputs.py engine/tests/test_release_fields.py engine/tests/test_release_integrity.py -q` → FAIL (imports).
- [ ] **Step 3: Implement the three modules and write the two data files.** `gsn` never imports legacy root code (the image does not contain it).
- [ ] **Step 4: Verify** — same command → pass (the "committed inputs" tests need the data files committed: commit the data files first, then re-run), then `.venv\Scripts\python -m pytest engine/tests -q`.
- [ ] **Step 5: Commit**

```bash
git add engine/src/gsn/release data/release_manifest.json data/release_thresholds.json engine/tests/test_release_inputs.py engine/tests/test_release_fields.py engine/tests/test_release_integrity.py
git commit -m "feat(m1): release manifest, inputs loader, record fields and integrity checks"
```

---

### Task A3: Migration 0002 — content tables, state rules, publish/rollback, engine views

**Files:**
- Create: `db/migrations/0002_releases.sql`, `tests/integration/test_releases_schema.py`, `tests/integration/test_00_first_publication.py`
- Modify: `tests/integration/test_grants.py` (expectations for the new tables)

**Interfaces:**
- Consumes: roles from `db/init/10-roles.sh`; `audit_log` (0001).
- Produces tables exactly as spec §3 (column names are the spec's). Constraints: `services.service_id` matches `^[a-z][a-z0-9_]*$`; `record_versions`: `UNIQUE (record_id, content_hash, embedding_model)`, `UNIQUE (version_id, record_id)`, index on `(text_hash, embedding_model)`, `source_commit ~ '^[0-9a-f]{40}$'`, `url_kind IN ('info','portal')`, `embedding vector(1024) NOT NULL`; `release_members`: PK `(release_id, version_id)`, `UNIQUE (release_id, record_id)`, FK `(version_id, record_id) → record_versions (version_id, record_id)`; `active_release.singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton)`; `publications.action IN ('publish','rollback')`.
- Trigger names (Task B2 registers them): on every guarded table `<table>_guard_row` (row-level) and `<table>_guard_truncate` (statement-level).
- Produces trigger functions (owned by `gsn_migrate`, plain `plpgsql`): `gsn_reject_change()` raising `'<table> is append-only'` (used `BEFORE UPDATE OR DELETE` row + `BEFORE TRUNCATE` statement on `services`, `records`, `record_versions`, `publications`); `releases_guard()` (UPDATE allowed only `draft → validated|rejected` with every column other than `status`, `gate_report`, `override_reason` unchanged; DELETE/TRUNCATE rejected; message `'release <id> is frozen'`); `release_children_guard()` on `release_members` and `release_services` (INSERT/DELETE only while the parent release is `draft`; UPDATE/TRUNCATE rejected); `active_release_guard()` (target must be `validated`; DELETE/TRUNCATE rejected).
- Produces `publish_release(p_release_id bigint, p_actor text) RETURNS void` and `rollback_release(p_actor text) RETURNS bigint` — `SECURITY DEFINER`, owner `gsn_migrate`, `SET search_path = pg_catalog, public`, lock the `active_release` row (or the table when empty) first; behaviour exactly spec §6 (publish refuses non-validated: `'release <id> is not validated'`; already active: `'release <id> is already active'`; rollback with no earlier publication: `'nothing to roll back'`); each writes the `active_release` switch, one `publications` row and one `audit_log` row (`release.publish` / `release.rollback`, target `release:<id>`, details `{"previous": <id or null>}`). EXECUTE granted to `gsn_worker` only.
- Produces views (owner `gsn_migrate`, default security, i.e. owner's rights): `engine_active_release(release_id, registry, registry_sha256, embedding_model)`, `engine_active_services(service_id, name_bn, name_en)` (live in the active release), `engine_active_versions(version_id, record_id, service_id, embed_text, record, category, subcategory, heading, lang, source_url, url_kind, official, source_verified, verified_at, embedding, embedding_model)`.
- Grants: exactly spec §3.1 (`gsn_engine`: SELECT on the three views only; `gsn_web`: nothing new).

- [ ] **Step 1: Write the failing integration tests** (`test_releases_schema.py`; helper `make_validated_release(connect, n_records=2)` inserts a service, records, versions with a constant unit vector, a draft release with members, then sets it `validated` — all as `gsn_worker`)

```python
def test_worker_can_build_a_draft_and_validate_it(connect):
def test_versions_services_records_publications_are_append_only(connect):   # UPDATE/DELETE/TRUNCATE as worker → InsufficientPrivilege; as migrate → message "<table> is append-only"
def test_release_status_moves_once_out_of_draft(connect):        # validated → rejected raises "is frozen"; changing source_commit raises
def test_members_frozen_after_draft(connect):                    # insert/delete member of validated release raises
def test_one_version_per_record_per_release(connect):            # second version of same record → UniqueViolation
def test_publish_and_rollback_write_switch_publication_and_audit(connect):  # publish r1, publish r2, rollback → active r1; publications rows 3; audit rows for each (read via web)
def test_second_rollback_reapplies_the_undone_release(connect):
def test_publish_refuses_unvalidated_and_already_active(connect):
def test_worker_cannot_write_active_release_or_publications_directly(connect):
def test_engine_sees_only_the_active_release_views(connect):     # SELECT from views ok, only active members; SELECT record_versions/releases → InsufficientPrivilege
def test_publish_rollback_functions_are_not_executable_by_web_or_engine(connect):
```

Note: tests share one database across runs; make every test create its own service/record IDs (`uuid4().hex[:8]` suffix) and not assume an empty `active_release`. The one state that exists only on a fresh database — "nothing to roll back" after the very first publication — lives in `tests/integration/test_00_first_publication.py` (sorts first): if `publications` is empty it publishes one release and asserts `rollback_release` raises `'nothing to roll back'`; otherwise it skips with that reason. CI runs itest on a fresh database, so it runs there.

- [ ] **Step 2: Run to verify failure** — stack cycle → new tests FAIL (`relation "services" does not exist`).
- [ ] **Step 3: Write `0002_releases.sql`; update `test_grants.py` for the new objects.**
- [ ] **Step 4: Verify** — `docker compose build web` then stack cycle → all integration tests pass; `docker compose ps -a` shows `migrate` exited 0.
- [ ] **Step 5: Commit**

```bash
git add db/migrations/0002_releases.sql tests/integration/test_releases_schema.py tests/integration/test_00_first_publication.py tests/integration/test_grants.py
git commit -m "feat(m1): release tables, state rules, publish/rollback functions and engine views"
```

---

### Task A4: Embedder interface, fake embedder and the store

**Files:**
- Create: `engine/src/gsn/embed.py`, `engine/src/gsn/store.py`
- Test: `engine/tests/test_embed.py`, `tests/integration/test_store.py`

**Interfaces:**
- Consumes: schema (A3); `VersionFields`, `content_hash`, `text_hash` (A1, A2).
- Produces (`embed.py`): `class Embedder(Protocol)`: `model_id: str`; `encode(texts: Sequence[str]) -> list[list[float]]` (1024 floats each, unit length). `FakeEmbedder` (`model_id = "fake-sha256-v1"`; vector from SHA-256 of `f"{i}|{text}"` for i in 0..31, bytes → 1024 values in [-1, 1], normalised; identical text → identical vector). `get_embedder(env: Mapping[str, str]) -> Embedder`: `GSN_EMBEDDER` = `fake` → `FakeEmbedder`; unset or `bge-m3` → `SentenceTransformerEmbedder` (raises `NotImplementedError("real embedder arrives in Task C1")` until C1); anything else → `ValueError`. Importing `gsn.embed` must not import `torch` or `sentence_transformers`.
- Produces (`store.py`, all functions take an open psycopg connection; vectors as text):
  - `@dataclass(frozen=True) NewVersion(record_id, service_id, record: dict, embed_text, content_hash, text_hash, fields: VersionFields, embedding: list[float])`
  - `@dataclass(frozen=True) Draft(source_commit, manifest_json, registry, registry_sha256, thresholds_json, embedding_model, note, created_by, services: list[ServiceEntry], member_version_ids: dict[str, int], new_versions: list[NewVersion])`
  - `stored_record_services(conn) -> dict[str, str]`; `stored_service_names(conn) -> dict[str, tuple[str, str]]`
  - `existing_versions(conn, keys: Iterable[tuple[str, str]], model: str) -> dict[str, int]` (record_id → version_id for matching `(record_id, content_hash)`)
  - `existing_vectors(conn, text_hashes: Iterable[str], model: str) -> dict[str, list[float]]`
  - `create_draft(conn, draft: Draft) -> int` — **one transaction**: insert missing services and records (`ON CONFLICT DO NOTHING`), insert new versions, the release (`draft`), `release_services`, members; returns `release_id`.
  - `set_gate_result(conn, release_id: int, status: str, gate_report: dict, override_reason: str | None) -> None`
  - `search(conn, release_id: int, service_id: str, vector: list[float], k: int) -> list[Hit]` — `ORDER BY embedding <=> q, record_id`, `sim = 1 - distance` (uses `Hit` from Task A5's `gsn.retrieval`; define `Hit` here in A4 inside `gsn/retrieval.py` with only the dataclass, A5 adds the logic)
  - `release_row(conn, release_id) -> dict | None`; `list_releases(conn) -> list[dict]` (id, status, source_commit, created_at, embedding_model, is_active); `member_hashes(conn, release_id) -> dict[str, str]` (record_id → content_hash); `live_services(conn, release_id) -> list[str]`; `active_release_id(conn) -> int | None`; `stored_vectors(conn, release_id) -> dict[str, list[float]]`
  - `publish(conn, release_id: int, actor: str) -> None` / `rollback(conn, actor: str) -> int` (call the SQL functions; translate their exceptions to `StoreError(message)`)
  - `audit(conn, actor: str, action: str, target: str, details: dict) -> None`
- `Hit` (in `engine/src/gsn/retrieval.py`): `@dataclass(frozen=True) Hit(record_id: str, service_id: str, sim: float, text: str, category: str | None, subcategory: str | None, heading: str | None, source_url: str)`.

- [ ] **Step 1: Write the failing tests**

```python
# engine/tests/test_embed.py
def test_fake_vectors_are_deterministic_unit_length_1024():
def test_different_texts_give_different_vectors():
def test_get_embedder_selects_fake_and_rejects_unknown():
def test_importing_embed_does_not_import_torch():          # subprocess: import gsn.embed; assert "torch" not in sys.modules
# tests/integration/test_store.py (FakeEmbedder; unique ids per test)
def test_create_draft_inserts_everything_in_one_transaction(connect):   # failure mid-way (duplicate member) leaves no release row
def test_existing_versions_and_vectors_are_found_by_hash_and_model(connect):
def test_search_orders_by_distance_then_record_id(connect):   # two records with identical text → same sim → record_id order; query == a record's text → sim ≈ 1.0 first
def test_search_is_limited_to_the_release_and_service(connect):
def test_publish_rollback_wrappers_raise_storeerror_with_the_db_message(connect):
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest engine/tests/test_embed.py -q` and the stack cycle → FAIL (imports).
- [ ] **Step 3: Implement `embed.py`, `store.py` and the `Hit` dataclass.**
- [ ] **Step 4: Verify** — both commands pass; `.venv\Scripts\python -m pytest engine/tests -q` clean.
- [ ] **Step 5: Commit**

```bash
git add engine/src/gsn/embed.py engine/src/gsn/store.py engine/src/gsn/retrieval.py engine/tests/test_embed.py tests/integration/test_store.py
git commit -m "feat(m1): embedder interface with fake embedder, and the release store"
```

---

### Task A5: Retrieval port and eval harness

**Files:**
- Modify: `engine/src/gsn/retrieval.py`
- Create: `engine/src/gsn/evals.py`
- Test: `engine/tests/test_retrieval.py`, `engine/tests/test_evals.py`

**Interfaces:**
- Consumes: `Hit` (A4); `Embedder` (A4).
- Produces (`retrieval.py`): the six constants (Global Constraints); `SearchFn = Callable[[str, list[float], int], list[Hit]]` (service_id, vector, k); `retrieve(search: SearchFn, embedder: Embedder, queries: Sequence[str], live_services: Sequence[str], services: Sequence[str] | None = None) -> list[Hit]` — port of `answer.retrieve()`'s `queries=` path: de-duplicate stripped non-empty queries keeping order; scope = requested services that are live (unknown ones ignored; empty → all live); exactly one scoped service → `per_service_k = max_context = SCOPED_TOP_K`, `candidate_n = max(CANDIDATE_N, SCOPED_TOP_K + 4)`; per service keep each record's best sim across queries; a service contributes only if its best sim ≥ `SERVICE_MIN_SIM`, taking its top `per_service_k`; merge, sort by `(-sim, record_id)`, cut to `max_context`. `grounded(hits) -> bool` (`hits` non-empty and `hits[0].sim >= GROUNDING_FLOOR`).
- Produces (`evals.py`): `@dataclass(frozen=True) Rule(gate_field: str, meta_field: str, ceiling: float, observe: bool)`; `RULES = {"nid": Rule("expected_category","category",0.55,True), "epassport": Rule("expected_category","category",0.55,True), "ereturn": Rule("expected_subcategory","subcategory",0.55,True), "bdris": Rule("expected_category","category",0.62,False)}`; `@dataclass(frozen=True) Verdict(query_id, service_id, verdict, reasons: list[str], top: list[tuple[str, float]])` with `verdict` in `"PASS"`, `"PASS (warn)"`, `"OBSERVE"`, `"FAIL"`; `judge(rule: Rule, gq: dict, hits: list[Hit], top_k: int) -> Verdict` (pure: the legacy `eval_one` branch order — out-of-scope probe, observational probe (only if `rule.observe`), gate-field check over the top-k metadata, record check, soft `min_similarity` WARN — with BDRIS treating any `expected_category is None` as an out-of-scope probe); `run_gold(search: SearchFn, embedder: Embedder, service_id: str, gold: dict) -> list[Verdict]` (one embedding per query, `top_k = gold.get("top_k", 4)`); `@dataclass(frozen=True) GateResult(passed: bool, unexpected_failures: list[str], known_failures_seen: list[str], counts: dict[str, dict[str, int]])`; `gate(verdicts: Iterable[Verdict], known_failures: Mapping[str, str]) -> GateResult`.

- [ ] **Step 1: Write the failing tests** (fake `search` closures over hand-made hits; no DB)

```python
# test_retrieval.py
def test_single_scoped_service_uses_scoped_limits():          # 12 candidates requested, 8 kept
def test_service_below_min_sim_contributes_nothing():         # best 0.399 → excluded; 0.40 → included
def test_best_sim_across_queries_wins_and_duplicates_merge():
def test_max_context_cut_and_tie_break_by_record_id():
def test_unknown_or_hidden_services_are_ignored():
def test_blank_and_duplicate_queries_are_dropped():
def test_grounded_floor_is_inclusive():                       # 0.42 → True, 0.4199 → False
# test_evals.py — one test per branch, per rule; values from the legacy scripts
def test_out_of_scope_probe_ceiling_055_vs_bdris_062():       # out-of-scope probe, best sim 0.60: nid → FAIL (≥ 0.55), bdris → PASS (< 0.62)
def test_observe_branch_exists_except_bdris():                # min_similarity set, no category/id: nid OBSERVE; bdris → treated as out-of-scope probe
def test_ereturn_gates_on_subcategory():
def test_expected_id_must_be_in_top_k():
def test_min_similarity_is_a_soft_warn():                     # best relevant sim below floor → "PASS (warn)"
def test_gate_ignores_known_failures_and_warns_observes():
def test_gate_counts_per_service():
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest engine/tests/test_retrieval.py engine/tests/test_evals.py -q` → FAIL.
- [ ] **Step 3: Implement.** Read `answer.py:160-218` and `eval_{nid,epassport,ereturn,bdris}.py::eval_one` before writing; the reason strings may be shorter than the legacy ones, the verdicts may not differ.
- [ ] **Step 4: Verify** — same command passes; full `engine/tests` clean.
- [ ] **Step 5: Commit**

```bash
git add engine/src/gsn/retrieval.py engine/src/gsn/evals.py engine/tests/test_retrieval.py engine/tests/test_evals.py
git commit -m "feat(m1): retrieval port and gold-query eval harness"
```

---

### Task A6: Build orchestration, CLI, release image and compose service

**Files:**
- Create: `engine/src/gsn/release/build.py`, `engine/src/gsn/release/commands.py`, `tests/integration/test_release_build.py`, `engine/tests/test_cli_release.py`
- Modify: `engine/src/gsn/cli.py`; `engine/Dockerfile` (new `release` target); `compose.yaml` (`release` service, `gsn-models` volume); `deploy/tests/test_compose_exposure.py`; `.github/workflows/ci.yml` (stack job: `--profile tools build` and a release smoke step); `docs/DEVELOPMENT.md` (new "Releases" section)

**Interfaces:**
- Consumes: everything from A1–A5.
- Produces (`build.py`): `@dataclass BuildResult(release_id: int | None, status: str, problems: list[str], gate: GateResult | None, embedded: int, reused_versions: int)`; `build_release(conn, source: ContentSource, embedder: Embedder, *, actor: str, note: str | None, override: str | None, today: date) -> BuildResult` — spec §6 steps 2–6 in order: load inputs → integrity (problems → status `"integrity_failed"`, nothing written) → compute versions (reuse by `(record_id, content_hash, model)`, vectors by `(text_hash, model)`, embed the rest **before** `create_draft`) → `create_draft` → `run_gold` per live service via `store.search` on the draft → `gate` → `set_gate_result` (`validated` / `rejected`, or `validated` with override) → audit rows (`release.build`, then `release.validate` / `release.reject` / `release.override`). `created_by` and actor = `actor`.
- Produces (`commands.py`): `publish(conn, release_id, actor, allow_fake: bool)` (refuses `fake-*` models unless `allow_fake`); `rollback(conn, actor) -> int`; `list_releases(conn) -> list[dict]`; `show(conn, release_id) -> dict` (release row without the large JSON blobs, plus `diff_vs_active: {"added": [...], "removed": [...], "changed": [...]}` by `record_id`/`content_hash`); `eval_release(conn, release_id, embedder) -> dict` (refuses when `embedder.model_id != release.embedding_model`).
- Produces CLI (`gsn.cli`): `gsn release build [--commit REF] [--note TEXT] [--override REASON]`, `gsn release publish ID [--allow-fake]`, `gsn release rollback`, `gsn release list`, `gsn release show ID`, `gsn eval --release ID [--json]`. Repository from `GSN_REPO_GIT` (default `/repo.git`); embedder from `get_embedder(os.environ)`; actor from `GSN_ACTOR` (default `owner`). Human-readable summary on stdout (JSON with `--json` for eval, always JSON for `show`); the first line of `build` output is exactly `release_id=<n> status=<status>` (scripts parse it); exit codes per Global Constraints. Build refuses to start when `secret_paths_in_history()` is non-empty (exit 2, lists the paths).
- Produces `engine/Dockerfile` target `release` (`FROM runtime AS release`): `USER root`, `apt-get install --no-install-recommends git` (clean apt lists), `mkdir /models && chown gsn:gsn /models`, `ENV HF_HOME=/models HF_HUB_OFFLINE=1 GIT_CONFIG_NOSYSTEM=1 GSN_REPO_GIT=/repo.git`, `USER gsn`, `ENTRYPOINT ["gsn"]`, `CMD ["--help"]`.
- Produces compose service `release`: `build: {context: ./engine, target: release}`, `image: gsn-release:dev`, `profiles: ["tools"]`, `restart: "no"`, env `GSN_DB_HOST/PORT/NAME`, `GSN_DB_USER=gsn_worker`, `GSN_DB_PASSWORD=${GSN_DB_WORKER_PASSWORD:?run python deploy/make_env.py first}`, `GSN_ACTOR=${GSN_ACTOR:-owner}`; volumes `gsn-models:/models`, `${GSN_REPO_GIT:-./.git}:/repo.git:ro`; `depends_on: {migrate: {condition: service_completed_successfully}}`; no `ports`. Top-level volume `gsn-models`.
- `test_compose_exposure.py`: `ONE_SHOT_SERVICES = {"migrate", "release"}`; `ALLOWED_DB_PASSWORDS["release"] = {"GSN_DB_WORKER_PASSWORD"}`; new test: `release` has `profiles == ["tools"]`, no `ports`, exactly the two volumes above; built-image set gains `gsn-release:dev`.
- CI stack job: build with `docker compose --profile tools build`; after the itest step add **Release smoke (fake embedder)**: `docker compose --profile tools run --rm -e GSN_EMBEDDER=fake release release build --commit HEAD --override "CI smoke test (fake embedder)" | tee build.log`, read the id with `id=$(sed -n 's/^release_id=\([0-9]*\) .*/\1/p' build.log)`, then `… release release publish "$id" --allow-fake`, then `… release release list`. (Entry point is `gsn`, so the first `release` is the service, the second the subcommand. The itest step runs first, on the fresh database.)

- [ ] **Step 1: Write the failing tests**

```python
# engine/tests/test_cli_release.py (argparse + exit-code mapping with build_release/commands monkeypatched)
def test_build_exit_codes_validated_rejected_integrity():   # 0 / 3 / 4
def test_build_refuses_when_history_has_secret_paths():     # exit 2, path listed
def test_bad_ref_is_exit_2_with_message():                  # Review Focus (GitSourceError)
def test_eval_refuses_model_mismatch():                     # exit 2
def test_publish_requires_allow_fake_for_fake_models():     # Review Focus
# tests/integration/test_release_build.py (DictSource fixture: 2 services × 3 records; gold queries whose text equals a record's text, so FakeEmbedder finds it at sim 1.0)
def test_clean_build_is_validated_and_audited(connect):
def test_failing_gold_query_rejects_unless_known_or_overridden(connect):   # three builds: rejected; known_failures → validated; --override → validated + override_reason + release.override audit row
def test_integrity_problem_writes_nothing(connect):          # release count unchanged
def test_rebuild_same_commit_reuses_all_versions_and_vectors(connect):     # Review Focus: second BuildResult.embedded == 0, reused == all, new release_id
def test_changed_provenance_field_new_version_same_vector(connect):        # fetched_at changes → new version_id, embedded == 0
def test_show_diff_vs_active(connect):                       # added / removed / changed
# deploy/tests/test_compose_exposure.py — updated constants + the new release-service test
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest engine/tests/test_cli_release.py deploy/tests -q` → FAIL; stack cycle → `test_release_build.py` FAIL.
- [ ] **Step 3: Implement `build.py`, `commands.py`, the CLI, the Dockerfile target, the compose service; update the exposure test, CI and DEVELOPMENT.md** ("Releases": what a release is; `docker compose --profile tools run --rm release release build|publish|rollback|list|show`; exit codes; that only committed content is read; that `GSN_EMBEDDER=fake` is for tests only).
- [ ] **Step 4: Verify**
  - `.venv\Scripts\python -m pytest engine/tests deploy/tests tools/tests -q` → pass
  - stack cycle → pass
  - real-repo smoke on the PC: `docker compose --profile tools run --rm -e GSN_EMBEDDER=fake release release build --commit HEAD --override "local smoke"` → exit 0, summary shows 4 services / 293 records; `… release release list` shows it. Then `docker compose down -v` (removes the smoke data).
- [ ] **Step 5: Commit**

```bash
git add engine/src/gsn/release/build.py engine/src/gsn/release/commands.py engine/src/gsn/cli.py engine/Dockerfile compose.yaml deploy/tests/test_compose_exposure.py .github/workflows/ci.yml docs/DEVELOPMENT.md engine/tests/test_cli_release.py tests/integration/test_release_build.py
git commit -m "feat(m1): gsn release build/publish/rollback/list/show, release image and compose service"
```

**Stage A review gate:** Opus reviews the stage diff, re-runs host tests + stack cycle + the PC smoke, and pushes the branch for CI before Stage B starts.

---

## Stage B — tamper protection

### Task B1: Audit hash chain, `audit_verify()` and the sequence grant

**Files:**
- Create: `db/migrations/0003_audit_chain.sql`, `engine/src/gsn/audit.py`, `tests/integration/test_audit_chain.py`, `engine/tests/test_cli_audit.py`
- Modify: `engine/src/gsn/cli.py` (`gsn audit verify`), `tests/integration/test_grants.py`

**Interfaces:**
- Produces (SQL): table `audit_chain(singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), last_seq bigint NOT NULL, last_hash bytea)` seeded `(true, 0, NULL)`, no grants to any login role. `audit_log` gains `chain_seq bigint UNIQUE`, `prev_hash bytea`, `row_hash bytea`, `hash_version smallint`. Trigger `audit_log_chain_insert` → function `audit_log_chain()` (`SECURITY DEFINER`, owner `gsn_migrate`, pinned search_path) fired `BEFORE INSERT FOR EACH ROW`: `SELECT … FROM public.audit_chain WHERE singleton FOR UPDATE`; `NEW.chain_seq = last_seq + 1`; `NEW.prev_hash = last_hash`; `NEW.hash_version = 1`; `NEW.row_hash = sha256(convert_to(jsonb_build_array(encode(prev_hash,'hex'), chain_seq, id, to_char(at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), actor, action, target, details)::text, 'UTF8'))` (JSON `null` for a NULL `prev_hash`/`target`); update the head. `audit_verify() RETURNS TABLE(ok boolean, rows_checked bigint, first_bad_seq bigint)` (`SECURITY DEFINER`): walks `chain_seq` 1..n (rows with NULL `chain_seq` are pre-chain and skipped), recomputes each hash with the same expression, checks `prev_hash` linkage, contiguity, and that the head equals the last row. EXECUTE on `audit_verify()` to `gsn_worker` and `gsn_web`. `REVOKE USAGE ON SEQUENCE audit_log_id_seq FROM gsn_web, gsn_worker`.
- Produces (`audit.py`): `@dataclass(frozen=True) AuditStatus(ok: bool, rows_checked: int, first_bad_seq: int | None)`; `verify(conn) -> AuditStatus`. CLI `gsn audit verify` → exit 0 when ok, 1 otherwise, one summary line.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_audit_chain.py
def test_worker_and_web_inserts_are_chained(connect):        # chain_seq consecutive, prev_hash links, row_hash 32 bytes
def test_callers_cannot_set_hash_columns(connect):           # INSERT naming chain_seq/row_hash as web → InsufficientPrivilege
def test_concurrent_inserts_chain_in_lock_order(connect):    # conn A: BEGIN; insert (holds head lock); thread B inserts (blocks); A commits; B completes → verify ok, 2 consecutive chain_seq
def test_verify_ok_then_detects_tampering(connect):          # verify ok; then in a rolled-back transaction as gsn_migrate: DISABLE TRIGGER audit_log_no_update_delete, UPDATE one chained row's action → audit_verify() gives ok false, first_bad_seq = that row's chain_seq
def test_inserts_work_without_sequence_usage(connect):       # web and worker still insert after the REVOKE
# engine/tests/test_cli_audit.py
def test_audit_verify_exit_codes():                          # monkeypatched verify → 0 / 1
```

(After Task B2 the watchdog rejects the `DISABLE TRIGGER` used by the tampering test; B2 changes that test to expect the watchdog's error first and keeps the verify-detects-tampering check by tampering inside `audit_chain`'s head row instead — see B2.)

- [ ] **Step 2: Run to verify failure** — stack cycle → FAIL (`column "chain_seq" does not exist`).
- [ ] **Step 3: Write the migration, `audit.py`, the CLI subcommand; update `test_grants.py` (no sequence USAGE expected).**
- [ ] **Step 4: Verify** — `docker compose build web`, stack cycle, host tests → pass.
- [ ] **Step 5: Commit**

```bash
git add db/migrations/0003_audit_chain.sql engine/src/gsn/audit.py engine/src/gsn/cli.py tests/integration/test_audit_chain.py tests/integration/test_grants.py engine/tests/test_cli_audit.py
git commit -m "feat(m1): audit_log hash chain, audit_verify and gsn audit verify"
```

---

### Task B2: Guard watchdog, database ownership and registrations

**Files:**
- Create: `db/init/20-guard.sh` (mode 100755), `db/migrations/0004_guard_registration.sql`, `tests/integration/test_guard.py`
- Modify: `tests/integration/test_audit_chain.py` (tampering test, see B1 note), `tests/integration/test_grants.py`, `docs/DEVELOPMENT.md` (watchdog + break-glass note; "recreate once with `docker compose down -v`")

**Interfaces:**
- Produces (`20-guard.sh`, runs as `$POSTGRES_USER` in database `gsn`, after `10-roles.sh`):
  - `ALTER DATABASE gsn OWNER TO "$POSTGRES_USER"` — this is how spec §8.3's "revoke CREATE on `gsn` from `gsn_migrate`" is done: a plain REVOKE would not hold, because an owner can re-grant itself; `REVOKE TEMPORARY ON DATABASE gsn FROM PUBLIC`; in database `postgres`: `REVOKE CONNECT ON DATABASE postgres FROM PUBLIC`.
  - Schema `gsn_guard` (owner superuser; `USAGE` to `gsn_migrate` only). Tables `gsn_guard.protected_tables(table_name text PRIMARY KEY, columns jsonb NOT NULL)`, `gsn_guard.protected_triggers(table_name text, trigger_name text, PRIMARY KEY (table_name, trigger_name))`, `gsn_guard.protected_functions(signature text PRIMARY KEY, fingerprint text NOT NULL)`.
  - `gsn_guard.protect_table(p_table text, p_triggers text[])` and `gsn_guard.protect_function(p_signature text)` — `SECURITY DEFINER`, pinned search_path, EXECUTE to `gsn_migrate` only; `p_table` must be schema-qualified and resolve (`to_regclass`); records columns as `[{"name","type"}]` (`format_type`, `attnum > 0`, not dropped); every listed trigger must exist with `tgenabled IN ('O','A')`; re-registering raises `'<name> is already protected'`. Function fingerprint = `md5(prosrc || coalesce(proconfig::text,'') || prosecdef::text || proowner::text || provolatile::text)` of `to_regprocedure(p_signature)`.
  - `gsn_guard.check()` (`RETURNS event_trigger`, `SECURITY DEFINER`, pinned search_path) on `ddl_command_end`, and `gsn_guard.check_drop()` on `sql_drop`. Violations (each raises `'gsn_guard: <what>'`, which rolls the DDL back): a protected table missing under its name; a registered column missing or with another type; a registered trigger missing or `tgenabled NOT IN ('O','A')`; a registered function missing or with a changed fingerprint; any rule other than `_RETURN` on a protected table (`pg_rewrite`); `relrowsecurity` on a protected table; any dropped object (`pg_event_trigger_dropped_objects()`) that is a protected table, registered trigger or registered function. Empty registry → return immediately.
- Produces (`0004_guard_registration.sql`): first a preflight `DO` block raising `'database predates the M1 guard (db/init/20-guard.sh); recreate it: docker compose down -v'` when `to_regnamespace('gsn_guard') IS NULL`; then `protect_table` for `public.audit_log` (triggers `audit_log_no_update_delete`, `audit_log_no_truncate`, `audit_log_chain_insert`), and — each with its `<table>_guard_row` and `<table>_guard_truncate` triggers — `public.record_versions`, `public.publications`, `public.services`, `public.records`, `public.releases`, `public.release_members`, `public.release_services`, `public.active_release`; `protect_function` for `public.audit_log_reject_change()`, `public.audit_log_chain()`, `public.audit_verify()`, `public.gsn_reject_change()`, `public.releases_guard()`, `public.release_children_guard()`, `public.active_release_guard()`, `public.publish_release(bigint,text)`, `public.rollback_release(text)`.

- [ ] **Step 1: Write the failing tests** (`test_guard.py`; every attempted change runs as `gsn_migrate` inside `conn.transaction(force_rollback=True)` so a test never alters the shared database)

```python
@pytest.mark.parametrize("ddl", [
    "DROP TRIGGER audit_log_no_update_delete ON audit_log",
    "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete",
    "ALTER TABLE audit_log ENABLE REPLICA TRIGGER audit_log_no_update_delete",
    "ALTER TABLE audit_log RENAME TO audit_log_old",
    "ALTER TABLE audit_log DROP COLUMN row_hash",
    "ALTER TABLE audit_log RENAME COLUMN actor TO who",
    "ALTER TABLE audit_log ALTER COLUMN target TYPE varchar(50)",
    "CREATE OR REPLACE FUNCTION audit_log_reject_change() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN NEW; END$$",
    "ALTER FUNCTION publish_release(bigint, text) SECURITY INVOKER",
    "DROP FUNCTION rollback_release(text) CASCADE",
    "DROP TABLE publications",
    "CREATE RULE keep ON DELETE TO record_versions DO INSTEAD NOTHING",
    "ALTER TABLE releases ENABLE ROW LEVEL SECURITY",
])
def test_watchdog_rejects(connect, ddl):                     # raises, message starts with "gsn_guard:"
def test_adding_a_column_is_allowed(connect):                # ALTER TABLE audit_log ADD COLUMN note text → succeeds (rolled back)
def test_unrelated_ddl_is_allowed(connect):                  # CREATE TABLE scratch_<uuid> (id int) → succeeds (rolled back)
def test_registration_is_not_callable_by_other_roles(connect):   # web/worker/engine: EXECUTE gsn_guard.protect_table → InsufficientPrivilege
def test_migrate_cannot_create_schemas_or_temp_tables(connect):  # CREATE SCHEMA x → InsufficientPrivilege; CREATE TEMP TABLE as web → InsufficientPrivilege
def test_public_cannot_connect_to_postgres_db(connect):      # has_database_privilege('gsn_web','postgres','CONNECT') is false
def test_the_registry_lists_every_protected_object(connect): # as migrate: SELECT from gsn_guard tables → 9 tables, 9 functions
```

- [ ] **Step 2: Run to verify failure** — `docker compose down -v` then stack cycle → `test_guard.py` FAIL (DDL succeeds).
- [ ] **Step 3: Write `20-guard.sh` and `0004_guard_registration.sql`; adjust `test_audit_chain.py` (tampering: expect the watchdog to refuse `DISABLE TRIGGER`; prove `audit_verify` detection by, inside a rolled-back transaction as `gsn_migrate`, inserting a row and then rewriting `audit_chain.last_hash` — verify reports the head mismatch) and `test_grants.py` (database owner is the superuser; no login role has CREATE on the database).**
- [ ] **Step 4: Verify** — `docker compose down -v`, `docker compose build web`, stack cycle → all pass. Then confirm the preflight once: move `db/init/20-guard.sh` out of the folder, `docker compose down -v && docker compose up -d postgres`, move it back, `docker compose run --rm migrate` → fails with the preflight text; finish with `docker compose down -v`.
- [ ] **Step 5: Commit**

```bash
git add db/init/20-guard.sh db/migrations/0004_guard_registration.sql tests/integration/test_guard.py tests/integration/test_audit_chain.py tests/integration/test_grants.py docs/DEVELOPMENT.md
git update-index --chmod=+x db/init/20-guard.sh
git commit -m "feat(m1): superuser-owned guard watchdog, registrations and database ownership"
```

---

### Task B3: M0 test hygiene

**Files:**
- Modify: `tests/integration/test_grants.py`, `tests/integration/conftest.py`, `deploy/tests/test_repo_hygiene.py`, `deploy/tests/test_compose_exposure.py`, `engine/pyproject.toml` (pytest options), `eval_epassport.py` (docstring lines 21–24 only), `docs/DEVELOPMENT.md` (test_grants wording)

**Interfaces:** none new. Items (from the M0 ledger, `.superpowers/sdd/2026-10-20-m0-foundations/progress.md` "Deferred by milestone — M1" and `deferred-minors.txt`):
1. Grants test made catalog-wide: no login role has any privilege on any table/view/sequence/function in `public` beyond the expected list (one table of expectations).
2. PUBLIC-ACL check asserts the ACL is non-NULL before checking it (a NULL ACL means owner defaults).
3. `current_database()` instead of the literal `'gsn'`.
4. `db/init/*.sh` are mode `100755` in git (`git ls-files -s`).
5. Every `${VAR:?…}` in `compose.yaml`/`compose.test.yaml` has a `change-me-*` entry in `.env.example`, and vice versa.
6. pytest `--import-mode=importlib` for `engine/tests` and `deploy/tests` so same-named test modules cannot collide.
7. `test_grants.py` docstring and DEVELOPMENT.md describe exactly what is covered.
8. `eval_epassport.py` docstring: the Dhaka-cantonment record's URL was verified in `1ae2e0d` and is no longer flagged.

- [ ] **Step 1: Write/adjust the tests for items 1–6** (each must fail against a deliberately broken copy: e.g. temporarily grant SELECT on `record_versions` to `gsn_engine` in a rolled-back transaction → catalog-wide test fails).
- [ ] **Step 2: Run** — host tests + stack cycle → new checks fail where the code is not yet compliant (if a check passes immediately, show the deliberate-break run instead).
- [ ] **Step 3: Make the fixes (items 1–8).**
- [ ] **Step 4: Verify** — host tests, stack cycle, `PYTHONIOENCODING=utf-8 python test_multiturn.py` → pass.
- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_grants.py tests/integration/conftest.py deploy/tests/test_repo_hygiene.py deploy/tests/test_compose_exposure.py engine/pyproject.toml eval_epassport.py docs/DEVELOPMENT.md
git commit -m "test(m1): M0 test hygiene items and stale eval docstring"
```

**Stage B review gate:** Opus reviews, re-runs everything from a fresh `docker compose down -v`, pushes for CI.

---

## Stage C — the real model

### Task C1: ML dependencies, release image, real embedder and `gsn models fetch`

**Files:**
- Create: `engine/constraints-ml.txt`
- Modify: `engine/pyproject.toml` (`[project.optional-dependencies] ml = ["sentence-transformers==5.6.0", "torch==2.13.0+cpu"]`), `engine/Dockerfile` (`release` target installs the ML set), `engine/src/gsn/embed.py`, `engine/src/gsn/cli.py` (`gsn models fetch`), `engine/tests/test_constraints.py`, `engine/tests/test_embed.py`, `.github/dependabot.yml` (pip `/engine`: ignore `torch` and `sentence-transformers`, comment "pinned to the ChromaDB baseline's versions until the M2 engine switch"), `.github/workflows/ci.yml` (audit `constraints-ml.txt` with the `+cpu` label stripped: `sed 's/+cpu//' engine/constraints-ml.txt > /tmp/ml.txt && python -m pip_audit -r /tmp/ml.txt --no-deps --disable-pip --progress-spinner off`), `docs/DEVELOPMENT.md` ("ML constraints" regeneration; "Model download")

**Interfaces:**
- `constraints-ml.txt`: every package the `ml` extra adds beyond `constraints.txt`, exact pins, generated inside `python:3.12.14-slim-bookworm` by the documented command (pip install `-c engine/constraints.txt` `--extra-index-url https://download.pytorch.org/whl/cpu` `"./engine[ml]"`, `pip freeze --exclude-editable`, drop names already in `constraints.txt`, sort case-insensitively, ASCII). Must include `torch==2.13.0+cpu`, `sentence-transformers==5.6.0`, `transformers==5.13.1`, `tokenizers==0.22.2`, `safetensors==0.8.0`, `numpy==2.5.1`, `huggingface-hub==1.23.0` (the PC's versions; if pip cannot satisfy one of them on Linux, stop and report).
- Dockerfile `release` stage adds: `pip install --no-deps --extra-index-url https://download.pytorch.org/whl/cpu -r /build/constraints-ml.txt && pip check` (bind-mounted build context as in `runtime`).
- `embed.py`: `MODEL_REPO = "BAAI/bge-m3"`, `MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"`, `MODEL_FILES` = the Global Constraints list; `SentenceTransformerEmbedder(cache_dir: str | None = None)` with `model_id = f"{MODEL_REPO}@{MODEL_REVISION}"`, lazy-imports `sentence_transformers`, loads with `revision=MODEL_REVISION`, `encode(..., normalize_embeddings=True, batch_size=16)`; a missing model gives `ModelMissingError("run: gsn models fetch")`. `fetch_model(cache_dir: str | None = None) -> str` uses `huggingface_hub.snapshot_download(MODEL_REPO, revision=MODEL_REVISION, allow_patterns=MODEL_FILES)` and returns the snapshot path; CLI `gsn models fetch` requires `HF_HUB_OFFLINE=0` and says so otherwise (exit 2).
- `test_constraints.py`: new tests — `constraints-ml.txt` lines are exact pins; the `ml` extra's direct pins match it; no package pinned in both files with different versions.

- [ ] **Step 1: Write the failing tests** (`test_constraints.py` additions; `test_embed.py`: `get_embedder({})` returns a `SentenceTransformerEmbedder` whose `model_id` is the pinned id without importing torch; `MODEL_FILES` equals the list).
- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest engine/tests -q` → FAIL.
- [ ] **Step 3: Generate `constraints-ml.txt` in the Linux container; implement; update Dockerfile, CLI, Dependabot, CI, docs.**
- [ ] **Step 4: Verify**
  - host tests pass; `docker compose --profile tools build release` succeeds (`pip check` clean); `docker image ls gsn-release:dev` size noted in the report.
  - `docker compose --profile tools run --rm -e HF_HUB_OFFLINE=0 release models fetch` → downloads once into `gsn-models` (≈ 2.3 GB).
  - **Early vector check (Opus):** embed three fixed sentences (one Bangla, one English, one Banglish) in the container and on the PC (global Python, `SentenceTransformer("BAAI/bge-m3")`, `normalize_embeddings=True`); every pair has cosine ≥ 0.9999. Record the numbers in the stage report.
- [ ] **Step 5: Commit**

```bash
git add engine/constraints-ml.txt engine/pyproject.toml engine/Dockerfile engine/src/gsn/embed.py engine/src/gsn/cli.py engine/tests/test_constraints.py engine/tests/test_embed.py .github/dependabot.yml .github/workflows/ci.yml docs/DEVELOPMENT.md
git commit -m "feat(m1): CPU torch + sentence-transformers release image, pinned bge-m3 embedder, gsn models fetch"
```

**Stage C review gate:** Opus reviews, confirms the early vector check, pushes for CI.

---

## Stage D — baseline, parity and release 1

### Task D1: ChromaDB baseline exporter and multi-service cases

**Files:**
- Create: `tools/m1/export_chroma_baseline.py`, `docs/reports/m1-parity/retrieve_cases.json`, `engine/tests/test_evals_baseline_replay.py`, `tools/tests/test_export_chroma_baseline.py`
- Later (Opus, Step 4): `docs/reports/m1-parity/chroma_baseline.json`, `docs/reports/m1-parity/chroma_vectors.json.gz`, `data/release_thresholds.json`

**Interfaces:**
- `export_chroma_baseline.py` (runs with the **global** Python that has chromadb; imports the legacy root modules `answer`, `eval_nid`, `eval_epassport`, `eval_ereturn`, `eval_bdris`; argument `--out DIR`):
  1. Freshness: for each of the four collections, `{id: document}` must equal `{record["id"]: record["text"]}` of the committed corpus at `HEAD` (read via `git show HEAD:<path>`); on any difference print the differing IDs and exit 1.
  2. Environment: versions of `torch`, `sentence-transformers`, `transformers`, `tokenizers`, `chromadb`, `numpy`; the model snapshot path and weight file actually loaded; `git rev-parse HEAD`.
  3. Vectors: every stored embedding → `chroma_vectors.json.gz` (`{record_id: [floats]}`).
  4. Gold: for each gold query, `collection.query` top-k with unrounded `sim = 1 - distance`, per-hit `category`/`subcategory` metadata, and the legacy verdict from that script's own `eval_one` (e-Return: also record `subcategory_available(collection)`).
  5. Retrieve: `answer.retrieve(q, None, model, colls, queries=[q], services=None)` and `…services=[svc]` for every gold query, plus every case in `retrieve_cases.json`; record selected hits (id, service, unrounded sim) in order.
  Output `chroma_baseline.json` = `{"commit", "environment", "documents": {record_id: text_hash}, "gold": [...], "retrieve": [...]}` (`text_hash` as in `gsn.hashing`).
- `retrieve_cases.json`: 12 cases `{"id", "queries": [2–3 strings], "services": [...]}` — 4 over two services, 4 over three, 4 over all four; queries are Bangla/English rewrites of existing gold queries (copied, not invented facts), e.g. `["জন্ম নিবন্ধন সংশোধন", "birth registration correction"]` over `["bdris", "nid"]`.
- Pure helpers in the exporter are unit-tested on the host without chromadb (freshness diff, JSON shaping).
- `test_evals_baseline_replay.py`: for every gold entry in the committed baseline, rebuild `Hit`s from the recorded top-k (ids, sims, metadata) and assert `gsn.evals.judge(RULES[svc], gq, hits, top_k).verdict == legacy_verdict`. Skips with a clear reason until the baseline file exists.

- [ ] **Step 1: Write the failing tests** (`tools/tests/test_export_chroma_baseline.py`: freshness diff finds an added, a removed and a changed document; output shape keys; `engine/tests/test_evals_baseline_replay.py` with a 4-entry synthetic baseline fixture covering each rule).
- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest tools/tests/test_export_chroma_baseline.py engine/tests/test_evals_baseline_replay.py -q` → FAIL.
- [ ] **Step 3: Implement the exporter and write `retrieve_cases.json`.**
- [ ] **Step 4: Opus runs the baseline** — `$env:PYTHONIOENCODING="utf-8"; python tools/m1/export_chroma_baseline.py --out docs/reports/m1-parity` (global Python). Then: the replay test against the real baseline passes; fill `data/release_thresholds.json` `known_failures` with exactly the gold IDs whose legacy verdict is `FAIL`, each with reason `"Banglish query embedded raw by the eval (no transliteration); FAIL in the ChromaDB baseline of <commit>"` after confirming each is Banglish (`lang` in the gold file) — if any FAIL is not Banglish, stop and ask the owner.
- [ ] **Step 5: Commit**

```bash
git add tools/m1/export_chroma_baseline.py tools/tests/test_export_chroma_baseline.py engine/tests/test_evals_baseline_replay.py docs/reports/m1-parity/retrieve_cases.json docs/reports/m1-parity/chroma_baseline.json docs/reports/m1-parity/chroma_vectors.json.gz data/release_thresholds.json
git commit -m "feat(m1): ChromaDB baseline (gold, retrieve cases, stored vectors) and known failures"
```

---

### Task D2: Parity comparison and `gsn parity`

**Files:**
- Create: `engine/src/gsn/parity.py`, `engine/tests/test_parity.py`
- Modify: `engine/src/gsn/cli.py`

**Interfaces:**
- Consumes: `store.search`, `store.stored_vectors`, `retrieval.retrieve`, `evals.run_gold`/`judge`, `GitSource`, real embedder.
- Produces: `TOLERANCE = 0.001`, `VECTOR_FLOOR = 0.9999`; `compare_vectors(baseline: Mapping[str, list[float]], current: Mapping[str, list[float]]) -> list[str]` (problems: missing/extra IDs, cosine < floor); `compare_ranked(expected: list[tuple[str, float]], actual: list[tuple[str, float]], thresholds: Iterable[float]) -> RankedDiff(ok: bool, problems: list[str], tie_swaps: list[str], near_threshold: list[str])` — same ID set; each sim within `TOLERANCE`; an order difference is a tie swap when the swapped pair's baseline sims differ by `< TOLERANCE`, otherwise a problem; any sim within `TOLERANCE` of a threshold is listed in `near_threshold`; `run_parity(conn, release_id, source: ContentSource, embedder) -> dict` (reads `docs/reports/m1-parity/chroma_baseline.json` and the vectors file **from `source`**, which is opened at the release's `source_commit`; first checks that every member's `text_hash` equals the baseline's `documents` entry and that the ID sets match — the baseline may have been recorded on an earlier commit, but the texts must be identical); report `{"ok", "vectors": [...], "gold": [...per query: verdict_expected, verdict_actual, diff], "retrieve": [...], "tie_swaps", "near_threshold"}`. CLI `gsn parity --release ID` prints the JSON report to stdout; exit 0 ok, 5 not ok.
- Thresholds passed to `compare_ranked`: gold → `[rule.ceiling, gq.get("min_similarity")]`; retrieve → `[SERVICE_MIN_SIM, GROUNDING_FLOOR]`.

- [ ] **Step 1: Write the failing tests** (`test_parity.py`, synthetic data only)

```python
def test_identical_results_pass():
def test_sim_drift_beyond_tolerance_fails():                 # 0.0011 → problem; 0.0009 → ok
def test_order_swap_within_tolerance_is_a_tie_swap_not_a_failure():
def test_order_swap_beyond_tolerance_fails():
def test_different_id_sets_fail():
def test_near_threshold_values_are_listed():                 # sim 0.4005 vs SERVICE_MIN_SIM 0.40
def test_vector_check_reports_missing_and_low_cosine():
def test_verdict_mismatch_fails_even_if_hits_match():
def test_text_hash_mismatch_with_baseline_documents_fails_first():
```

- [ ] **Step 2: Run to verify failure** — `.venv\Scripts\python -m pytest engine/tests/test_parity.py -q` → FAIL.
- [ ] **Step 3: Implement `parity.py` and the CLI subcommand.**
- [ ] **Step 4: Verify** — host tests pass; `docker compose --profile tools build release`.
- [ ] **Step 5: Commit**

```bash
git add engine/src/gsn/parity.py engine/tests/test_parity.py engine/src/gsn/cli.py
git commit -m "feat(m1): parity comparison and gsn parity"
```

---

### Task D3: Real-model eval workflow

**Files:**
- Create: `.github/workflows/evals.yml`
- Modify: `docs/DEVELOPMENT.md` (what the workflow does, how to run it by hand)

**Interfaces:**
- Triggers: `workflow_dispatch`; `push` to `master` and `pull_request`, both with `paths:` `engine/src/gsn/embed.py`, `engine/src/gsn/retrieval.py`, `engine/src/gsn/evals.py`, `engine/src/gsn/release/**`, `engine/constraints-ml.txt`, `data/**`, `*_gold_queries.json`, `concept_registry.json`, `.github/workflows/evals.yml`.
- Job (ubuntu-24.04, timeout 60): checkout (`persist-credentials: false`, `fetch-depth: 0` so the secret-path preflight sees the whole history); `actions/cache` for `~/gsn-models` keyed `bge-m3-5617a9f61b028005a4858fdac845db406aefb181`; `sudo install -d -o 10001 -g 10001 ~/gsn-models`; `python deploy/make_env.py`; `docker compose --profile tools build`; `docker compose up -d postgres` + `docker compose run --rm migrate`; model fetch only on cache miss (`… -e HF_HUB_OFFLINE=0 release models fetch`); `release release build --commit HEAD` must exit 0 (validated: only known failures); `release eval --release 1 --json` uploaded as an artifact; tear down `docker compose down -v`. The models directory is passed with an override `-v ~/gsn-models:/models` on each `run` (do not change `compose.yaml`).
- Actions pinned by SHA like `ci.yml`.

- [ ] **Step 1: Write the workflow.** (No local unit test; `deploy/tests/test_repo_hygiene.py` gains a check that every `uses:` in `.github/workflows/*.yml` is pinned to a 40-hex SHA — write that test first and see it fail on a deliberately unpinned line, then fix.)
- [ ] **Step 2: Run** — `.venv\Scripts\python -m pytest deploy/tests -q` → pass.
- [ ] **Step 3: Push the branch and trigger the workflow by hand** (`gh workflow run evals.yml --ref feat/m1-data-in-postgres`), watch it with a single `gh run watch <id> --exit-status` in the background.
- [ ] **Step 4: Verify** — the run is green and its artifact's verdict counts equal the baseline's (expected 60 PASS, 6 PASS (warn), 3 FAIL — all known — and 5 OBSERVE across 74, per CLAUDE.md).
- [ ] **Step 5: Commit**

```bash
git add .github/workflows/evals.yml deploy/tests/test_repo_hygiene.py docs/DEVELOPMENT.md
git commit -m "ci(m1): real-model eval workflow"
```

---

### Task D4: Release 1, parity run and documentation (Opus)

**Files:**
- Create: `docs/reports/<date of the run>-m1-parity.md`
- Modify: `docs/PROJECT_STATUS.md`, `CLAUDE.md` (layout table: new modules, `release` service, release commands, ML-only-in-image rule), `docs/DEVELOPMENT.md` (final pass), `docs/superpowers/plans/2026-10-22-m1-data-in-postgres.md` (as-built notes)

- [ ] **Step 1:** On the merged-to-date branch tip C (contains the baseline and all code): `docker compose down -v`, rebuild, `up -d`, `release models fetch` if the volume is new.
- [ ] **Step 2:** `docker compose --profile tools run --rm release release build --commit <C>` → exit 0, `validated`, gate counts equal the baseline's.
- [ ] **Step 3:** `… release parity --release 1 > %TEMP%\m1-parity.json` → exit 0. Any problem: stop, investigate, explain; never adjust tolerances to pass.
- [ ] **Step 4:** `… release release publish 1`; `… release audit verify` → ok; `… release release show 1`.
- [ ] **Step 5:** Routing tests on C: `python test_multiturn.py`, `python test_understand.py`, `python test_turn_routing.py`, `python test_eval_conversations.py` (global Python, `PYTHONIOENCODING=utf-8`); confirm `registry_sha256` of release 1 equals `sha256(git show C:concept_registry.json)`.
- [ ] **Step 6:** Write the report (environment, vector check, gold and retrieve results, tie swaps, near-threshold list, timings, image/model sizes), update the docs, commit with explicit paths, push the branch, run CI, then ask the owner before merging to `master`.

---

## Done when (spec §1)

1. Release 1 built from commit C (baseline + all M1 code) is `validated` and published.
2. `gsn parity --release 1` exits 0: vectors ≥ 0.9999 cosine, identical verdicts, results within 0.001 with only reported tie swaps.
3. Routing tests pass on C; release 1's `registry_sha256` matches the committed file.
4. Stack integration tests (schema, store, build, audit chain, guard, grants) and CI (including the real-model workflow) are green.
5. The parity report and status updates are committed after the run.
