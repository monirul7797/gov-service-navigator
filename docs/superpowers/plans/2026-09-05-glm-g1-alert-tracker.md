# G1 Alert Tracker Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` if available. Execute only the released packet; commit, report and stop for Codex review.

**Goal:** Deliver a local alert inbox with trustworthy source health, prioritized news leads and official verification links.

**Architecture:** Sensors produce observations; bounded interpretation produces attributed claims; the event service groups them; a transactional outbox supplies the separate maintenance UI. None of these modules can publish corpus data.

**Tech stack:** Python, sqlite3, unittest, existing requests/Playwright/BeautifulSoup/PyYAML/Streamlit and `LLMBackend.complete`.

**Spec:** [Design](../specs/2026-09-05-tracker-evidence-update-design.md); [control guide](2026-09-05-glm-implementation-control.md).

## Global constraints

All control-guide constraints apply. G0 must be accepted first. The production chatbot and corpus paths stay untouched. Source registry entries start disabled until inspected and tested. No automatic scheduler installation or external notifications. Every packet runs `python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'` and, after local commit, `python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'`.

## P04 — Validated contracts, store and immutable artifacts

**Create:** `maintenance/__init__.py`, `maintenance/models.py`, `maintenance/store.py`, `maintenance/migrations.py`, `maintenance/artifacts.py`, `tests/maintenance/test_p04_store.py`. Modify shared fixtures; add packet report.

**Public interfaces:**

```python
canonical_hash(value: dict) -> str
validate_observation(value: dict) -> dict  # normalized copy; ValueError on invalid input
MaintenanceStore(root: Path)              # explicit root, no import-time I/O
MaintenanceStore.initialize() -> None
MaintenanceStore.close() -> None
MaintenanceStore.record_observation(value: dict) -> dict  # stored normalized row
MaintenanceStore.get_observation(observation_id: str) -> dict
put_artifact(root: Path, content: bytes) -> str            # full SHA-256, immutable
read_artifact(root: Path, digest: str) -> bytes             # rechecks digest
```

Observation schema v1 required keys: `schema_version=1`, `observation_id`, `source_id`, `role`, `requested_url`, `final_url`, `observed_at`, `published_at`, `updated_at`, `content_access`, `content_hash`, `artifact_hash`, `title`, `text`, `extraction_version`, `services`, `topics`, `scope`, `retrieval_outcome`, `coverage_complete`, `supporting_spans`. Nullable unknown dates/URLs/hashes use null, not invented values. `services` is drawn from nid/epassport/ereturn/bdris/visa. `content_access` is full_text/snippet_only/headline_only/none. Each span has `start`, `end`, `quote`; offsets reference the exact stored text and must round-trip. Retrieval outcome is ok/unchanged/blocked/failed/deferred/incomplete plus a reason. A failed observation cannot claim full text without a retained artifact and explicit provenance.

Sensor-to-store calls may include an ephemeral `_artifact_bytes` field: normalized source text encoded as UTF-8 for text observations, original PDF bytes for binary observations. `record_observation` writes/verifies those bytes with `put_artifact`, checks supplied hashes, strips the ephemeral field and commits the reference with the observation. No bytes enter JSON columns. A failure observation with no content has null content/artifact hashes. The `observation()` fixture supplies matching text/bytes/hashes. Nonempty source_id is required here; registry membership is checked by P05/P08. Do not add a source-table FK that makes the documented standalone observation fixture impossible to insert.

Initialize schema_migrations, observations, artifacts, source_health, events, event_observations, event_history, jobs, outbox, import_runs and quarantined_rows tables. Use FK constraints; JSON payload columns keep versioned records, while identity/time/status fields needed for queries are indexed columns. Do not create approval/publication behavior in this packet. Later migrations add those tables without destructive recreation.

