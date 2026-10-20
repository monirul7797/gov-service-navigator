# G3 Approved Publication Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` if available. Execute only the released packet/subpacket; commit, report and stop for Codex review.

**Goal:** Publish an explicitly approved e-Passport patch without exposing mixed corpus/index versions, with effective dates and rollback.

**Architecture:** Immutable approved inputs produce a staged corpus/index/registry manifest. Verify it, then atomically switch one SQLite pointer. Every reader pins that release; legacy writers cannot mutate managed collections.

**Tech stack:** Python, SQLite, existing Chroma and BGE-M3, unchanged metadata builders and deterministic routing.

**Spec:** [Design](../specs/2026-09-05-tracker-evidence-update-design.md); [control guide](2026-09-05-glm-implementation-control.md).

## Global constraints

G2 must be accepted. All control-guide constraints apply. This entire phase runs in isolated test corpus/vector/maintenance roots. Creating a local commit is not permission to migrate live collections or publish real govt guidance. Production cutover is a distinct operator action after Codex accepts the pilot.

No direct collection rename or cross-store transaction assumption. Immutable published collections are never updated/deleted in place. BGE-M3 documents use no E5 prefix, normalized embeddings, existing batch size16 and cosine collection space observed in `embed_epassport.py`; verify installed library behavior without changing those settings. Metadata matches `freshness.metadata.build_metadata`. Report the installed model/library revision; missing cached model blocks the real-model gate rather than triggering a download.

Run cumulative offline and exported-commit checks for each packet. Real Chroma integration explicitly uses a fresh temp vector directory and disables telemetry/network; if the installed library still attempts networking, fail and diagnose rather than weakening the guard. Never instantiate `PersistentClient` at the live `data/vectordb` in tests.

## P15 — Exact approvals and revision validity

**Create:** `maintenance/approvals.py`, `tests/maintenance/test_p15_approvals.py`; modify models/store/migrations, proposal UI/CLI; report.

**Interfaces:**

```python
approve_patch(store, patch_id: str, *, selected_item_ids: list[str],
              actor: str, now: str, activate_at: str | None) -> dict
check_approval(store, approval_id: str, *, current_records: list[dict],
               evidence_revisions: dict, now: str) -> dict
revoke_approval(store, approval_id: str, *, actor: str, reason: str, now: str) -> dict
```

Approval row binds exact canonical patch hash, selected dependency-complete operations, complete base/dependency record hashes, evidence/extraction versions, effective date, actor/time and authorization. Hash is recomputed from stored patch content at approval/publication boundaries; never trust a caller-supplied hash string alone. `check_approval` returns valid/waiting_effective/needs_review/revoked with reasons; unknown/missing state fails closed. An unrelated release ID alone does not revoke approval.

- [ ] **1. Add exact-hash/stale-input tests.** Introduce `approved_case(store)` fixture that stores an actual immutable P13 patch, evidence/extraction revisions and four complete e-Passport fixture records, then approves it through the public API. Return `approval_id`, `records`, `evidence_revisions` and `patch_id`; do not insert a fabricated approved flag directly in SQL.

```python
def test_changed_target_invalidates_approval(self):
    from maintenance.approvals import check_approval
    from tests.maintenance.fixtures import approved_case
    case = approved_case(self.store)
    case['records'][0]['text'] = 'changed by another update'
    result = check_approval(self.store, case['approval_id'],
        current_records=case['records'], evidence_revisions=case['evidence_revisions'],
        now='2026-09-05T03:00:00Z')
    self.assertEqual(result['status'], 'needs_review')
```

- [ ] **2. Implement validation and UI approval.** Show old/new full rows, evidence, scope and scheduled date. Selecting dependent bilingual edits is atomic. Corrections after approval create a new patch requiring review; unrelated additions elsewhere retain original approval if target/dependency/evidence hashes match. Relevant new contrary evidence holds a pending approval even when the old file remains readable. Mere source unavailability/last-checked changes do not expire an otherwise valid stored official artifact.
- [ ] **3. Add A25-A27/A39 cases.** Forged/missing ID, changed approved payload, missing evidence, unknown base hash, revocation, incomplete dependent selection, future date and unrelated record update. User consent in UI is necessary; CLI approval requires explicit patch ID/hash confirmation arguments, not a blanket `--approve-all`.
- [ ] **4. Run checks, commit and stop.** Approval exists; no staging/publication yet.

## P16 — Release bootstrap and immutable staging

