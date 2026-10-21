# M1 design review by DeepSeek V4.1 Flash, with triage

- **Reviewed:** `docs/superpowers/specs/2026-10-22-m1-data-in-postgres-design.md` as committed in
  `ed36689`, plus the code and data it depends on.
- **Reviewer:** DeepSeek V4.1 Flash (`deepseek-flash`), reasoning effort `max`, via the DeepSeek
  Harness headless profile in read-only mode. It ran on a `git archive` copy of the commit, so no
  secret files were present. The run took 9.7 minutes.
- **Triage:** Opus 5.5, 2026-10-22. Each claim was checked against the code and data before being
  accepted. The design was revised the same day; the revised spec's sections are cited below.

## Triage

| # | Finding (short) | Verdict | Where it went |
|---|---|---|---|
| 1 | Hash trigger needs SELECT the worker lacks | Accepted | §8.2 (`SECURITY DEFINER` trigger) |
| 2 | Hash chain ordered by identity `id` breaks under concurrency | Accepted (new) | §8.2 (`audit_chain` head row, `chain_seq`) |
| 3 | `SECURITY DEFINER` functions need a pinned `search_path`; migrate role can create schemas | Accepted | §3.1, §8.3 |
| 4 | `retrieve()` baseline would use LLM normalisation | Accepted | §7.3 (`queries=` path only) |
| 5 | Release container can't read the baseline or keep output | Accepted | §5 (stdout; baseline read from the commit) |
| 6 | JSONB registry can't be byte-identical | Accepted | §1, §3 (`registry_sha256`) |
| 7 | Compose exposure test would fail on the new service | Accepted | §9.2 |
| 8 | git "dubious ownership" / `--git-dir` | Accepted | §6 step 1 |
| 9 | Model volume not writable by the `gsn` user | Accepted | §9.1 |
| 10 | CPU torch index / constraints / image bloat | Accepted | §9.1 (separate release image, Linux-generated `constraints-ml.txt`) |
| 11 | Whole-record hash forces re-embedding on re-crawl | Partly: version identity stays whole-record (a newer `fetched_at` is a real change worth recording); embeddings are reused by `text_hash` | §3 |
| 12 | Engine role can read drafts and old versions | Accepted | §3.1 (views of the active release only) |
| 13 | OID registration breaks after `pg_dump`/restore | Accepted; fixed with name-based registration instead | §8.1 |
| 14 | Column drops and `ENABLE REPLICA TRIGGER` bypass the watchdog | Accepted (new) | §8.1 (column signature; `tgenabled` `O`/`A`) |
| 15 | Worker can switch `active_release` without a publication or audit row | Accepted (new) | §3.1, §6 (publish/rollback as DB functions) |
| 16 | The flagged e-Passport record is stored as verified | **Rejected:** the corpus is right; commit `1ae2e0d` (2026-08-07) verified the URL and cleared the flag. The `eval_epassport.py` docstring is stale and gets corrected. | §3, §10 |
| 17 | No tie rule; tolerance semantics unclear; rounded similarities | Accepted | §3 (tie-break), §7.3 (tie swaps, near-threshold list, unrounded values) |
| 18 | Baseline may be stale relative to the corpus | Accepted | §7.3 (freshness check, stored-vector export) |
| 19 | Parity misses the multi-query/multi-service path | Accepted | §7.3 (`retrieve_cases.json`) |
| 20 | NID `fetched_at` is a date-time | Accepted (new; confirmed `2026-07-12T23:04:19`) | §3 |
| 21 | "All on one commit" contradiction; M1 too big for one pass | Accepted | §1, §10 (four reviewed stages) |
| 22 | NID IDs are content-derived | Accepted as a documented limitation | §3 |
| 23 | Canonical hashing / UTC / hash version unspecified | Accepted | §3, §8.2 |
| 24 | Missing unique keys; long transaction while embedding | Accepted | §3, §6 |
| 25 | `test_multiturn` isn't a retrieval test; `[ml]` not covered by `test_constraints`; per-file eval rules | Accepted | §1, §7.1, §9.1, §11 |
| 26 | `.git` mount trust and config | Checked: the remote URL has no embedded credentials and Git Credential Manager stores them outside `.git`. Added git env hardening and a secret-path preflight; network restriction deferred to M4. | §6, §9.2, §12 |