- [ ] **1. Write a persistence/reload regression.** Add `observation(**overrides)` fixture that constructs all required v1 fields with a stable synthetic passport proposal and valid span. `content_hash` is the hash of source text bytes; later ingestion owns artifact storage. Test insert dedup by `(source_id, content_hash, extraction_version, requested_url, content_access)` and preserve later check timestamps in health rather than duplicate unchanged observations.

```python
def test_observation_survives_reload(self):
    from maintenance.store import MaintenanceStore
    from tests.maintenance.fixtures import observation
    with tempfile.TemporaryDirectory() as td:
        store = MaintenanceStore(Path(td)); store.initialize()
        saved = store.record_observation(observation())
        again = MaintenanceStore(Path(td)).get_observation(saved['observation_id'])
        self.assertEqual(again['content_hash'], saved['content_hash'])
```

- [ ] **2. Implement schema/validation and transaction boundaries.** Roll back the whole observation+artifact-reference operation on FK/validation failure. Idempotent migrations use schema version checks inside a transaction; an unsupported newer version is an error, never a reset. Hash collisions/path-content mismatches fail without overwrite.
- [ ] **3. Add failure tests.** Simulate write failure before commit, invalid span, NaN hash payload, malformed UUID, missing source_id, repeated artifact bytes and an existing mismatched artifact file. Assert old database rows/bytes unchanged; valid later work still succeeds.
- [ ] **4. Run targeted/cumulative/export checks, commit and stop.** No app or model imports allowed in store tests.

## P05 — Registry and bounded observation adapters

**Create:** `config/tracker_sources.yaml`, `maintenance/sources.py`, `maintenance/sensors/__init__.py`, `maintenance/sensors/official.py`, `maintenance/sensors/news.py`, `tests/maintenance/test_p05_sensors.py`, `tests/fixtures/tracker/` HTML/feed fixtures. Modify models/fixtures; report.

**Interfaces:**

```python
load_sources(path: Path) -> list[dict]
observe_official(source: dict, *, fetch, render, robots, previous: dict | None,
                 now: str) -> dict
observe_news(source: dict, *, fetch, now: str) -> list[dict]
```

`fetch(url, headers) -> dict` returns status, requested_url, final_url, redirect_chain, content_type, body bytes and failure reason; P02 production adapter enforces host policy. `render(url) -> RenderResult` uses P03. `robots(url) -> dict` uses the explicit unattended decision API. Sources carry registry fields from spec section 4 plus exact host policy, `method`, `verified_at`, `enabled` and `validation_evidence`. Registry validation rejects enabled/unverified sources or missing policy. Observation adapters return P04 payloads, with byte artifacts persisted by the orchestrator in P08, not written to corpus files.

- [ ] **1. Create explicit disabled source entries.** Use the already observed epassport fee page and dip/ssd starting pages. Seed other official candidates from existing source registry/provenance. Add the four named news publisher homepages as disabled discovery candidates, not invented feeds. Live endpoint selection is P09 evidence work. Read-only parsing of legacy source files is allowed; never mutate them.
- [ ] **2. Write tests for source changes and failure outcomes.** Fixture HTML includes a notice index with two actual anchor URLs, a boilerplate timestamp and a bot challenge. Preserve relevant text changes; ignore known boilerplate-only noise. A 304 with no previous baseline is incomplete, never unchanged. A partial notice index cannot emit disappearance.

```python
def test_disabled_source_never_fetches(self):
    from maintenance.sensors.official import observe_official
    from tests.maintenance.fixtures import source
    def forbidden(*args, **kwargs):
        raise AssertionError('disabled source contacted')
    result = observe_official(source(enabled=False), fetch=forbidden,
        render=forbidden, robots=forbidden, previous=None, now='2026-09-05T03:00:00Z')
    self.assertEqual(result['retrieval_outcome']['status'], 'deferred')
```