**Create:** `maintenance/releases.py`, `maintenance/staging.py`, `maintenance/vector_adapter.py`, `tests/maintenance/test_p16_staging.py`, `tests/integration/test_release_staging.py`; modify store/migrations/fixtures; report.

**Interfaces:**

```python
bootstrap_release(store, *, corpus_paths: dict, registry_path: Path,
                   vectors, now: str) -> dict
stage_release(store, approval_id: str, *, vectors, embed, now: str) -> dict
```

`vectors` is an adapter with `read_collection(name) -> list[dict]`, `create_collection(name, settings: dict) -> None`, `add_records(name, records: list[dict]) -> None`, `settings(name) -> dict`; records contain id/document/metadata/embedding. It wraps an explicitly supplied Chroma client, not a live default. `embed(texts: list[str]) -> list[list[float]]` is injected; production uses existing BGE-M3 configuration. Read/copy in bounded batches; conversion handles numpy values without JSON type errors.

Manifest fields: schema_version, release_id, parent_release_id, approved_change_set_ids, service_map with snapshot hash/path, collection name, complete-record/document/vector metadata hashes, model/settings fingerprint and managed flag; registry snapshot/hash; verification state and creation time. Registry snapshot is carried unchanged in this pilot. Bootstrap validates full corpus/vector ID/document/metadata parity and marks provenance legacy; it does not certify freshness. Unmanaged service references remain fixed logical legacy entries until later service gates.

- [ ] **1. Write staging isolation/reuse tests.** Base fixture has unchanged record, metadata-only record, changed record and retired record; patch adds one record. Track embed calls. Only changed/new text is encoded; unchanged and metadata-only embeddings copied exactly. A model/settings mismatch blocks reuse and returns needs_review; do not silently re-embed the whole corpus.

```python
def test_staging_does_not_switch_active_release(self):
    from maintenance.staging import stage_release
    from tests.maintenance.fixtures import staged_case
    case = staged_case(self.store)  # extends approved_case with bootstrap and fake vectors
    before = self.store.active_release_id()
    result = stage_release(self.store, case['approval_id'],
        vectors=case['vectors'], embed=case['embed'], now='2026-09-05T03:00:00Z')
    self.assertEqual(self.store.active_release_id(), before)
    self.assertEqual(result['status'], 'staged')
```

Add `MaintenanceStore.active_release_id() -> str | None` here. `staged_case` must call real bootstrap/approval services; it must not bypass them with flags. Bootstrap initializes the first isolated active baseline only after parity checks; this is distinct from activating a proposed patch.

- [ ] **2. Implement staging and lease fencing.** Use P08 publisher lease/token, immutable release UUID, full-hash files, physical name `epassport_r_<uuidhex>`. Write corpus/registry snapshots before referencing them. A partial collection is unpublished and resumable only for that job; verify existing IDs/hashes on retry, reject mismatches. Keep old active ID pointer unchanged. Materialize complete replacement rows, not text-only patches.
- [ ] **3. Test missing corpus IDs, metadata mismatch, retired ID exclusion, dimensions, nonfinite vectors, write failure and retry.** Unknown live metadata prevents bootstrap acceptance. Other services' manifest references must be carried unchanged.
- [ ] **4. Run real Chroma staging integration.** `python -B -m unittest discover -s tests/integration -p 'test_release_staging.py' -v`. Use known deterministic vectors in a real temporary Chroma store to verify copying/settings/metadata/ID behavior without downloading a model. Print library version and exact settings. This does not satisfy the later BGE-M3 retrieval gate.
- [ ] **5. Run cumulative/export checks, commit and stop.** A28/A29/A31 preparation evidence; no candidate activation.

## P17 — Candidate verification and atomic publication

**Create:** `maintenance/verify.py`, `maintenance/publish.py`, `tests/maintenance/test_p17_publication.py`, `tests/integration/test_release_publication.py`; modify releases/store/migrations; report.

**Interfaces:** `verify_release(store, release_id: str, *, vectors, retrieval_check) -> dict`; `publish_release(store, release_id: str, *, vectors, retrieval_check, now: str) -> dict`; `recover_publication(store, job_id: str) -> dict`.

`retrieval_check(manifest: dict) -> dict` returns passed boolean and named check results; it cannot return a bare True that bypasses structural checks. `verify_release` always performs ID/content/metadata/dimension/finite-value/registry/evidence validation itself. Store the verification report hash and candidate manifest hash it covers. A changed candidate invalidates verification.

- [ ] **1. Add fail-before-switch and successful-switch tests through public publisher.**