Alternatives adopted: the stored-vector comparison, a separate release image, and a registry hash.
Not adopted: an in-process Windows comparison. The proof has to cover the container path that
real releases use.

---

## Original report (verbatim)

# Review: M1 — Data in PostgreSQL (design)

Reviewed against the repository snapshot (`answer.py`, the four eval/embed scripts and gold files, the corpora, `db/`, `web/src/main.rs`, `engine/`, `compose.yaml`, CI and integration tests). I made no changes. Line references below are to the files in this snapshot; design references are to `docs/superpowers/specs/2026-10-22-m1-data-in-postgres-design.md`.

## Findings (most severe first)

**1. Blocker — Audit rows cannot be inserted by the release CLI: the hash trigger has no SELECT privilege — §8.2 / §3.1**
- **Wrong:** The design says `audit_log` gains `prev_hash`/`row_hash` "set by a `BEFORE INSERT` trigger", and callers keep inserting only `actor, action, target, details`. It never says that trigger is `SECURITY DEFINER`. A normal (invoker) trigger runs as the inserting role.
- **Evidence:** `db/migrations/0001_audit_log.sql:42-43` grants SELECT to `gsn_web` only, and column-level INSERT `(actor, action, target, details)` to `gsn_web, gsn_worker`. `tests/integration/test_grants.py:13,41-47` asserts `gsn_worker` has **no** SELECT and no table privileges. The release service uses `gsn_worker` credentials (design §9.2).
- **Why:** The trigger's `SELECT … FROM audit_log ORDER BY id DESC LIMIT 1` will raise `permission denied for table audit_log`, aborting every `release build`/`publish`/`rollback` audit write. This is exactly the role that writes them.
- **Fix:** Make the hash trigger `SECURITY DEFINER`, owned by a role with SELECT on `audit_log`, with a fixed `SET search_path = pg_catalog, public`; revoke EXECUTE from PUBLIC and grant it only where needed. Add a worker-inserts-an-audit-row test.

**2. Blocker — The audit hash chain's ordering is unsound under concurrency — §8.2**
- **Wrong:** The design serialises with a transaction advisory lock, but `audit_log.id` is `GENERATED ALWAYS AS IDENTITY` (`0001_audit_log.sql:13`). The identity value is computed **before** `BEFORE ROW` triggers. Two concurrent transactions can therefore get ids in the opposite order to the order in which they acquire the lock: the later id can hold the earlier `prev_hash`. A verifier walking `ORDER BY id` (the natural reading of "verification starts at the first hashed row") reports a false break; a verifier following `prev_hash` links must be specified. Separately, in READ COMMITTED the trigger's predecessor read may still use the INSERT's snapshot after waiting on the lock; that needs a two-session test.
- **Why:** This is the core tamper-evidence property; a false "chain broken" result (or a fork) makes `gsn audit verify` unusable exactly when multiple services write audit rows. M1's single-writer release container may hide it, but the design is the live-system foundation.
- **Fix:** A single-row `audit_chain` table with `UPDATE … RETURNING` (row lock + EvalPlanQual) is the safest construction; alternatively assign a `chain_seq` from a sequence **inside** the lock and verify by it, not by `id`. Add a two-connection integration test.

**3. Blocker — `SECURITY DEFINER` functions are not search_path-hardened, and the migration role can create schemas — §8.1 / §8.2**
- **Wrong:** Only the registration function is called out as `SECURITY DEFINER`; no function has a pinned `search_path`. The watchdog function and `audit_verify()` are not specified at all. Meanwhile `gsn_migrate` owns `public` and has CREATE on the database (`tests/integration/test_grants.py:100-107`). A compromised migration role can create its own schema, set `search_path`, and shadow an unqualified table/function reference inside a function that runs as the superuser owner.
- **Why:** It defeats the purpose of §8: the role the guard is supposed to constrain can escalate through the guard itself. It also means the watchdog's EXECUTE privilege must be granted to every DDL-capable role (or the first migration's `CREATE TABLE` fails if EXECUTE is revoked from PUBLIC).
- **Fix:** `SET search_path = pg_catalog, gsn_guard` on every definer function; schema-qualify every reference; own the guard functions by a dedicated NOLOGIN role; `REVOKE CREATE ON DATABASE gsn FROM gsn_migrate` if migrations do not need new schemas; grant EXECUTE on the watchdog to `gsn_migrate` explicitly.

