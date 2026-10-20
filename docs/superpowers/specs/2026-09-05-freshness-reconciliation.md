# Freshness audit reconciliation

Date: 5 September 2026

## Decision

Freshness V2.1 is not accepted for unattended production use. The nine bugs listed in the supplied final audit remain present. Preserve and repair the acquisition and comparison components needed by a tracker; replace the legacy multi-file approval and publication workflow with the staged workflow in the companion design. Do not connect the new tracker to the current re-embedding CLI.

This document records a read-only implementation reconciliation, not a repair. Only documentation was added to the project. Existing source edits, corpora, monitoring state, embeddings, Git configuration, index, and commit history were not intentionally changed. All behavioral probes ran against copies outside the project.

## Inputs and repository identity

- Supplied handoff: Govt_Service_Navigator_Ultra_Handoff_2026-09-05.docx, particularly sections 8, 10, 13 and Appendix A.
- Supplied audit: freshness_v21_final_review/FRESHNESS_V21_FINAL_REVIEW.md.
- Audit probes: freshness_v21_final_review/validation/negative_probes.py.
- Observed branch: master; HEAD: 8d3c83cfe35ac4f5ba7667856b8562c300aeeaee.
- Pre-existing source modifications: acquisition/acquire.py, acquisition/crawler.py, acquisition/transport.py; 63 added and 5 removed lines collectively.
- Corpus counts independently read: NID 102, e-Passport 66, e-Return 83, BDRIS 42, Visa 39.
- Visa is absent from answer.py's live collection allowlist and freshness/metadata.py's supported embedding services; no embed_visa.py exists. Corpus readiness does not authorize Visa routing or embedding.

The handoff's model-role assignments are historical context, not instructions to dispatch another model. Requirements explicitly agreed with Monirul in this conversation govern the new design.

## Verification method and limits

1. Read the audit and inspected the current production call paths, including CLI construction, persisted discovery queues, source trust checks, and Streamlit collection caching.
2. Recorded SHA-256 hashes of all 364 existing tracked files before probes. Final comparison found all 364 byte-for-byte unchanged, including the three pre-existing source edits. HEAD remained unchanged. New documentation was checked separately; final evidence is in final_verification.json in the audit artifact directory below.
3. Copied the relevant tracked Python modules, corpus files and concept registry into separate working-tree and HEAD directories. HEAD files were obtained with Git show, not copied from the modified checkout. The original repository was never checked out, reset, cleaned or staged.
4. Ran the supplied negative probes against the working-tree copies. Added focused probes calling the real run_discovery orchestrator with injected crawl results and persisted temporary inventories/queues. These establish the two event-identity failures beyond helper-only examples.
5. Used Python 3.12.10 from the project's existing installed environment, with UTF-8 output and bytecode writes disabled. The bundled document runtime lacks requests, so its import failures were environment failures and are not counted as product test results.
6. A strict socket guard raised RuntimeError before network connections. The three freshness/acquisition suites attempted robots.txt network calls and stopped under that guard. They were then rerun with network attempts converted to OSError, exercising the existing robots-unreachable fallback. No government website, model API, Chromium instance or real Chroma collection was contacted during these checks.
7. Scratch directories inherited permissions beneath the task's permitted artifact directory. The harness replaced temporary-directory creation only to avoid a Windows sandbox permission error; production source was unchanged.

Test results are therefore qualified offline results, not evidence that these suites are inherently hermetic or that a production deployment is accepted. No broad live crawl or real embedding test was performed. The six design concerns were reconciled by code inspection, not exhaustive crash-injection or migration testing.

### Results

| Check | Current result | Interpretation |
|---|---|---|
| Working copy test_freshness.py | 60 checks pass with simulated network failure | Existing assertions remain green despite the negative-probe failures |
| Working copy test_freshness_v21.py | 31 checks pass with simulated network failure | Does not establish correct production wiring |
| Working copy test_acquisition.py | 63 checks pass with simulated network failure | Does not cover all monitor trust paths |
| Working copy test_multiturn.py | 40 checks pass under strict network guard | Existing deterministic conversation behavior preserved in this test |
| HEAD copy test_freshness_v21.py | Crashes before first completed check | RenderResult lacks final_url required by KnownSourceMonitor |
| Supplied negative probes | All eight targeted correctness predicates remain false; dry-run store-byte preservation true | Script exit code 0 means it printed results, not that correctness checks passed |
| Added production discovery probes | Distinct old URLs receive the same link-change ID; repeated absence increases queue from 1 to 2 | Confirms defects through persisted orchestrator calls |