```python
def test_candidate_mismatch_keeps_previous_release(self):
    from maintenance.publish import publish_release
    from tests.maintenance.fixtures import prepared_case
    case = prepared_case(self.store)  # real P16 stage followed by injected corrupted vector document
    old = self.store.active_release_id()
    case['vectors'].corrupt_document(case['collection_name'], case['record_id'])
    result = publish_release(self.store, case['release_id'], vectors=case['vectors'],
        retrieval_check=case['retrieval_check'], now='2026-09-05T03:00:00Z')
    self.assertEqual(result['status'], 'verification_failed')
    self.assertEqual(self.store.active_release_id(), old)
```

`prepared_case` builds a valid staged case; the explicit corruption in the test is the fault. Fake-vector `corrupt_document` is test-only and never exported by the production adapter.

- [ ] **2. Implement commit protocol.** After independent validation, recheck approval, affected base hashes, evidence, effective time and current pointer. In a single short transaction, recheck the database approval/evidence/conflict revision tokens against those validated, fence the job owner, compare-and-swap pointer, record release/job result and enqueue publication notification. This closes the race where relevant evidence changes after validation but before the pointer switch. No file/vector writes in that transaction; immutable verified artifact hashes bind the external data. A CAS failure returns restage_required if unrelated inputs remain valid, otherwise needs_review. The worker may restage/reverify the same authorized patch, preserving other changes; it must not simply change the parent ID on a stale snapshot.
- [ ] **3. Add fault injection at every stage boundary.** Crash before/after collection fill, verification, pointer transaction, notification and compatibility export. A precommit failure leaves old active; a postcommit restart detects committed new release and does not double-notify. Instrument an optional injected fault hook in application service tests, not global production environment variables allowing bypasses.
- [ ] **4. Run real Chroma publication tests and cumulative/export checks.** Assert full old-or-new ID/hash sets after reopening database/client. No mixed active guidance. A missing approval/source hash never enables an optional-guard bypass. A malformed job row is quarantined with a safe identifier, and a later valid independent job still runs.
- [ ] **5. Commit and stop.** A25/A27/A30/A31/A39 and replacement semantics for R7/R9. No production pointer switch.

## P18 — Release resolver and primary chatbot readers

**Create:** `src/knowledge_release.py`, `tests/maintenance/test_p18_readers.py`; modify `answer.py`, `app.py`, `disambiguate.py`; report.

**Interfaces:** `pin_release(*, maintenance_root: Path | None, legacy_registry_path: Path, binding_path: Path | None=None) -> dict`; `resolve_collections(client, release: dict, *, allowed_services: tuple[str,...]) -> dict`; `registry_for_release(release: dict) -> dict`. Managed activation is an explicit cutover action, represented by an on-disk binding as well as optional `GSN_MAINTENANCE_ROOT` configuration. Missing/unavailable configured managed state is an error, not silent fallback to old guidance. Only an unbound, unconfigured installation retains legacy behavior.

Binding schema v1 contains canonical maintenance_root, vector_root, corpus export paths and managed service names. The cutover design uses `managed_knowledge.json` inside the vector root and `.managed_knowledge.json` in each protected corpus export directory, with identical binding ID/hash. P18 reads supplied/anchored bindings; P19C implements validation/writer enforcement. Tests create bindings only in temporary roots. A marker/config mismatch or unreadable marker fails closed. Removing an environment variable cannot make bound knowledge unmanaged. Creating live bindings is reserved for the separate production cutover packet after G3 acceptance.

- [ ] **1. Add resolver and handle-turn tests.** Two releases with different e-Passport data: one in-flight turn pins old; a later turn sees new. Use the actual `answer.handle_turn` entry point with fake model/backend and injected collections, not only a resolver assertion. Verify output citations/records and unchanged service routing.

```python
def test_old_pin_stays_consistent_after_switch(self):
    first = pin_release(maintenance_root=self.root, legacy_registry_path=self.registry)
    self.publish_second_fixture_release()  # helper uses P17 public API
    second = pin_release(maintenance_root=self.root, legacy_registry_path=self.registry)
    self.assertNotEqual(first['release_id'], second['release_id'])
    self.assertEqual(first['registry_hash'], self.first_registry_hash)
```