**4. Blocker — The `retrieve()` parity proof cannot be reproduced: the baseline uses LLM normalisation, the release side deliberately does not — §7.2 / §7.3**
- **Wrong:** §7.3 records only "top-k IDs, similarities and verdicts" from the baseline and then runs "the same queries" against the release; §7.2 says `gsn.retrieval` takes "ready-made queries only (normalisation stays with the routing code, which moves in M2)". But `answer.retrieve()` with no `queries=` calls `normalize()` (`answer.py:175-183`), and `normalize()` calls the LLM for any Latin query that is not clearly English (`normalize_query.py:119-131`), returning `[original, bangla]`. There are **17 Banglish gold queries** (4 NID, 4 ePassport, 4 eReturn, 5 BDRIS).
- **Why:** For those queries the release side either embeds only the raw query (different results) or must re-call the LLM (not available in the release container and not deterministic), so the `retrieve()` half of the parity proof either fails for a reason unrelated to retrieval or silently compares different inputs. `gsn parity --release … --baseline PATH` cannot know the normalised query list.
- **Fix:** Have the baseline record `norm.queries` (and `norm.kind`) per probe, and make `gsn.parity` replay those exact strings through `gsn.retrieval`. Alternatively restrict the `retrieve()` parity to `queries=`-supplied deterministic cases and explicitly waive the Banglish ones in the report.

**5. Blocker — The `release` service cannot see the committed baseline or persist the parity report — §7.3 / §9.2**
- **Wrong:** §9.2 gives the one-off `release` service only `gsn-models:/models` and `${GSN_REPO_GIT:-./.git}:/repo.git:ro`. The `gsn-engine` image installs only the `gsn` package (`engine/Dockerfile:20-24`); the working tree, including `docs/reports/m1-parity/chroma_baseline.json`, is not in the container, and `docker compose run --rm` discards writes. §7.3 step 3 is written as `gsn parity --release 1 --baseline PATH`, and step 5 writes `docs/reports/<date>-m1-parity.md`.
- **Why:** The documented command has no access to its input and its output is thrown away. The same applies to `gsn eval --json PATH`.
- **Fix:** Mount a reports/baseline directory read-write (e.g. `./docs/reports:/reports`), or have `gsn parity` read the baseline from the commit via `git cat-file` and write JSON to stdout; if a report file is required, write it to a mounted volume.

**6. Major — `releases.registry` is JSONB, so it cannot be "byte-identical" to `concept_registry.json` — §1 (done criterion 3) / §3**
- **Wrong:** The done criterion requires the stored registry to be byte-identical to the committed file; the column is `JSONB`. JSONB canonicalises whitespace and key order (and drops duplicates); `concept_registry.json` is 3,732 bytes of pretty-printed JSON with blank lines and one-line keyword arrays.
- **Why:** Done criterion 3 is unachievable as written, so the milestone can never be marked done without weakening it.
- **Fix:** Store `registry_sha256` (of the raw bytes) plus, if desired, `registry JSONB`; compare the hash. Or store the raw text in `text`/`bytea` and compare that. State "semantic equality" if that is what is meant.

**7. Major — Adding the `release` service breaks the compose exposure test, so "CI is green" fails — §9.2 / §10**
- **Wrong:** `deploy/tests/test_compose_exposure.py:21-23` hard-codes `EXPECTED_SERVICES = {caddy, web, engine, worker, postgres, migrate}` and `:128-130` asserts exact equality. `:33-40` hard-codes `ALLOWED_DB_PASSWORDS` with no `release`; `:247-251` will raise `KeyError` for it. The design says the exposure test "keeps enforcing this" but plans no update.
- **Why:** CI goes red on the first commit that adds the service; the design's own done criterion 4 cannot be met.
- **Fix:** Add `release` to `ONE_SHOT_SERVICES`, map it to `{"GSN_DB_WORKER_PASSWORD"}`, update the test name/docstring, and assert it is `profiles: ["tools"]` and has no ports.