- [ ] **3. Implement adapters.** Use repaired quality/diff functions as pure helpers; do not call legacy `KnownSourceMonitor.run` to write a second JSON state store. Parse bounded RSS/Atom via stdlib XML and supplied publisher page fixtures via BeautifulSoup. Full-article fetch must obey the same policy; unavailable article retains headline/snippet provenance. Reject DOCTYPE/entity declarations before parsing and bound input size; XML external references are not followed. Carry HTTP validators, extraction version, requested/final URLs, link discoveries and coverage reasons. Limit response size to 10 MiB and at most 20 article candidates per source run, with explicit incomplete coverage on truncation.
- [ ] **4. Add tests for incomplete pagination, PDF byte changes, TLS/403/429, robots defer, missing publication date, unrelated-domain link and missing source policy.** Only actual observed official links are direct-link candidates.
- [ ] **5. Run checks, commit and stop.** This packet creates fixture-tested adapters, not a live-monitoring claim.

## P06 — Interpretation, priority and official links

**Create:** `maintenance/interpretation.py`, `maintenance/priority.py`, `maintenance/verification_links.py`, `tests/maintenance/test_p06_interpretation.py`; modify models and fixtures; report.

**Interfaces:**

```python
interpret(observation: dict, *, backend) -> dict
assign_priority(claim: dict, *, now: str) -> dict  # priority, reason, action
verification_links(claim: dict, sources: list[dict],
                   inspected_links: list[dict]) -> list[dict]
```

Claim required fields: `observation_id`, `services`, `topic`, `scope`, `issuing_authority`, `circular_id`, `policy_status`, `evidence_status`, `effective_at`, `consequential_current_change`, `summary`, `excerpt`, `supporting_spans`, `interpretation_status`, `issues`. Unknown values remain null/unknown. `interpretation_status` is ready/review_required/failed. Model output is a proposal; schema/span/URL validation is deterministic. Backend uses existing `.complete(system,user,max_tokens,temperature)` only, injected in tests. No query-router changes.

- [ ] **1. Add a proposal priority test before implementation.** Add `claim(**overrides)` fixture with complete valid fields.

```python
def test_proposal_does_not_become_fee_change(self):
    from maintenance.priority import assign_priority
    from tests.maintenance.fixtures import claim
    result = assign_priority(claim(policy_status='proposed', effective_at=None),
                             now='2026-09-05T03:00:00Z')
    self.assertEqual((result['priority'], result['action']), ('Watch', 'watch'))
```

- [ ] **2. Implement bounded model interpretation.** Delimit article text as untrusted evidence. Request structured JSON only; validate with P04 primitives. One retry for invalid output, then review_required; never tools/agent loops. Use at most 12,000 source characters per call, explicitly mark truncation. Excerpts are actual substrings, at most 25 whitespace-delimited words total per source on an alert. Summary is 60-120 words only with sufficient full text; short inputs yield shorter summaries. Store original-language excerpt and label translations. A headline-only input cannot yield full-article claims.
- [ ] **3. Implement deterministic priority and links.** Apply the spec's 72-hour rule to an applicable established effective-date claim, never to a proposal's hoped-for date. Distinguish media-only urgency from official authority. Match service/topic to curated links; direct supporting notice requires inspected content supporting the claim, not merely a matching domain or a newspaper anchor. Include last check/result labels.
- [ ] **4. Add assertions for A01/A02/A05-A08/A24.** Model invents a quote/URL; fixture has hostile instructions; two types of passport fee; foreign-country visa article; old date versus page banner; conflicting date; Bangla proposal verbs; backend failure. Invalid claims land in review with evidence retained.
- [ ] **5. Run checks, commit and stop.** No hardcoded live fee change and no data writes outside maintenance test roots.

## P07 — Event lifecycle and legacy import

**Create:** `maintenance/events.py`, `maintenance/import_legacy.py`, `tests/maintenance/test_p07_events.py`; modify store/models/migrations/fixtures; report.

**Interfaces:** `ingest_claim(store, observation: dict, claim: dict) -> dict`; `review_event(store, event_id: str, action: str, details: dict, *, now: str) -> dict`; `import_legacy(store, rows: list[dict], inventory: dict, *, import_key: str) -> dict` returns imported/quarantined/reused counts.