The original audit's passing-suite counts were reproduced only under the offline-failure harness described above. This reconciliation additionally identifies implicit robots network dependence in tests advertised as hermetic.

## Nine bug findings

Identifiers R1-R9 below map in order to the original audit's nine remaining bugs, rather than to its B1-B12 categories.

| ID | Finding and current evidence | Disposition before the affected path is enabled |
|---|---|---|
| R1 | HEAD transport dataclasses lack final_url; current monitor reads it. The isolated HEAD test raises AttributeError at freshness/known_monitor.py:121. Existing local transport additions supply the missing field. | Review and reconcile all three local acquisition edits into a reproducible repair commit; never blindly overwrite or attribute them to HEAD. Test an exported candidate commit. |
| R2 | freshness/run_freshness_check.py:153 passes an empty url plus new_url for a source_link_changed event; old_url is omitted. Two real orchestrator runs with different old URLs yielded disc_9554c3bcb856f773. | Canonical identity includes service, seed, event type, old URL, new URL and relevant observation transition. |
| R3 | Discovery events contain old_url/new_url, while freshness/discovery.py:323 resolves only event.url. apply_review_action raises an inventory-resource-not-found error. It also mutates the in-memory event status before validation. | Validate event shape and resolve its resource before mutations. The new event inbox replaces this handler; if retained as an interim CLI, repair it before use. |
| R4 | Consecutive-missing count is included in event identity at freshness/run_freshness_check.py:155. Two persisted runs at counts 3 and 4 created two unresolved removal events. | One lifecycle event per absence episode; update count/last_seen without a new alert. Reappearance closes the episode; later absence can open a new episode. |
| R5 | Queue initialization directly accesses q['event_id'] at freshness/run_freshness_check.py:86. A legacy pending row without it raises KeyError before discovery. | Versioned import validates every legacy row. Derive an ID only when identity is unambiguous; quarantine malformed rows with reasons. Never let one row stop the run. |
| R6 | freshness/known_monitor.py:233 skips reading the baseline text during dry run. A 1,000-character baseline followed by 100 characters is not quarantined as collapse in the dry run. Store bytes remain unchanged. | Dry and real comparison consume identical snapshots and inputs; only persistence differs. Test equal proposed classifications plus zero persistent writes. |
| R7 | freshness/reembed.py:166 indexes task['task_id'] inside its exception handler. A missing task ID escapes isolation and prevents later work. The dry-run projection also directly indexes required fields. | Legacy execution remains unavailable to the new system. Validate job shape before dispatch; errors must use safe diagnostic identifiers and not stop independent jobs. |
| R8 | KnownSourceMonitor accepts fetched PDF bytes before a final-host check; the later guard includes only browser/static methods at freshness/known_monitor.py:240. Fake external PDF becomes a trusted baseline. | Enforce provenance and approved destination policy for every content type and redirect hop. Validate document bytes/type; unapproved destinations cannot become trusted evidence. |
| R9 | freshness/reembed.py:183 constructs ReembedService() with no source_store_path, disabling its optional guard. The guarded lookup also uses an unnormalized URL and skips checking absent entries. | New approvals bind exact evidence and corpus revision hashes. Require checks in real entry points; missing/inconsistent state blocks publication. Repair the legacy CLI only if it must remain operational before replacement. |

## Six design concerns