**8. Major — `git` in the release image will hit "dubious ownership" on the owner's Windows bind mount — §6 / §9.2**
- **Wrong:** The image runs as `gsn` UID 10001 (`engine/Dockerfile:13-14,22`); `${GSN_REPO_GIT:-./.git}:/repo.git:ro` is a Windows bind mount, normally root-owned in the Linux container. Git refuses a repository owned by another user unless `safe.directory` is set. Also, `/repo.git` is the git directory, not a worktree: `git -C /repo.git` will not work; it needs `--git-dir`/`GIT_DIR`.
- **Why:** `gsn release build` fails on the primary development machine before reading any content, with a confusing error.
- **Fix:** Run `git -c safe.directory=/repo.git --git-dir=/repo.git …` (plus `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`), and add a preflight that reports a clear error. Test the actual mount on the owner's machine.

**9. Major — The model named volume is not writable by the non-root container user — §9.2**
- **Wrong:** `HF_HOME=/models` and `gsn-models:/models`, but the image never creates `/models` and runs as `gsn` (UID 10001). A fresh named volume is root-owned (the image directory does not exist to seed ownership), so `gsn models fetch` cannot write to it.
- **Why:** First-run model download fails with `PermissionError`, blocking both `gsn models fetch` and every build.
- **Fix:** `RUN mkdir -p /models && chown gsn:gsn /models` in the runtime stage before `USER gsn` (Docker copies that ownership into a new named volume), or an explicit init step; verify by running `gsn models fetch` as the container user.

**10. Major — The CPU torch/index/constraints story does not work as written — §9.1**
- **Wrong:** `engine/Dockerfile:20-21` installs `-c /build/constraints.txt` from the default index; `engine/constraints.txt` (56 lines) contains no `torch`/`sentence-transformers`; `engine/pyproject.toml` has no `[ml]` extra; `docs/DEVELOPMENT.md:271-276` regenerates constraints from a **Windows** venv using `.[dev]`, while §9.1 says the Windows `.venv` will not install `[ml]` (C: is nearly full). A `torch==2.13.0+cpu` pin is not on PyPI; `ci.yml:63` audits `-r engine/constraints.txt`, and Dependabot reads `pyproject.toml`. Installing `gsn[ml]` into the runtime **and** test images also bloats the M0 images for a tool only the release container uses.
- **Why:** The image either installs a huge CUDA wheel, fails to resolve `+cpu`, or the constraints cannot be regenerated by the documented procedure; CI audit/Dependabot become unreliable.
- **Fix:** Add a dedicated `release` image target (or `gsn-release:dev`) with `--index-url https://download.pytorch.org/whl/cpu` and `torch==2.13.0+cpu`; regenerate the ML tree in a Linux/container venv and document it; add `[ml]` to `engine/tests/test_constraints.py:41-49`; keep runtime/test lean.

**11. Major — `content_hash` over the whole record defeats "embed only changed records" — §3**
- **Wrong:** §3 defines `content_hash` as the hash of the record's canonical JSON (all fields) and reuses a version on `(record_id, content_hash, embedding_model)`. The record includes `fetched_at`/`last_crawled` and provenance fields (`source_sha256`, `bangla_ok`, `bangla_issues`). A re-crawl changes those even when `text` is unchanged, so every record gets a new hash and is re-embedded.
- **Why:** For the tracker (the long-term goal), unchanged content would be re-embedded on every crawl; the version-reuse optimisation never fires on real re-crawls.
- **Fix:** Hash the fields that affect retrieval/output (`embed_text`, `heading`, `source_url`, `category`, `lang`, …) and keep the full original record as payload; or exclude explicitly volatile provenance fields from the identity hash.

**12. Major — The engine role can read drafts and every historical version — §3.1**
- **Wrong:** §3.1 grants `gsn_engine` SELECT on `record_versions`, `releases`, `release_members`, etc. The parent spec (§6.1) says `engine` is "read-only on published data". This lets the M2 engine read rejected/draft releases, `override_reason`, `gate_report` and every old version, including content never published.
- **Why:** Least-privilege violation and a data-exposure path for unverified content through the public-facing engine.
- **Fix:** Expose only the active release through a `security_invoker=false` view owned by `gsn_migrate` (`v_active_records`) or a `SECURITY DEFINER` function, with a pinned `search_path`; grant the engine SELECT on that only.