Event contracts are the spec's independent policy/evidence/processing statuses, priority/reason, stable UUID, material revision, effective claims, operator disposition and related observation IDs. Matching uses service+topic+scope+authority and compatible circular/decision identifiers. Missing identifiers must not force uncertain merges: separate events plus suggested association is acceptable. A correction can be a material revision without changing event ID.

- [ ] **1. Write persisted identity regressions.** Three compatible reports become one event with three observation links. Distinct old URLs changing to one new URL stay distinct resource-transition identities. Repeated missing counts update one open absence episode; reappearance closes it, next disappearance starts a new episode.

```python
def test_malformed_import_does_not_drop_valid_row(self):
    from maintenance.import_legacy import import_legacy
    from tests.maintenance.fixtures import legacy_event, inventory
    result = import_legacy(self.store, [{'status':'pending'}, legacy_event()],
                           inventory(), import_key='fixture-import-v1')
    self.assertEqual(result['quarantined'], 1)
    self.assertEqual(result['imported'], 1)
```

- [ ] **2. Implement transactions.** Validate import rows/inventory into a candidate list before one commit; quarantine uncertain rows with original JSON and reason. A repeated import key/content is idempotent; a key with different input hash fails. Do not invoke legacy mutation-based migration or infer approved patches from legacy approval flags.
- [ ] **3. Implement event actions/history.** Watch/snooze/irrelevant/merge/resolved never authorize patches. Record actor/reason/previous state. Merge preserves original IDs as aliases and observation attribution. New official effective evidence can resurface a snoozed event; identical repeats cannot. Resolve conflicting pending resource episodes deliberately, not by erasing queue rows.
- [ ] **4. Test A03/A04/A11-A13 and status transitions after reload.** Simulate crash between event and observation-link writes; no half-linked event. Syndicated copies do not increment independent corroboration.
- [ ] **5. Run checks, commit and stop.** R2-R5 semantics are replaced in new runtime; do not claim old JSON handlers repaired.

## P08 — Worker, source health and local outbox

**Create:** `maintenance/worker.py`, `maintenance/notifications.py`, `tests/maintenance/test_p08_worker.py`; modify store/migrations/fixtures; report.

**Interfaces:** `run_due_once(store, sources: list[dict], *, now: str, owner: str, observe, backend) -> dict`; `list_notifications(store, *, unread_only: bool) -> list[dict]`; `mark_notification_read(store, notification_id: str) -> None`. Worker injects clock/observer/backend and calls P05→P06→P07. No endless background thread in tests.

- [ ] **1. Write lease/outbox tests with two store connections.** Add atomic `claim_job(job_key, owner, now, lease_seconds) -> dict | None`; leases carry random fencing tokens. Renewal/finish requires the current token; a late expired owner cannot publish another worker's result. Use a fake clock and no real sleeps.

```python
job = a.claim_job('source:s1:2026-09-05T03:00:00Z', 'worker-a', now, 60)
self.assertIsNotNone(job)
self.assertIsNone(b.claim_job('source:s1:2026-09-05T03:00:00Z', 'worker-b', now, 60))
```

- [ ] **2. Implement bounded cadence.** Official daily09:00, news every6h, digest18:00 Asia/Dhaka. Use `zoneinfo`; if Windows tz data is missing report missing `tzdata`, do not silently use machine local time. One catch-up per due source after wake. Per-host concurrency one, minimum two seconds; honor Retry-After, two bounded transient retries, no repeated 403/challenge retries. Persist next due time instead of sleeping through long backoffs.
- [ ] **3. Persist health even without change.** Two consecutive failed due checks degraded; two missed intervals stale; record last attempt/success/complete/change independently. Retrying incomplete pagination never proves absence. Material-event outbox unique key `(event_id, material_revision)`; health notifications have their own transition key. Informational excluded from digest, Watch included, urgent emitted on next check. No unchanged-run notifications.
- [ ] **4. Test A09/A37 plus crash/restart and health recovery.** Failed source does not stop other sources; expired lease owner cannot finalize; sleep creates missed coverage, not successful no-change checks; first-run seven-day lookback uses article dates.
- [ ] **5. Run checks, commit and stop.** No Windows scheduled task or autostart registration.

