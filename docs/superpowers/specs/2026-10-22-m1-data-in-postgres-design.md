# M1 — Data in PostgreSQL: design

Status: approved by the owner 2026-10-22; revised the same day after an independent review by
DeepSeek V4.1 Flash (max reasoning; report and triage in
`docs/superpowers/reports/2026-10-22-m1-design-review-deepseek.md`).
Parent: `2026-10-20-system-architecture-design.md` (§6 data model and releases, §10.2 gates, §11
milestones). This document fixes the details M1 needs; where it refines the parent spec, §11 lists
the change.

## 1. Goal and finish line

Move the knowledge base from ChromaDB into PostgreSQL + pgvector as **immutable, versioned
releases**, built and published with a `gsn release` command, and prove that retrieval from a
release gives the same results as today's ChromaDB collections.

**Done when:**

1. A commit C contains the ChromaDB baseline (§7.3) and all M1 code. Release 1, built from C,
   passes its gates and is published.
2. The parity comparison (§7.3) passes: stored vectors match, and for every gold query and every
   `retrieve()` case the verdicts are identical and the results agree within the stated tolerance.
3. The routing tests (`test_multiturn.py`, `test_understand.py`, `test_turn_routing.py`,
   `test_eval_conversations.py`) pass on C, and release 1's `registry_sha256` equals the SHA-256 of
   `concept_registry.json` at C. (These are routing tests, not retrieval tests: `test_multiturn.py`
   stubs the vector store.)
4. The tamper protections (§8) are in place and tested; CI is green.
5. The parity report is committed after the run (a follow-up commit; release 1 stays built from C).

**Nothing changes for users in M1.** `answer.py`, `app.py` and the four legacy eval scripts keep
using ChromaDB. The chatbot switches to PostgreSQL in M2 (engine service); ChromaDB and
`data/vectordb/` are deleted only after that switch.

## 2. Decisions (owner, 2026-10-22)