**13. Major — Registering protected objects by OID breaks after a logical backup/restore — §8.1**
- **Wrong:** The registry stores table/trigger/function OIDs. `pg_dump`/`pg_restore` (the M4 "restore test") assigns new OIDs; the restored registry points at objects that do not exist and the watchdog rejects every subsequent DDL/migration.
- **Why:** The designed tamper protection makes a standard restore unusable; this is a future operational fire.
- **Fix:** Store a stable identity (schema + name plus a column/trigger signature) and/or provide a documented post-restore re-registration; include restore in the M4 test.

**14. Major — Two concrete guard bypasses: column changes and `ENABLE REPLICA TRIGGER` — §8.1**
- **Wrong:** The checks are "the table exists and every registered trigger exists and is enabled". (a) `ALTER TABLE audit_log DROP COLUMN row_hash` or `RENAME COLUMN` leaves the table and triggers in place, so it passes; the trigger then references a missing/renamed column. (b) `ALTER TABLE … ENABLE REPLICA TRIGGER` sets `tgenabled='R'`: the trigger is "enabled" but does **not** fire in normal sessions, so the owner can then UPDATE/DELETE.
- **Why:** Both defeat the append-only/hash guarantees without tripping the described guard, and the design's risk table only worries about the guard being too strict.
- **Fix:** Store a schema/column signature and reject non-additive changes to protected tables (or explicitly allowlist `ADD COLUMN`); require `tgenabled = 'O'` exactly, not merely "not disabled"; add both cases to the integration tests.

**15. Major — `active_release` can be changed without a `publications`/`audit_log` row — §3 / §6**
- **Wrong:** §3 says `active_release` is "updated only by publish/rollback", and §6 does the switch, the `publications` insert and the audit insert "in one transaction". But `gsn_worker` has direct UPDATE on `active_release` (§3.1). The database only enforces "must point at a validated release".
- **Why:** A bug or a compromised worker can silently change what the public site serves, with no publication row and no audit row; the audit chain cannot detect a missing append.
- **Fix:** A `SECURITY DEFINER publish_release(release_id, actor, reason)` that performs all three writes, with UPDATE revoked from the worker; optionally a trigger requiring a fresh matching `publications` row.

**16. Major — `source_verified` marks the one known-unverified ePassport record as verified — §3 / §4**
- **Wrong:** §3 defines `source_verified = not record.get("source_url_unverified", False)`. `eval_epassport.py:21-24` says `epassport-notice-dhaka-cantonment-jurisdiction` is "already flagged source_url_unverified: true in the corpus"; `data/chunks/epassport.jsonl:44` has `"source_url_unverified": false`. The design's derivation therefore stores `source_verified = true`.
- **Why:** The field whose purpose is to flag an unconfirmed source silently says the opposite; this is a data-integrity error, not a style issue.
- **Fix:** Resolve the corpus/docstring contradiction before release 1 (set the flag to `true` if the URL is unverified, or fix the docstring if it is verified), and add an integrity check that the eval's known-unverified record is flagged.

**17. Major — The parity criterion has no tie rule and mixes two different tolerances — §1 (criterion 2) / §7.3**
- **Wrong:** The pass condition is "identical top-k IDs in the same order" **and** "similarities within 0.001". pgvector's `ORDER BY embedding <=> $1 LIMIT k` has no secondary sort key, so exactly/ nearly equal scores can come back in any order; the legacy `retrieve()` relies on Python's stable sort and Chroma's arbitrary tie order. A 0.001 shift can also flip which service clears `SERVICE_MIN_SIM=0.40` or whether the top hit clears `GROUNDING_FLOOR=0.42`. The legacy eval `top_hits` round to 3 decimals (`eval_nid.py:82`), so a baseline built through `eval_one` loses precision.
- **Why:** The proof can fail for a legitimate 0.0005 difference near the rank-k boundary, while the 0.001 tolerance is too coarse to be a retrieval-quality statement. It also cannot be interpreted when order and similarity disagree.
- **Fix:** Add a deterministic tie-break (`ORDER BY distance, record_id`) and mirror it in the ported retrieval and baseline; compare top-k as sets plus per-ID similarities; treat order swaps where scores are within tolerance as ties (reported, not failed). Record unrounded similarities.