| Concern | Code reconciliation | Design decision |
|---|---|---|
| Cross-file consistency | Source state, event state and tasks are written separately; atomic replacement of each file is not a transaction spanning them. | SQLite transactions for operational state; immutable staged corpus/vector releases and one active-release pointer for publication. |
| Lost diagnostics | reconcile_seed_inventory returns unverified_diagnostics, but run_discovery does not persist that map; failed seeds are counted and skipped. | Persist a compact per-source outcome and last-success time even when no content-change event is emitted. |
| Stale lifecycle events | Individual review changes do not reconcile other pending events for the same resource. | Event episodes and explicit supersession/merge history; acknowledged evidence does not suppress all future changes to that source. |
| Migration guarantees | migrate_v1.py validates source conflicts before writes, but asserts total resulting-store counts after writes. backup_v1_state uses global V1 inputs. | Freeze legacy mutation-based migration. New importer validates the complete candidate transaction and preserves uncertain history as imported/unverified. No source or target overwrite to repair ambiguous state. |
| Evidence retention | Candidate filenames use 12 hex characters of a content hash; no reference-aware cleanup is present. | Full content hashes, no overwrite on mismatch, immutable originals and referenced revisions; cleanup is separate, previewable and never part of ingestion. |
| Acceptance coverage | Tests inject a source-store guard omitted by the CLI and miss malformed IDs/known-PDF branches. Strict network blocking exposes robots network attempts. | Public-entry-point, reload, crash/recovery, stale-approval and network-prohibition checks, using the exact candidate commit. |

## Retain, repair, simplify and replace

| Component | Decision | Boundary |
|---|---|---|
| Existing four-service chatbot, deterministic router, corpus schemas, BGE-M3 | Retain | No routing-threshold changes or automatic Visa enablement in this work |
| Acquisition transport, URL policy, provenance, extraction utilities, safe-click rules | Retain and repair | R1/R8 plus accurate transport error classification; explicit policies remain testable |
| Known-source mapping and content comparison | Retain and adapt | R6 repaired; monitoring baseline is not proof that the corpus reflects that version |
| Broad crawler | Simplify its role | Curated notice hubs and focused assisted acquisition; bounded crawl never means comprehensive absence evidence |
| JSON discovery queues and resource review | Replace in the new runtime | Import legacy observations once into a versioned event store; do not dual-write two competing inboxes |
| Existing manual-edit approval/reembed pipeline | Replace for managed updates | Keep useful metadata/embedding functions; do not make the old CLI an implicit alternate publisher |
| Snapshot and corpus history | Extend | Original evidence, extracted blocks, full record revisions and effective dates stored separately |
| Streamlit and CLI collection resolution | Adapt only at publication phase | Current app caches collection handles and answer.py loads literal service names; every supported reader must resolve a pinned active release |
| Visa Phase 4 | Separate gate | Preparation and monitoring can continue; live routing/embedding require separate service acceptance |

## Minimum safe adoption sequence

1. Reconcile local acquisition edits and demonstrate reproducible imports/tests from the candidate commit. Preserve the current three edits until explicitly integrated.
2. Repair the capture trust boundary and dry-run fidelity. Add real production-path tests for all retained sensor paths. Do not silently reinterpret HTTP/TLS failures as policy changes.
3. Introduce the tracker store, sensor adapter and inbox. Implement R2-R5 lifecycle/import semantics there, without exposing the legacy discovery review as a second active inbox.
4. Keep the legacy approval/reembed executor outside the new workflow. Implement R7/R9 and cross-file consistency requirements in the new approved-patch publisher before enabling any managed corpus writes.
5. Preserve existing chatbot behavior until release-aware reading and staged publication pass the pilot acceptance checks.

This avoids repairing every legacy subsystem merely to replace it later, while prohibiting use of any known-broken path as a shortcut. A repair-only interim release would instead have to close all nine bugs and its relevant consistency concerns before production acceptance.

## Evidence artifacts

Probe output, input copies, harnesses and initial tracked-file hashes are retained outside the project under:

C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/gsn_audit_cba85f40629d4c78a042795794d6040f/

Useful artifacts are results_project_python.json, results_offline.json, production_probe_results.json, head_test_freshness_v21_project_python.log, working_negative_probes_project_python.log, and the three *_offline.log files. Earlier bundled-runtime import logs remain distinguishable from the project-runtime results. These artifacts contain copied public corpus fixtures, not production vector data or credentials.

The companion specification is 2026-09-05-tracker-evidence-update-design.md. No new system has been implemented or scheduled by this reconciliation.