## P09 — Maintenance inbox, CLI and live-source gate

**Create:** `maintenance/cli.py`, `maintenance_app.py`, `maintenance/ui/__init__.py`, `maintenance/ui/inbox.py`, `maintenance/ui/health.py`, `tests/maintenance/test_p09_interfaces.py`, `tests/integration/test_tracker_live_sources.py`, `docs/TRACKER_OPERATIONS.md`. Modify source registry/worker API adapters only as necessary for production wiring; report.

**Interfaces:** `main(argv: list[str] | None=None) -> int`; CLI `python -m maintenance.cli --root <explicit-root> init|check-once|inbox|health|digest`. `check-once --fixture <fixture-file>` injects replay data and is labelled replay in UI/logs. `check-once --live --source-id <id>` enables a bounded source check; no provider credentials printed. `streamlit run maintenance_app.py -- --root <explicit-root>` opens the separate operator app.

Add `validate-source --source-id <id>` for an explicit bounded acquisition of a disabled candidate, using its mandatory host policy and robots rules. This command records validation evidence but never silently enables the entry; a reviewed registry edit enables it after success. Normal check-once cannot contact disabled entries. `main` catches argument-parser SystemExit and returns its integer code for in-process tests; module execution passes that code to sys.exit.

- [ ] **1. Add entry-point tests before UI code.** Invoke `main([...])` with temp root and captured stdout; parse returned JSON. Replay proposal → Watch, excerpt, source link and suggested official links. Hash original corpus fixtures before/after. Test UI view-model builders from persisted data; do not mock away store or event service.

```python
def test_cli_requires_an_explicit_root(self):
    from maintenance.cli import main
    self.assertNotEqual(main(['inbox']), 0)
```

- [ ] **2. Implement Inbox and Source health.** Match all alert-card fields/actions in spec; filters service/priority/status; accessible links; summary/excerpt distinction; visible replay/access limitations; history and snooze actions go through P07. Evidence/proposal and update-history views display honest empty states until G2/G3. Do not expose nonfunctional approval/publish controls.
- [ ] **3. Validate live source endpoints through real adapters.** Inspect the four publisher sites for actual supported feeds/search paths and test a small selection using P02/P03 policy. Do not guess RSS URLs or add paid providers. Capture bounded smoke evidence in the maintenance root: URL, time, method, observed article title/date, resulting observation, coverage and failure. Enable only passing source entries with evidence references; at least two independent publishers and the official pilot paths are required. If access fails, ship replay-capable code with G1 live gate explicitly incomplete and return to Codex for source strategy.
- [ ] **4. Run offline/candidate checks and explicit live smoke.** Integration command `python -B -m unittest discover -s tests/integration -p 'test_tracker_live_sources.py' -v` requires an explicit `GSN_LIVE_TEST_ROOT` and enabled fixture registry path; absent configuration is reported as a skipped live gate, not phase success. Limit to selected source checks; never all govt domains. Manually inspect the maintenance UI for a proposal, urgent media-only lead, direct versus suggested link and failed source. Save screenshots only in the test root.
- [ ] **5. Commit/report and stop for G1 acceptance.** No scheduler activation. Include accessible/inaccessible publisher matrix and evidence URLs; do not claim comprehensive monitoring.

## G1 coverage gate

A01-A13, A24 and A37 are covered; A14/A15 are inherited from G0. Source health and blocked monitoring are distinct from unchanged. R2-R5 are replaced by tested event/import semantics; legacy diagnostics are retained during import. No news observation or UI action can call corpus/embedding code. Live gate failures prevent claiming a functioning live tracker even when fixture tests pass.