- [ ] **2. Integrate primary readers.** `answer.load_collections` takes an optional pinned release and resolves only approved logical service names; never list all historical physical collections as a fallback. `app.load_resources` caches backend/model independently; per-turn registry/collection resolution keyed by release ID. Do not freeze release selection at app startup. `disambiguate.load_registry` uses the pinned snapshot path when supplied; decision algorithm/thresholds stay unchanged.
- [ ] **3. Test unbound/no-config compatibility, bound state with environment variable absent, corrupt managed pointer failure, cached Streamlit rerun and registry/corpus consistency.** Reader call sites pass their actual vector-root binding path; no binding is discovered by scanning unrelated directories. A registry patch is not authored here; immutable snapshot parity is tested using controlled fixture releases. Actual new routing concepts remain blocked for separate architectural review.
- [ ] **4. Run cumulative/export checks and manual UI test against isolated release data.** No live Chroma use. Commit and stop. A32/A36/A40 reader consistency.

## P19 — Auxiliary readers and mutation guards

This packet is released as **three separately reviewed subpackets**, each with its own commit/report/exported tests. Do not complete P19B or P19C while dispatched only P19A.

### P19A — Standalone query readers

**Modify only:** `query_nid.py`, `query_epassport.py`, `query_ereturn.py`, `query_bdris.py`; create `tests/maintenance/test_p19a_query_readers.py`; report.

**Consumes:** P18 resolver. Keep existing positional questions/CLI behavior; add optional explicit historical-release selection only through the resolver, not arbitrary collection names.

- [ ] Add tests invoking each script's main path with fake model/client and explicit temp managed root. Assert logical service resolves active physical collection and no historical list fallback.
- [ ] Replace literal collection lookup with the common pinned resolver. Preserve query normalization/model settings/thresholds.
- [ ] Test unmanaged compatibility and missing configured managed state; run cumulative/export checks, commit and stop.

```python
# Required cross-reader assertion; execute separately for each query script.
self.assertEqual(client.requested_collection_names, [active_physical_name])
self.assertNotIn(retired_physical_name, client.requested_collection_names)
```

### P19B — Evaluation readers

**Modify only:** `eval_nid.py`, `eval_nid_rerank.py`, `eval_epassport.py`, `eval_ereturn.py`, `eval_ereturn_frontdoor.py`, `eval_bdris.py`; create `tests/maintenance/test_p19b_eval_readers.py`; report. `eval_normalize.py` is inspected read-only and changed only if it actually reads collections; otherwise report no change needed.

- [ ] Add tests that run the evaluation initialization path with P18 resolver and assert report includes release ID, registry hash and model/settings fingerprint.
- [ ] Pin one release for the entire evaluation batch for comparable scores; preserve scoring logic and gold-query files.
- [ ] Verify active/historical explicit choice, no-config compatibility and no vector writes. Run cumulative/export checks, commit and stop.

```python
self.assertEqual(len(set(row['release_id'] for row in evaluation_rows)), 1)
self.assertEqual(evaluation_report['release_id'], selected_release_id)
```

### P19C — Block direct mutation of managed knowledge

**Create:** `maintenance/write_guard.py`, `tests/maintenance/test_p19c_writer_guards.py`. **Modify:** `embed_nid.py`, `embed_epassport.py`, `embed_ereturn.py`, `embed_bdris.py`, `freshness/reembed.py`, `patch_epassport_process.py`, `seed_corpus.py`; report. Inspect their actual write targets before changes; guard only relevant managed knowledge paths. If another writer is found, report it to Codex for an explicit follow-up scope rather than declaring coverage complete.

**Interface:** `require_unmanaged_or_staging(*, service: str, maintenance_root: Path | None, target: Path, staging_job_id: str | None) -> None`. Resolve the actual target and read its P18 managed binding: vector-root marker for collection operations, containing corpus-directory marker for JSONL operations. Explicit maintenance_root must agree with a binding when one exists; absent environment/config cannot bypass a discovered binding. Malformed/unreadable/conflicting bindings block the write. This is defense against accidental use of maintained entry points, not a filesystem access-control boundary. Staging permission requires a real active job and exact target match; a string named staging is insufficient. The new publisher writes through its own adapter/lease and does not shell out to legacy embed scripts.

- [ ] Add production-entry tests with spy clients/writers. A managed e-Passport target must fail before delete/upsert/model encoding or corpus overwrite, including with `GSN_MAINTENANCE_ROOT` absent. Every maintained writer uses its actual resolved target, not a caller-selected harmless path for the guard.
- [ ] Insert guards before any mutation. Preserve unmanaged workflows; no repair of unrelated legacy queue semantics. Check compatibility exports for manual drift before importing, never treat edited export as published truth.
- [ ] Run cumulative/export checks, commit and stop. No test calls a real legacy full-embed routine against live directories.