**18. Major — The baseline may not correspond to the committed corpora — §7.3 / §13**
- **Wrong:** The risk table only discusses library/platform drift. The baseline is a manual run against the owner's `data/vectordb`, which is absent from the repository (and gitignored). The corpora may have changed since the embedding run: `embed_ereturn.py:5` says "81 bilingual FAQ records", while `data/chunks/ereturn.jsonl` now has 83; the vectors are from July–August. If the collection is stale, the parity compares a release built from today's corpus with a baseline built from an older corpus.
- **Why:** A mismatch would be blamed on the port, or — worse — a stale collection would make the "parity" meaningless because "what users get today" is not what the release contains.
- **Fix:** Before generating the baseline, assert `collection.count()` and each document's text hash equals the commit's corpus; record `pip freeze`/`torch`/`sentence-transformers` and the loaded model files in the report; re-embed if stale.

**19. Major — Parity does not exercise the production multi-query / multi-service retrieval path — §7.3**
- **Wrong:** §7.3 runs `retrieve()` unscoped and scoped to one service. In production, `handle_turn` calls `answer(raw, …, queries=[u.query_bn, u.query_en, raw], services=u.services or None)` (`answer.py:373-375`) — multiple query embeddings and a subset of 2+ services. A bug in the multi-query merge or the `services` filter would not be caught.
- **Why:** This is the path M2 actually uses; the parity proof could pass while production retrieval is different.
- **Fix:** Add deterministic `queries=` / `services=` parity cases (2, 3 and 4 services; 2–3 queries), using ready-made query strings so no LLM is involved.

**20. Major — NID `fetched_at` is a timestamp; a strict "valid date" check fails all 102 NID records — §4.1 / §6**
- **Wrong:** §4.1 maps NID `verified_at` to `fetched_at`; §6 says a value that is "not a valid date, or is in the future, fails the build". Every NID record has `"fetched_at": "2026-07-12T23:04:19"` (`data/chunks/nid.jsonl:1`), and `datetime.date.fromisoformat` rejects it.
- **Why:** A natural strict implementation makes release 1 unbuildable.
- **Fix:** Parse as an ISO datetime and take `.date()` (or cast in SQL), and state that rule explicitly; add a fixture with a timestamp and with a future date.

**21. Major — "All on one commit" is self-contradictory, and M1 is too large for one packet — §1 / §7.3 / §10**
- **Wrong:** The report is written after the parity run and committed to `docs/reports/<date>-m1-parity.md`; committing it creates a new commit, so release 1's `source_commit` is the parent, not the report commit. Separately, the milestone bundles schema, guard watchdog, hash chain, CLI, ML packaging, baseline generation, parity, a new CI workflow, integration tests and M0 test minors.
- **Why:** The done criterion is unachievable in the stated order, and the tamper-protection/parity risks are the kind that need their own review points. A single commit makes bisecting and reviewing much harder.
- **Fix:** Split into (a) schema + build/publish + tests, (b) parity harness + baseline, (c) tamper protection, (d) packaging/CI. State "release 1 is built from the code commit; the parity report is a follow-up commit" (or allow a second release), and drop "all on one commit" from the finish line.

**22. Minor — NID record IDs are content-derived; a heading/URL edit changes identity — §3 / §6**
- **Wrong:** `build_nid_corpus.py:130` computes the ID as `sha1(f"{SERVICE}|{url}|{heading}|{body[:40]}")[:16]`. Changing a heading or source URL — or the first 40 characters of the body — produces a **new** `record_id`, so `record_versions` records "added + removed" rather than a new version. The design calls these "today's corpus IDs" and treats `record_id` as stable identity.
- **Why:** Version history and `gsn release show`'s "changed vs live" view are misleading for the NID corpus, and a content change can silently lose the version link.
- **Fix:** Document the behaviour and compare releases by `(record_id, content_hash)`; or introduce a stable `record_id` separate from the legacy chunk ID.

**23. Minor — `record` is JSONB, so "verbatim" and the canonical hash need a precise definition — §3 / §8.2**
- **Wrong:** JSONB normalises key order, whitespace and duplicate keys, so "the original record, every field, verbatim" is only semantically true. More importantly, `content_hash = sha256(canonical JSON)` does not say which serialiser: Python `json.dumps(..., sort_keys=True, ensure_ascii=False, separators=(",", ":"))` and PostgreSQL's `record::text` produce different bytes. Likewise "timestamps in UTC" needs `at AT TIME ZONE 'UTC'` explicitly, and a hash needs a version so adding a column later does not invalidate old rows.
- **Fix:** Specify the Python canonicalisation, never recompute from `jsonb::text`, add a `hash_version` constant/column, hash the `at` timestamp as UTC, and test a round-trip.