| Topic | Decision |
|---|---|
| Approach | New modules in the `gsn` package, run in a one-off container (approach A). Not a Windows-host tool; not a ChromaDB-shaped adapter in the legacy code. |
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
| `record_versions` | `version_id` (identity PK), `record_id` FK, `content_hash`, `fields_hash`, `text_hash`, `hash_version`, `record` (JSONB: the original record, every field; string values exactly as in the file), `embed_text` (the record's `text`, exactly what is embedded), `category`, `subcategory`, `heading`, `lang`, `source_url`, `url_kind`, `official`, `source_verified`, `verified_at` (date, nullable), `embedding` (`vector(1024)`), `embedding_model`, `source_commit`, `created_at`, `created_by`, `change_note` | **append-only** (UPDATE/DELETE/TRUNCATE rejected) |
| `releases` | `release_id` (identity PK, small integers the owner types), `status` (`draft` → `validated` \| `rejected`), `source_commit`, `manifest` (JSONB), `registry` (JSONB), `registry_sha256` (of the file's raw bytes), `thresholds` (JSONB), `gate_report` (JSONB), `embedding_model`, `note`, `override_reason`, `created_at`, `created_by` | only `status` (+ `gate_report`, `override_reason`) may change, once, out of `draft` |
| `release_members` | `release_id`, `version_id`, `record_id`; PK (`release_id`, `version_id`), UNIQUE (`release_id`, `record_id`) — one version per record per release | insert/delete only while the release is `draft` |
| `release_services` | (`release_id`, `service_id`) PK, `status` (`live` \| `hidden`) | frozen with the release |
| `active_release` | single row (`singleton` boolean PK), `release_id` FK, `switched_at` | changed only through the publish/rollback functions (§6); must point at a `validated` release |
| `publications` | `publication_id`, `release_id`, `previous_release_id`, `action` (`publish` \| `rollback`), `at`, `actor` | **append-only**; written only by the publish/rollback functions |

- **Version identity** — `content_hash` = SHA-256 of the record's canonical JSON, computed in
  Python as `json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`
  encoded UTF-8, with `hash_version = 1`. It is never recomputed from `jsonb::text`.
  `fields_hash` = SHA-256 of the canonical JSON of the derived columns (`category`, `subcategory`,
  `heading`, `lang`, `source_url`, `url_kind`, `official`, `source_verified`, `verified_at` as an
  ISO date or null), so a version also captures what the manifest mapping and the derivation code
  made of the record. A build reuses an existing version when (`record_id`, `content_hash`,
  `fields_hash`, `embedding_model`) exists (UNIQUE index). A changed field such as a newer
  `fetched_at`, a changed `verified_at_field` mapping or a changed `url_kind` rule is a real change
  and gets a new version (amended 2026-10-23 after the A6 review: reuse by content alone would
  have kept stale derived columns forever).
- **Embedding reuse** — `text_hash` = SHA-256 of `embed_text`. A new version whose (`text_hash`,
  `embedding_model`) already exists copies that vector instead of re-embedding, so a re-crawl that
  only changes provenance fields costs no embedding time.
- `url_kind` is computed at build time by a port of `answer.url_kind()` (same markers; unit tests
  pin identical output on every corpus URL).
- `source_verified` = `not record.get("source_url_unverified", False)`. (Checked: the one e-Passport
  record carrying the flag, `epassport-notice-dhaka-cantonment-jurisdiction`, was verified and set
  to `false` in commit `1ae2e0d`; the `eval_epassport.py` docstring that still calls it unverified
  is stale and gets corrected.)
- `verified_at`: the mapped field is parsed as an ISO date or date-time and its date part is
  stored (NID's `fetched_at` is a date-time, e.g. `2026-07-12T23:04:19`). Unparseable or
  future values fail the build.
- **Record IDs**: NID IDs are derived from URL + heading + the first 40 characters of the body
  (`build_nid_corpus.py`), so editing those fields yields a new `record_id` (shown as removed +
  added, not as a new version). Accepted for Phase 1 and shown that way by `gsn release show`.
- **No vector index**: retrieval is an exact scan (293 rows, milliseconds). An HNSW index is added
  when the corpus reaches tens of thousands of records.
- **Retrieval query** (per service, per query embedding): members of the given release joined to
  `record_versions`, filtered by `service_id`, `ORDER BY embedding <=> $q, record_id`, limited to
  k. The `record_id` tie-break makes the order deterministic. Similarity = 1 − cosine distance, as
  ChromaDB reports it.

### 3.1 Database roles

| Role | M1 grants |
|---|---|
| `gsn_worker` | SELECT/INSERT on `services`, `records`, `record_versions`, `releases`, `release_members`, `release_services`; UPDATE (status, gate_report, override_reason) on `releases`; DELETE on `release_members` (draft only, enforced by trigger); SELECT on `active_release` and `publications`; EXECUTE on `publish_release()`, `rollback_release()`, `audit_verify()`; existing column-level INSERT on `audit_log`. **No** direct INSERT/UPDATE on `active_release` or `publications`. |
| `gsn_engine` | SELECT only on views that show the **active release**: its records and vectors, its live services, and its registry (used from M2). No access to drafts, rejected releases, gate reports or old versions. |
| `gsn_web` | nothing new (release list and Rollback arrive with the admin panel in M6) |
| `gsn_migrate` | owns the tables, views and functions; registers protected objects (§8). `CREATE` on the database is revoked (no new schemas are needed; re-granted deliberately if that changes). |

All `SECURITY DEFINER` functions pin `SET search_path = pg_catalog, <their schema>`, qualify every
object name, have `EXECUTE` revoked from `PUBLIC`, and are granted only to the roles listed.

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
e-Return, BDRIS; IDs taken from the baseline run). Editing this file is a visible commit.

## 5. Commands

Run in the one-off `release` service (§9.2): `docker compose --profile tools run --rm release …`.
Machine-readable output goes to **stdout** (the host redirects it to a file); nothing needs a
writable host folder.

```
gsn models fetch                                  download bge-m3 (pinned revision) into the model volume
gsn release build [--commit <sha>] [--note TEXT] [--override REASON]
gsn release publish <release_id>
gsn release rollback
gsn release list
gsn release show <release_id>                     gate report; records added/changed/removed vs live
gsn eval --release <release_id> [--json]          gold queries against any release
gsn parity --release <release_id>                 compare with the committed ChromaDB baseline (§7.3)
gsn audit verify                                  check the audit_log hash chain (§8.2)
```

`gsn parity` reads the baseline and the `retrieve()` cases **from the release's source commit**
(git), so the comparison always uses the baseline that was committed with the code.

Every build, publish, rollback and override writes an `audit_log` row (actor from `GSN_ACTOR`,
default `owner`).

## 6. `release build`, publish, rollback

1. **Resolve** `--commit` (default `HEAD`) to a full SHA. All git access is plumbing only
   (`git --git-dir=/repo.git cat-file …`) with `-c safe.directory=/repo.git`,
   `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, hooks and fsmonitor disabled. A
   preflight gives a clear error if the repository cannot be read, and refuses to run if any
   commit in the history contains a path matching `setenv*.bat` or `.env`.
2. **Integrity checks** — any failure stops the build before anything is written, with the full
   list of problems:
   - every listed file exists at the commit and parses; each `live` service has a corpus, a gold
     file and a registry entry;
   - every record has `id`, non-empty `text`, non-empty `source_url`;
   - no `TODO(verify)` anywhere in a record;
   - record IDs unique across the release; no two records with identical `text`;
   - a record whose `lang` is `bn`, `bn+en` or `mixed` contains Bengali script;
   - the mapped `verified_at` field parses (§3) and is not in the future;
   - a record's service never changes from the one already stored for its `record_id`.
3. **Embed** new texts (those without a stored vector for the model) **before** opening the write
   transaction, so no transaction stays open while the CPU works.
4. **Draft**, in one short transaction: insert the release (`draft`), its `release_services`, any
   new versions, and its members.
5. **Evals**: run every gold query of every live service against the draft (§7.1).
6. **Gate**: any FAIL not listed in `known_failures` → `rejected`, unless `--override REASON`
   (reason stored on the release and in `audit_log`). WARN and OBSERVE never block. Otherwise
   `validated`. The gate report (integrity summary, per-query verdicts, counts) is stored.

**Publish** and **rollback** are database functions (`SECURITY DEFINER`, owned by `gsn_migrate`,
protected by §8): each performs the `active_release` switch, the `publications` row and the
`audit_log` row in one transaction, so none can happen without the others.

- `publish_release(id, actor)` refuses a release that is not `validated` or is already active. Any
  validated release, including an older one, may be published by ID.
- `rollback_release(actor)` undoes the most recent switch: it makes the latest publication's
  `previous_release_id` active again (so a second rollback re-applies the release the first one
  undid). To go further back, publish the older release by ID. With no earlier publication it
  refuses.

## 7. Evals and parity

### 7.1 Eval harness (`gsn.evals`)

One harness for the four gold files, reproducing each legacy script's verdict rules exactly,
including their per-file differences:

| Service | Gate field | Out-of-scope ceiling | OBSERVE verdict |
|---|---|---|---|
| NID, e-Passport | `expected_category` | 0.55 | yes |
| e-Return | `expected_subcategory` | 0.55 | yes |
| BDRIS | `expected_category` | **0.62** | **no** (any `expected_category: null` is an out-of-scope probe) |

All use `top_k` from the gold file (4 today) and embed the gold query raw. Used by
`release build`, `gsn eval` and `gsn parity`.

### 7.2 Retrieval module (`gsn.retrieval`)

Port of `answer.retrieve()` over a release: same constants (`CANDIDATE_N` 8, `PER_SERVICE_K` 2,
`SERVICE_MIN_SIM` 0.40, `MAX_CONTEXT` 6, `SCOPED_TOP_K` 8, `GROUNDING_FLOOR` 0.42), same
per-service selection, multi-query merge and scoping rules. It takes **ready-made queries only**
(the `queries=` path of `retrieve()`); LLM normalisation stays with the routing code, which moves
in M2. Only services `live` in the release are searched.

### 7.3 Parity proof

1. **Baseline** (owner's PC, the Python environment that has ChromaDB), one script, output
   committed under `docs/reports/m1-parity/`:
   - **Freshness check first**: every ChromaDB collection's IDs and stored documents must equal
     the committed corpus exactly; otherwise the script stops (re-embed before going on).
   - Records the library versions (`torch`, `sentence-transformers`, `chromadb`), the model
     revision and the weight files actually loaded.
   - Exports ChromaDB's 293 **stored vectors**.
   - Runs all 74 gold queries directly against the collections (unrounded similarities, top-k
     IDs) and records each legacy script's verdict from its own evaluation function.
   - Runs `answer.retrieve()` **only through its `queries=` path** (no LLM): every gold query alone,
     unscoped and scoped to its service; plus a committed set of multi-query, multi-service cases
     (`retrieve_cases.json`: 2–3 ready-made query strings each, over 2, 3 and 4 services),
     mirroring how `handle_turn` calls it.
2. **Harness check**: a unit test replays the baseline's ChromaDB hits through `gsn.evals` and must
   reproduce every legacy verdict — this proves the port of the verdict rules on its own.
3. **Release side**: release 1 built from commit C; `gsn parity --release 1`.
4. **Pass** =
   - every stored vector in release 1 has cosine similarity ≥ 0.9999 with ChromaDB's vector for
     the same record (if this fails, nothing else is meaningful);
   - identical verdicts for all 74 gold queries;
   - identical top-k ID sets and the same selected context for every `retrieve()` case;
   - every similarity within 0.001 of the baseline;
   - order differences are allowed only between results whose similarities differ by less than
     0.001 (reported as tie swaps, not failures);
   - every decision within 0.001 of a threshold (`SERVICE_MIN_SIM`, `GROUNDING_FLOOR`, the
     out-of-scope ceilings, `min_similarity`) is listed in the report for a human look.
   Any other mismatch is investigated and explained in the report, never waved through.
5. Report: `docs/reports/<date of the run>-m1-parity.md`, summary in `docs/PROJECT_STATUS.md`.

The release image pins the same `sentence-transformers` and `torch` versions as the PC (recorded
by the baseline) and the model revision the PC uses
(`BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181`), so only platform rounding should differ.

## 8. Tamper protection (carried from M0)

### 8.1 Guard watchdog

- First-start step (`db/init/`, superuser): a `gsn_guard` schema owned by the superuser holding a
  registry of protected objects, a `SECURITY DEFINER` registration function (EXECUTE granted only
  to `gsn_migrate`; there is no "unregister"), and event triggers on `ddl_command_end` and
  `sql_drop`.
- Migrations register each protected table **by schema-qualified name** together with its guard
  triggers, the trigger functions and the publish/rollback/verify functions, and the table's
  column list (name and type) at registration time: `audit_log`, `record_versions`,
  `publications` (append-only), `releases`, `release_members`, `release_services`,
  `active_release` (state rules).
- After every schema change the watchdog checks, and rolls the change back on any violation:
  - every registered table still exists under its name (renaming or dropping it is a violation);
  - every registered column still exists with its registered type (adding columns is allowed;
    dropping, renaming or retyping a registered column is not);
  - every registered trigger exists and is enabled in a mode that fires in normal sessions
    (`tgenabled` `O` or `A`; `D` disabled and `R` replica-only are violations);
  - no registered function was dropped or replaced (`CREATE OR REPLACE`). The function fingerprint
    uses role *names* (`proowner::regrole`), field separators and the ACL, so it survives a restore
    and also catches a widened EXECUTE grant;
  - (added 2026-10-23 after the B2 review) no table rewrite of a protected table (a `table_rewrite`
    event trigger: `ALTER COLUMN … TYPE … USING` to the same type would otherwise rewrite rows
    without firing row triggers); protected tables stay `LOGGED`; no unregistered non-internal
    trigger on a protected table (a later-firing trigger could change a row after the guard
    approved it); no inheritance parent or child for a protected table (rows in a child skip the
    guards but appear in plain reads).
- Protected functions in `public` pin `search_path = pg_catalog, pg_temp` (every relation is
  already qualified), so no overload created in `public` can capture a call inside them.
- Name-based registration survives `pg_dump`/`pg_restore` (object IDs change on restore); the M4
  restore test includes a watchdog check.
- Changing a guard later needs the superuser, by design (documented break-glass procedure). The
  superuser itself is out of scope: it is reachable only from the server console.
- Migrations check that the watchdog exists and stop with a clear message if the database predates
  it. Because `db/init/` runs only on an empty volume, the local database is recreated once
  (`docker compose down -v`; it holds no real data).

### 8.2 Audit hash chain

- `audit_log` gains `chain_seq`, `prev_hash`, `row_hash` and `hash_version`, set by a
  `BEFORE INSERT` trigger function (`SECURITY DEFINER`, pinned `search_path`, so the worker — which
  has no SELECT on `audit_log` — can still insert). The function locks a single-row `audit_chain`
  head (`SELECT … FOR UPDATE`), takes the next `chain_seq` and the previous hash from it, computes
  `row_hash = sha256(prev_hash ‖ canonical row)` and updates the head. Concurrent inserts are
  therefore chained in lock order, independently of the identity `id` (which is assigned before
  the trigger runs).
- Canonical row: `chain_seq`, `id`, `at` rendered in UTC with fixed precision, `actor`, `action`,
  `target`, `details` — with `hash_version = 1` so the format can evolve.
- Verification walks the chain by `chain_seq`, not by `id`, starting at `chain_seq = 1`. Every
  `audit_log` row must be chained: a row with a NULL `chain_seq` is reported as a break (amended
  2026-10-23 after the B1 review — every real database is created fresh, so an unchained row can
  only be a forged append). The verification core takes the rows and the head as parameters, so its
  tamper detection can be tested on scratch copies while the watchdog (§8.1) protects the real
  table.
- `audit_verify()` (`SECURITY DEFINER`) returns OK or the first broken `chain_seq`, so the worker can
  verify without reading audit contents. `gsn audit verify` wraps it.
- Exporting the latest hash off the server (so even a superuser rewrite is detectable) is M4.

### 8.3 Grant tidy-up

Drop the unneeded `USAGE` on `audit_log_id_seq` (identity column); revoke `TEMPORARY` on `gsn`
and `CONNECT` on the `postgres` database from `PUBLIC`; revoke `CREATE` on `gsn` from
`gsn_migrate`. `tests/integration/test_grants.py` is updated to the new expectations.

## 9. Packaging and runtime

### 9.1 Images and dependencies

- **Separate release image** (`gsn-release:dev`, Dockerfile target `release`): adds the `gsn[ml]`
  extra (`sentence-transformers`, CPU-only `torch` from the PyTorch CPU index, at the PC's
  versions) and `git`. The ML packages are installed in their own `ml` stage built `FROM base`
  from `constraints-ml.txt` alone, and `release` builds on that stage and installs the engine on
  top, so editing engine code never rebuilds the ~1.7 GB ML layer (amended 2026-10-25 after the C1
  review). The engine/worker runtime and test images stay as they are until M2. Expected size ≈
  runtime + 1.7 GB unpacked.
- The ML dependency tree is locked in a separate `engine/constraints-ml.txt`, generated **inside a
  Linux container** by a documented command (it cannot come from the Windows venv). It lists what
  the `ml` extra needs beyond the runtime image (packages that `constraints.txt` pins only for the
  dev tree are therefore listed too, at the same versions). CI audits it
  and `engine/tests/test_constraints.py` covers the `ml` extra. Dependabot ignores `torch` and
  `sentence-transformers` until the M2 engine switch (an upgrade later means re-running parity).
- The image creates `/models` owned by the `gsn` user before switching to it, so a fresh
  `gsn-models` named volume is writable by `gsn models fetch`.
- The Windows `.venv` does not install `[ml]` (C: is nearly full): unit tests there use a
  deterministic fake embedder.
- New modules: `gsn.embed` (real and fake embedders behind one interface), `gsn.store`
  (database access), `gsn.retrieval`, `gsn.evals`, `gsn.release` (build/list/show and calls to the
  publish/rollback functions), `gsn.gitsource` (hardened git reads), `gsn.audit` (verify), wired
  into `gsn.cli`.

### 9.2 Compose

- New one-shot service `release` (profile `tools`, never started by `docker compose up`): image
  `gsn-release:dev`, `gsn_worker` credentials, volumes `gsn-models:/models` and
  `${GSN_REPO_GIT:-./.git}:/repo.git:ro` (only `.git`; the repository's remote URL holds no
  credentials — Git Credential Manager keeps them outside `.git`), `HF_HOME=/models`,
  `HF_HUB_OFFLINE=1` (`gsn models fetch` runs with it set to `0`). No published ports.
- `deploy/tests/test_compose_exposure.py` is updated in the same change: `release` joins the
  one-shot services, receives only `GSN_DB_WORKER_PASSWORD`, must have `profiles: ["tools"]` and no
  ports; `gsn-release:dev` joins the set of locally built images.
- `gsn-models` volume ≈ 2.3 GB on the Docker drive (G:, 26 GB free).

## 10. Testing, CI and delivery

- **Unit** (host `.venv` and CI python job, fake embedder): manifest/threshold parsing, integrity
  checks (one failing fixture per rule, including a date-time and a future `verified_at`),
  canonical hashing and version/embedding reuse, `url_kind` port, verdict rules (including the
  baseline replay of §7.3 step 2 and the per-file differences of §7.1), retrieval selection and
  tie-break logic, parity comparison rules, hardened git reads, CLI argument handling.
- **Integration** (stack job, real PostgreSQL, fake embedder): build → validated/rejected →
  publish → rollback; the worker cannot touch `active_release`/`publications` directly; the engine
  role sees only the active release's views; draft-only membership changes; append-only tables
  reject UPDATE/DELETE/TRUNCATE; watchdog rejects dropping, renaming, disabling, replica-mode and
  replacing guards and dropping/retyping registered columns, and allows adding a column; audit
  inserts from two concurrent connections produce a verifiable chain; `audit_verify` detects a
  break; per-role grants (each role can do exactly its list).
- **Real-model evals** (new CI workflow): builds the release image, fetches the model (cached
  between runs), builds a release and runs the gold queries; triggered by changes to
  `engine/src/gsn/{embed,retrieval,evals,release}`, `data/chunks/**`, gold files, the manifest,
  thresholds or the registry, plus a manual button (public repository: no minute cost).
- **M0 test minors** folded into one packet: grants test made catalog-wide, NULL-ACL assertion,
  `current_database()` instead of a hard-coded name, `10-roles.sh` mode test, `.env.example` ↔
  compose `${VAR:?}` consistency test, importlib test mode, `test_grants` docstring accuracy; plus
  the stale `eval_epassport.py` docstring (§3).
- **Delivery in four reviewed stages**, each ending with green tests and an Opus review before the
  next starts: (A) schema, roles, release build/publish/rollback with the fake embedder;
  (B) tamper protection (watchdog, hash chain, grant tidy-up); (C) release image, model, real
  embedder, real-model CI workflow; (D) baseline, parity and release 1. Within each stage: small
  packets with failing tests first, Sonnet implements, Opus reviews and re-runs the tests; the
  ChromaDB baseline and the final parity run are done by Opus.

## 11. Changes to the parent spec

- §6.1: live/hidden status is per release (`release_services`), not a global `services.status`,
  so a rollback also rolls back a service going live. New `publications` table records every
  publish/rollback and drives rollback. `text_bn`/`text_en` are deferred to M2 (sources-only mode)
  and will be derived by explicit per-corpus rules from the stored original record; M1 does not
  guess them.
- §6.1/§6.4: release statuses are `draft`, `validated`, `rejected`; "which release is live" is
  `active_release` + `publications` (no `published`/`retired` status). Publish and rollback are
  database functions; the engine reads only views of the active release.
- §6.3: the `verified_at` mapping is confirmed (§2 above).
- §6.4: publish notifications arrive with the notification sender in M6.
- §10.2 (every data release): in M1 the gate is integrity + retrieval evals; routing fixtures join
  the release gate in M2 (with the routing code) and the generation eval in M7.
- §11 (M1 "done when"): `test_multiturn.py` is a routing test and does not exercise retrieval; the
  M1 finish line is §1 above.

## 12. Out of scope

| Item | Milestone |
|---|---|
| Engine `/v1/turn`; chatbot reading PostgreSQL; routing code moving into `gsn` | M2 |
| Deleting ChromaDB and `data/vectordb/` | M2, after the engine switch |
| `text_bn`/`text_en` columns; sources-only mode | M2 |
| Backups (including a restore test with the watchdog); off-server copy of the audit hash | M4 |
| Admin release list and Rollback button; publish notifications | M6 |
| Visa release | data workflow, once it has a gold set |
| Vector index | when the corpus reaches tens of thousands of records |
| Restricting the release container's network to PostgreSQL only | M4 (server hardening) |

## 13. Risks

| Risk | Mitigation |
|---|---|
| Linux vs Windows PyTorch gives slightly different vectors | Same library versions and model revision; stored-vector check; tolerance 0.001 with tie and near-threshold rules |
| The local model cache loaded different weights than the pinned revision | The baseline records the loaded files; the stored-vector check would show it |
| ChromaDB's collections are stale relative to the committed corpus (vectors from July–August) | Baseline freshness check (IDs and documents must equal the commit) before anything is recorded |
| Watchdog blocks a legitimate migration | Additive changes are allowed; only guard removal/disable/replace and registered-column changes are blocked; documented superuser procedure |
| Image and model fill the Docker drive | Separate release image; sizes stated (≈ 1 GB + 2.3 GB); keep ≥ 10 GB free on G:; `docker builder prune -f` |
| Legacy eval rules ported wrongly | Baseline replay test (§7.3 step 2) proves the port independently of retrieval |