```python
with self.assertRaises(PermissionError):
    require_unmanaged_or_staging(service='epassport', maintenance_root=self.root,
        target=self.live_fixture_target, staging_job_id=None)
self.assertEqual(vector_spy.mutations, [])
```

## P20 — Effective dates, rollback and pilot acceptance

Release as **P20A and P20B separately**. Both follow accepted P19C.

### P20A — Scheduled activation and operator result

**Create:** `maintenance/activation.py`, `maintenance/ui/history.py`, `tests/maintenance/test_p20a_activation.py`. Modify worker/CLI/maintenance_app/store only for activation wiring; report.

**Interface:** `activate_due_once(store, *, now: str, vectors, embed, retrieval_check) -> dict`. Add CLI `prepare-release --approval-id`, `publish-release --release-id`, `history`; production tests use explicit roots and public functions. UI shows stages, revisions, delayed activation and verification results.

- [ ] Write before-date/after-wake/superseded-evidence tests with fake clock. Date-only commencement uses Asia/Dhaka midnight only when original wording establishes that date; ambiguous times hold for review.
- [ ] Compose P15-P17 checks; no extra approval for unchanged authorized patch. Relevant changes hold; unrelated release restages; expired worker cannot publish. Approval explicitly includes scheduled activation.
- [ ] Test export failure after commit reports active new release plus export error; no automatic pointer reversal. Run cumulative/export checks, commit and stop.

```python
self.assertEqual(before_due['activated'], 0)
self.assertEqual(after_wake['activated'], 1)
self.assertTrue(after_wake['results'][0]['activation_delayed'])
```

### P20B — Rollback and complete isolated pilot

**Create:** `maintenance/rollback.py`, `tests/maintenance/test_p20b_rollback.py`, `tests/integration/test_epassport_publication_pilot.py`, `docs/MANAGED_PUBLICATION.md`. Modify history UI/CLI for rollback wiring; report.

**Interfaces:** `preview_rollback(store, target_release_id: str, *, now: str) -> dict`; `rollback(store, preview_hash: str, *, actor: str, now: str) -> dict`. Preview includes current/target release hashes, changed services/records, later changes that would be lost and applicability issues. Rollback checks preview still matches the current pointer, keeps history and records operator authorization. Expired/superseded target guidance blocks rollback pending a new reviewed recovery patch; never silently restore it as current.

- [ ] Write rollback tests after a later update; stale preview fails, valid preview switches one pointer, active/in-flight historical references remain readable. No collection/file deletion.
- [ ] Implement history, operation outcomes and rollback command `rollback --preview-hash`. Show storage usage; cleanup is out of scope.
- [ ] Run the full pilot against isolated corpus/vector data: proposal/no change; approved scoped replacement; addition under existing routing concept; explicit retirement; future activation; relevant stale approval; unrelated rebase; crash recovery; rollback. All go through CLI/UI application services.
- [ ] Run real cached BGE-M3 and Chroma multilingual checks: existing e-Passport gold queries plus targeted old/new fee/citation cases in Bangla, English and Banglish. Compare against a baseline using the same model/settings and report per-query regressions. Also run existing deterministic four-service tests and relevant reader integration checks. If model/cache unavailable, report incomplete real-model gate; synthetic embeddings are insufficient for G3 acceptance.
- [ ] Run cumulative and candidate-export tests, inspect UI results, commit/report and STOP. No live cutover or scheduler registration. Codex decides G3 acceptance and the separate production rollout packet.

```python
self.assertEqual(preview['current_release_id'], current_id)
self.assertIn(later_changed_record_id, preview['records_reverted'])
# A pointer change after preview must invalidate the exact preview hash.
self.assertEqual(stale_rollback_result['status'], 'stale_preview')
```

## G3 acceptance and remaining scope

A25-A34, A36, A39 and A40's registry/corpus consistency are covered here; A35 remains a live Visa publication block. A40's positive publication of a newly approved routing concept is deferred to a separate routing-change plan, because the specification excludes it from this pilot. P18 must prove pinning and P13/P17 must reject unapproved/new-concept patches; do not claim that deferred capability shipped.

No known R7/R9 optional-guard behavior may be reachable through new application entry points. Recovery must be proven with a real temporary Chroma store. Exact patch approval, immutable evidence, full-row parity and reader refresh all gate publication; passing helper tests alone does not accept the phase.