**24. Minor — Missing uniqueness/atomicity details — §3 / §6**
- **Wrong:** There is no unique index for the version-reuse key `(record_id, content_hash, embedding_model)`, and `release_members` does not prevent two versions of the same record in one release. The build "insert the release … reusing versions … embedding only new records" (`§6 step 3`) can hold a write transaction open while the CPU embeds 293 records.
- **Fix:** Add the unique indexes (and `(release_id, record_id)`), embed outside the write transaction, then insert the release and members in one short transaction.

**25. Minor — Several "M1 done" checks do not test what the text says — §7.1 / §10 / §11**
- **Wrong:** `test_multiturn.py` stubs `sentence_transformers`/`chromadb` and calls `handle_turn_registry(..., colls=None)` (`test_multiturn.py:28-37,65`), so it exercises no retrieval at all; the parent §10.2 gate ("every data release: … routing fixtures, generation eval") and §11 ("test_multiturn … match ChromaDB results") are not reflected in §11's "changes to the parent spec". `engine/tests/test_constraints.py:41-49` checks only `dependencies + dev`, not the new `[ml]` extra, so a torch mismatch will not be caught. The eval harness also needs the per-file rules that are not in one common list: BDRIS's `OUT_OF_SCOPE_CEILING` is **0.62**, not 0.55, and `eval_bdris.py` has no OBSERVE verdict.
- **Fix:** Call `test_multiturn` a routing test (not a parity check); add the §10.2/§11 changes to §11; extend `test_constraints.py` to the `ml` extra; list the per-file constants (0.55/0.62, no OBSERVE for BDRIS) in §7.1 and in the harness tests.

**26. Nit — The `.git` mount trust claim is unverified; `.git` also carries config**
- **Wrong:** §9.2 says "only `.git`: git history never contains `setenv_*.bat` or `.env`". The `.gitignore` covers both, and `deploy/tests/test_repo_hygiene.py:75-83` enforces the patterns, but this snapshot has no `.git`, so I could not verify history. `.git/config` can contain remote URLs with credentials. The release container has outbound network and DB credentials.
- **Fix:** Before mounting, run `git rev-list --objects --all` and reject `setenv_*.bat`/`.env` paths; set `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`; consider mounting a `git archive` of the target commit instead of the whole `.git` and restrict the container's network to postgres.

## Simpler alternatives

- **Tamper evidence:** a single-row `audit_chain` table with `UPDATE … RETURNING` is simpler than a trigger plus advisory lock, and it is concurrency-safe. The event-trigger watchdog is a lot of machinery; if its only job is to stop `gsn_migrate`, a dedicated NOLOGIN owner for the protected tables plus `SECURITY DEFINER` writers may be easier to reason about — but the owner still needs DDL, so the event trigger is defensible; the fixes in findings 13/14 are the minimum.
- **Parity:** compare the release's **293 stored vectors** with Chroma's stored vectors component-wise (max absolute difference) in addition to top-k/sims. If the vectors match, retrieval differences can only come from the query/maths, which narrows the proof dramatically. A one-off in-process comparison (Chroma + `gsn.retrieval` in the same venv) also avoids the Linux/Windows/container model risk.
- **Packaging:** a separate `release` image target with the ML extra; do not put `gsn[ml]` in the runtime/test images until M2.
- **Registry:** store a hash, not a byte-for-byte JSONB copy.

## Claims checked and found correct

