# M1 — Data in PostgreSQL: design

Status: approved in conversation 2026-10-22 (owner review of this document pending).
Parent: `2026-10-20-system-architecture-design.md` (§6 data model and releases, §10.2 gates, §11
milestones). This document fixes the details M1 needs; where it refines the parent spec, §11 lists
the change.

## 1. Goal and finish line

Move the knowledge base from ChromaDB into PostgreSQL + pgvector as **immutable, versioned
releases**, built and published with a `gsn release` command, and prove that retrieval from a
release gives the same results as today's ChromaDB collections.

**Done when** (all on one commit):

1. Release 1, built from that commit, passes its gates and is published.
2. The parity comparison (§7) passes for all 74 gold queries: identical verdicts, identical top-k
   records in the same order, similarities within 0.001, both for the per-service gold queries and
   for the multi-service `retrieve()` logic.
3. `test_multiturn.py`, `test_understand.py`, `test_turn_routing.py` and
   `test_eval_conversations.py` pass, and the registry stored in release 1 is byte-identical to the
   committed `concept_registry.json`.
4. The tamper protections (§8) are in place and tested; CI is green.

**Nothing changes for users in M1.** `answer.py`, `app.py` and the four legacy eval scripts keep
using ChromaDB. The chatbot switches to PostgreSQL in M2 (engine service); ChromaDB and
`data/vectordb/` are deleted only after that switch.

## 2. Decisions (owner, 2026-10-22)

| Topic | Decision |
|---|---|
| Approach | New modules in the `gsn` package, run in a one-off container from the engine/worker image (approach A). Not a Windows-host tool; not a ChromaDB-shaped adapter in the legacy code. |
| `verified_at` | NID ← `fetched_at`; e-Passport ← `last_crawled`; e-Return and BDRIS ← none (link shown without a date; they form the re-check list). `last_updated` is the source's own publication date: stored in the record, never used as `verified_at`. |
| Services in M1 releases | NID, e-Passport, e-Return, BDRIS (293 records), all `live`. Visa joins through the data workflow once it has a gold set. |
| Content source | Committed files at a given commit only, never the working tree. |
| Release gate | Integrity checks + retrieval evals. Generation eval M7; routing fixtures move with the routing code in M2; publish notifications M6. |

## 3. Data model

New migrations (`db/migrations/0002_…` onwards; applied migrations are never edited).

| Table | Columns (key ones) | Mutability |
|---|---|---|
| `services` | `service_id` (PK: `nid`, `bdris`, `epassport`, `ereturn`), `name_bn`, `name_en` | insert only |
| `records` | `record_id` (PK, today's corpus IDs — unique across all corpora), `service_id` FK, `created_at` | insert only |
| `record_versions` | `version_id` (identity PK), `record_id` FK, `content_hash`, `record` (JSONB: the original record, every field, verbatim), `embed_text` (exactly what is embedded: the record's `text`), `category`, `subcategory`, `heading`, `lang`, `source_url`, `url_kind`, `official`, `source_verified`, `verified_at` (date, nullable), `embedding` (`vector(1024)`), `embedding_model`, `source_commit`, `created_at`, `created_by`, `change_note` | **append-only** (UPDATE/DELETE/TRUNCATE rejected) |
| `releases` | `release_id` (identity PK, small integers the owner types), `status` (`draft` → `validated` \| `rejected`), `source_commit`, `manifest` (JSONB), `registry` (JSONB), `thresholds` (JSONB), `gate_report` (JSONB), `embedding_model`, `note`, `override_reason`, `created_at`, `created_by` | only `status` (+ `gate_report`, `override_reason`) may change, once, out of `draft` |
| `release_members` | (`release_id`, `version_id`) PK | insert/delete only while the release is `draft` |
| `release_services` | (`release_id`, `service_id`) PK, `status` (`live` \| `hidden`) | frozen with the release |
| `active_release` | single row (`singleton` boolean PK), `release_id` FK, `switched_at` | updated only by publish/rollback; must point at a `validated` release |
| `publications` | `publication_id`, `release_id`, `previous_release_id`, `action` (`publish` \| `rollback`), `at`, `actor` | **append-only** |

- `content_hash` = SHA-256 of the record's canonical JSON (sorted keys, UTF-8, no insignificant
  whitespace). A build reuses an existing version when (`record_id`, `content_hash`,
  `embedding_model`) already exists, so unchanged records are neither stored nor embedded twice.
- `url_kind` is computed at build time by a port of `answer.url_kind()` (same markers; unit tests
  pin identical output on every corpus URL).
- `source_verified` = `not record.get("source_url_unverified", False)`.
- `verified_at` comes from the manifest's per-service field (§4.1); a value that is not a valid
  date, or is in the future, fails the build.
- **No vector index**: retrieval is an exact scan (293 rows, milliseconds). An HNSW index is added
  when the corpus reaches tens of thousands of records.
- **Retrieval query** (per service, per query embedding): members of the given release joined to
  `record_versions`, filtered by `service_id`, ordered by cosine distance (`<=>`), limited to k.
  Similarity = 1 − distance, as ChromaDB reports it.

### 3.1 Database roles

| Role | M1 grants |
|---|---|
| `gsn_worker` | SELECT/INSERT on `services`, `records`, `record_versions`, `releases`, `release_members`, `release_services`, `publications`; UPDATE (status, gate_report, override_reason) on `releases`; DELETE on `release_members` (draft only, enforced by trigger); UPDATE on `active_release`; existing column-level INSERT on `audit_log`; EXECUTE on `audit_verify()` |
| `gsn_engine` | SELECT on `services`, `records`, `record_versions`, `releases`, `release_members`, `release_services`, `active_release` (used from M2) |
| `gsn_web` | nothing new (release list and Rollback arrive with the admin panel in M6) |
| `gsn_migrate` | owns the tables; registers protected objects (§8) |

## 4. Committed inputs

### 4.1 `data/release_manifest.json`

One entry per service: `service_id`, `name_bn`, `name_en`, `status` (`live` | `hidden`),
`corpus` (path), `gold` (path), `verified_at_field` (`fetched_at` | `last_crawled` | `null`).
M1 contents: the four live services, corpora `data/chunks/{nid,epassport,ereturn,bdris}.jsonl`
(BDRIS: the merged file the embed script uses), gold files `*_gold_queries.json`.

Adding a service later = corpus → gold queries → registry entry → manifest entry → release.

### 4.2 `data/release_thresholds.json`

`known_failures`: gold query IDs allowed to FAIL, each with a written reason. M1 contents: the
three Banglish gold queries that fail today because the eval embeds them raw (one each in NID,
e-Return, BDRIS). Editing this file is a visible commit.

## 5. Commands

Run in the one-off `release` service (§9.2): `docker compose --profile tools run --rm release …`.

```
gsn models fetch                                  download bge-m3 (pinned revision) into the model volume
gsn release build [--commit <sha>] [--note TEXT] [--override REASON]
gsn release publish <release_id>
gsn release rollback
gsn release list
gsn release show <release_id>                     gate report; records added/changed/removed vs live
gsn eval --release <release_id> [--json PATH]     gold queries against any release
gsn parity --release <release_id> --baseline PATH compare with the ChromaDB baseline (§7)
gsn audit verify                                  check the audit_log hash chain (§8.2)
```

Every build, publish, rollback and override writes an `audit_log` row (actor from `GSN_ACTOR`,
default `owner`).

## 6. `release build`

1. **Resolve** `--commit` (default `HEAD`) to a full SHA in the read-only repository
   (`/repo.git`). Read the manifest, thresholds, registry, corpora and gold files **at that commit**
   with git plumbing (`git cat-file`), hooks and fsmonitor disabled.
2. **Integrity checks** — any failure stops the build before anything is written, with the full
   list of problems:
   - every listed file exists at the commit and parses; each `live` service has a corpus, a gold
     file and a registry entry;
   - every record has `id`, non-empty `text`, non-empty `source_url`;
   - no `TODO(verify)` anywhere in a record;
   - record IDs unique across the release; no two records with identical `text`;
   - a record whose `lang` is `bn`, `bn+en` or `mixed` contains Bengali script;
   - the `verified_at` field, where mapped, is a valid date not in the future;
   - a record's service never changes from the one already stored for its `record_id`.
3. **Draft**: insert the release (`draft`), its `release_services`, and its members — reusing
   versions where possible, embedding only new or changed records.
4. **Evals**: run every gold query of every live service against the draft (§7.1 harness).
5. **Gate**: any FAIL not listed in `known_failures` → `rejected`, unless `--override REASON`
   (reason stored on the release and in `audit_log`). WARN and OBSERVE never block. Otherwise
   `validated`. The gate report (integrity summary, per-query verdicts, counts) is stored.

**Publish** refuses anything not `validated`; in one transaction it switches `active_release`,
appends a `publications` row and an `audit_log` row; publishing the release that is already
active is refused, and any `validated` release (including an older one) may be published by ID.
**Rollback** undoes the most recent switch: it makes the latest publication's
`previous_release_id` active again, recorded the same way (so a second rollback re-applies the
release the first one undid). To go further back, publish the older release by ID. With no earlier
publication, rollback refuses.

## 7. Evals and parity

### 7.1 Eval harness (`gsn.evals`)

One harness for the four gold files, reproducing each legacy script's verdict rules exactly
(PASS / PASS (warn) / OBSERVE / FAIL; including e-Return's subcategory rule and BDRIS's rules).
Gold queries are embedded raw, as the legacy scripts do. Used by `release build`, `gsn eval` and
`gsn parity`.

### 7.2 Retrieval module (`gsn.retrieval`)

Port of `answer.retrieve()` over a release: same constants (`CANDIDATE_N` 8, `PER_SERVICE_K` 2,
`SERVICE_MIN_SIM` 0.40, `MAX_CONTEXT` 6, `SCOPED_TOP_K` 8, `GROUNDING_FLOOR` 0.42), same
per-service selection and scoping rules, ready-made queries only (normalisation stays with the
routing code, which moves in M2). Only services `live` in the release are searched.

### 7.3 Parity proof

1. **Baseline** (owner's PC, the Python environment that has ChromaDB): a one-off script runs all
   74 gold queries through the legacy eval logic (importing each script's evaluation function) and
   through `answer.retrieve()` (unscoped, and scoped to the gold query's service), recording top-k
   IDs, similarities and verdicts. Output committed as `docs/reports/m1-parity/chroma_baseline.json`.
2. **Harness check**: a unit test replays the baseline's ChromaDB hits through `gsn.evals` and must
   reproduce every legacy verdict — this proves the port of the verdict rules on its own.
3. **Release side**: release 1 built from the same commit; `gsn parity` runs the same queries
   against it.
4. **Pass** = identical verdicts, identical top-k IDs in the same order, every similarity within
   0.001, for both the gold-query runs and the `retrieve()` runs. Any mismatch is investigated and
   explained in the report, never waved through.
5. Report: `docs/reports/<date of the run>-m1-parity.md`, summary in `docs/PROJECT_STATUS.md`.

The image pins the same `sentence-transformers` (5.6.0) and `torch` (2.13.0, CPU build) as the
PC, and the model revision the PC uses (`BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181`),
so only platform rounding should differ.

## 8. Tamper protection (carried from M0)

### 8.1 Guard watchdog

- First-start step (`db/init/`, superuser): a `gsn_guard` schema owned by the superuser holding a
  registry of protected objects, a `SECURITY DEFINER` registration function (EXECUTE granted only
  to `gsn_migrate`; there is no "unregister"), and event triggers on `ddl_command_end` and
  `sql_drop`.
- Migrations register each protected table **by identity (OID)** together with its guard triggers
  and their trigger functions: `audit_log`, `record_versions`, `publications` (append-only),
  `releases`, `release_members`, `release_services`, `active_release` (state rules).
- After every schema change the watchdog checks that every registered table still exists and
  every registered trigger exists and is enabled, and rejects any DROP of, or `CREATE OR REPLACE`
  on, a registered object. A violation rolls the schema change back. Renaming does not escape it
  (checks are by OID).
- Changing a guard later needs the superuser, by design (documented break-glass procedure). The
  superuser itself is out of scope: it is reachable only from the server console.
- Migrations check that the watchdog exists and stop with a clear message if the database predates
  it. Because `db/init/` runs only on an empty volume, the local database is recreated once
  (`docker compose down -v`; it holds no real data).

### 8.2 Audit hash chain

- `audit_log` gains `prev_hash` and `row_hash`, set by a `BEFORE INSERT` trigger (serialised with a
  transaction advisory lock): `row_hash = sha256(prev_hash ‖ canonical row)`, timestamps in UTC.
  Callers still insert only `actor, action, target, details`.
- Rows written before the chain existed (local test rows only) keep NULL hashes; verification
  starts at the first hashed row.
- `audit_verify()` (`SECURITY DEFINER`) returns OK or the first broken row ID, so the worker can
  verify without reading audit contents. `gsn audit verify` wraps it.
- Exporting the latest hash off the server (so even a superuser rewrite is detectable) is M4.

### 8.3 Grant tidy-up

Drop the unneeded `USAGE` on `audit_log_id_seq` (identity column); revoke `TEMPORARY` on `gsn`
and `CONNECT` on the `postgres` database from `PUBLIC`.

## 9. Packaging and runtime

### 9.1 Python package and image

- New optional extra `gsn[ml]`: `sentence-transformers==5.6.0`, `torch==2.13.0` (CPU wheel from
  the PyTorch CPU index), plus their locked tree in `engine/constraints.txt`. The runtime and test
  images install `gsn[ml]`; the Windows `.venv` does not (C: is nearly full) — unit tests there use
  a deterministic fake embedder.
- `torch` and `sentence-transformers` stay at the PC's versions until the M2 engine switch;
  Dependabot ignores them until then (an upgrade later means re-running the parity check).
- New modules: `gsn.embed` (real and fake embedders behind one interface), `gsn.store`
  (database access), `gsn.retrieval`, `gsn.evals`, `gsn.release` (build/publish/rollback/list/
  show), `gsn.audit` (verify), wired into `gsn.cli`.
- Image adds `git` (read-only plumbing for §6 step 1). Expected growth ≈ 1 GB.

### 9.2 Compose

- New one-shot service `release` (profile `tools`, never started by `docker compose up`): image
  `gsn-engine:dev`, `gsn_worker` credentials, volumes `gsn-models:/models` and
  `${GSN_REPO_GIT:-./.git}:/repo.git:ro` (only `.git`: git history never contains
  `setenv_*.bat` or `.env`), `HF_HOME=/models`, `HF_HUB_OFFLINE=1` (`gsn models fetch` runs with
  it set to `0`). No published ports (the exposure test keeps enforcing this).
- `gsn-models` volume ≈ 2.3 GB on the Docker drive (G:, 26 GB free).

## 10. Testing and CI

- **Unit** (host `.venv` and CI python job, fake embedder): manifest/threshold parsing, integrity
  checks (one failing fixture per rule), content hashing and version reuse, `url_kind` port,
  verdict rules (including the baseline replay of §7.3 step 2), retrieval selection logic, CLI
  argument handling.
- **Integration** (stack job, real PostgreSQL, fake embedder): build → validated/rejected →
  publish → rollback; draft-only membership changes; append-only tables reject UPDATE/DELETE/
  TRUNCATE; watchdog rejects dropping/disabling/replacing guards (including after a rename);
  hash chain verifies and detects a break; per-role grants (each role can do exactly its list).
- **Real-model evals** (new CI workflow): builds a release with the real model and runs the gold
  queries; triggered by changes to `engine/src/gsn/{embed,retrieval,evals,release}`,
  `data/chunks/**`, gold files, the manifest, thresholds or the registry, plus a manual button;
  model cached between runs (public repository: no minute cost).
- **M0 test minors** folded into one packet: grants test made catalog-wide, NULL-ACL assertion,
  `current_database()` instead of a hard-coded name, `10-roles.sh` mode test, `.env.example` ↔
  compose `${VAR:?}` consistency test, importlib test mode, `test_grants` docstring accuracy.
- Workflow as in M0: small packets with failing tests first, Sonnet implements, Opus reviews and
  re-runs the tests; the ChromaDB baseline and the final parity run are done by Opus.

## 11. Changes to the parent spec

- §6.1: live/hidden status is per release (`release_services`), not a global `services.status`,
  so a rollback also rolls back a service going live. New `publications` table records every
  publish/rollback and drives rollback. `text_bn`/`text_en` are deferred to M2 (sources-only mode)
  and will be derived by explicit per-corpus rules from the stored original record; M1 does not
  guess them.
- §6.1/§6.4: release statuses are `draft`, `validated`, `rejected`; "which release is live" is
  `active_release` + `publications` (no `published`/`retired` status).
- §6.3: the `verified_at` mapping is confirmed (§2 above).
- §6.4: publish notifications arrive with the notification sender in M6.

## 12. Out of scope

| Item | Milestone |
|---|---|
| Engine `/v1/turn`; chatbot reading PostgreSQL; routing code moving into `gsn` | M2 |
| Deleting ChromaDB and `data/vectordb/` | M2, after the engine switch |
| `text_bn`/`text_en` columns; sources-only mode | M2 |
| Backups; off-server copy of the audit hash | M4 |
| Admin release list and Rollback button; publish notifications | M6 |
| Visa release | data workflow, once it has a gold set |
| Vector index | when the corpus reaches tens of thousands of records |

## 13. Risks

| Risk | Mitigation |
|---|---|
| Linux vs Windows PyTorch gives slightly different vectors | Same library versions and model revision; tolerance 0.001; any order change investigated |
| The local model cache loaded different weights than the pinned revision | Parity run would show it; the report records which files were loaded |
| ChromaDB's stored vectors were computed in July–August, possibly with older library versions | The baseline is what users get today, so it is the reference; differences beyond tolerance are investigated before anything is re-embedded |
| Watchdog blocks a legitimate migration | Additive changes are allowed; only guard removal/disable/replace is blocked; documented superuser procedure |
| Image and model fill the Docker drive | Sizes stated (≈ 1 GB + 2.3 GB); keep ≥ 10 GB free on G:; `docker builder prune -f` |
| Legacy eval rules ported wrongly | Baseline replay test (§7.3 step 2) proves the port independently of retrieval |