- **Corpus size and integrity:** 102 NID + 66 ePassport + 83 eReturn + 42 BDRIS = **293**, matching §2 (`docs/PROJECT_STATUS.md:25` and a direct count). All 293 record IDs are unique across corpora; there are no duplicate texts, no `TODO(verify)`, no empty `source_url`, and every `bn`/`bn+en`/`mixed` record contains Bengali script.
- **Gold counts:** 18 + 21 + 17 + 18 = **74**; all files use `top_k: 4`; every `expected_id` exists in its corpus; the category/subcategory values exist.
- **`answer.py` constants:** `CANDIDATE_N=8`, `PER_SERVICE_K=2`, `SERVICE_MIN_SIM=0.40`, `MAX_CONTEXT=6`, `SCOPED_TOP_K=8`, `GROUNDING_FLOOR=0.42` (`answer.py:46-53`) exactly match §7.2. `ALLOWED_COLLECTIONS` is `{nid, bdris, epassport, ereturn}` (`:59`), and `url_kind()` uses the markers described (`:134-142`).
- **Embedding inputs:** all four embed scripts embed the record's `text` (bge-m3, no E5 prefix) and create cosine collections (`embed_nid.py:81-97`, `embed_epassport.py:83-99`, `embed_ereturn.py:96-112`, `embed_bdris.py:115-131`). `1 − distance` is the correct Chroma-to-pgvector cosine map.
- **`verified_at` fields:** NID has `fetched_at` on all 102 records; ePassport has `last_crawled` on all 66; eReturn/BDRIS have neither; `last_updated` is ePassport-only and is not used as `verified_at`.
- **Baseline arithmetic:** the CLAUDE baseline's 60 PASS + 6 warn + 3 FAIL + 5 OBSERVE = 74, and the three fails being Banglish is consistent with the gold files (I could not verify the specific IDs without embeddings).
- **DB/bootstrap:** `10-roles.sh` creates the four LOGIN roles, the `gsn` database owned by `gsn_migrate`, and the `vector` extension (`db/init/10-roles.sh:25-41`). `sqlx::migrate!("../db/migrations").run(&pool)` runs as `gsn_migrate` (`web/src/main.rs:101-111`). `pgvector/pgvector:0.8.7-pg17-bookworm` (`compose.yaml:26`) supports `vector(1024)`.
- **Event-trigger placement:** creating event triggers requires the superuser; putting them in `db/init` (before migrations) is the right place, and raising in `ddl_command_end`/`sql_drop` does roll the DDL back. The OID-vs-rename reasoning is right for tables (but not columns — finding 14).
- **M0 audit log:** `audit_log` is append-only with column-level INSERT and rejects the owner (`0001_audit_log.sql`; `test_database.py:110-123`); `gsn_worker` deliberately has no SELECT.
- **`db/init` only on an empty volume:** confirmed by `10-roles.sh:3-4`; the design's `docker compose down -v` note is correct.
- **Secret-file ignore rules:** `.gitignore` covers `setenv_*.bat` and `.env`, and `test_repo_hygiene.py` enforces them (history itself was not verifiable in this snapshot).

## Things I could not verify here (marked unsure)

- That `sentence-transformers 5.6.0` / `torch 2.13.0` are actually the owner's PC versions: `requirements_phase2.txt` only says `>=3.0.0`, and there is no lockfile for the legacy environment.
- The model revision `BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181` and the claim that the PC loaded exactly those weights (no model/network in this snapshot).
- The specific three failing gold query IDs (no Chroma/embeddings here); only the count and language are consistent.
- Docker Desktop's exact ownership mapping for the `.git` bind mount (the `safe.directory` fix is harmless regardless).
- Whether `USAGE` on `audit_log_id_seq` is truly unnecessary for the identity column: PostgreSQL identity columns use `NextValueExpr` (no sequence ACL check), so §8.3 is plausible, but I did not run PostgreSQL; `tests/integration/test_grants.py:39,46` currently asserts `USAGE` and must be updated either way.
- Git history for `setenv_*.bat`/`.env`: no `.git` exists in this snapshot.

## Overall assessment

The design is well grounded: the corpus counts, fields, retrieval constants and eval rules I checked match the code and data, and the core decision to build immutable releases from committed git objects is sound. The blocking issues are concentrated in the tamper-protection chapter and the parity proof: the audit trigger cannot read the table as the worker, the hash chain's ordering is not concurrency-safe, the `SECURITY DEFINER` functions are unhardened, and the `retrieve()` parity cannot be reproduced because the baseline depends on LLM normalisation the release side deliberately omits. Several packaging/CI details (the compose service-set test, the model volume, the CPU torch index) will also fail on the owner's PC or in CI as written. I would not implement M1 as one commit/packet: fix findings 1–5 first, split the work into schema/build, parity and tamper-protection packets, and make the Chroma baseline a first-class, verifiable artifact rather than a side report.
