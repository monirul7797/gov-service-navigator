# Tracker Implementation Plan and GLM Control Guide

> **For agentic workers:** Use `superpowers:executing-plans` if available, executing only the packet released by the orchestrator. Do not dispatch subagents or continue into another packet. The user's GLM worker/reviewer arrangement overrides the skill's default execution-choice workflow.

**Goal:** Deliver the tracker, evidence preparation and approved e-Passport publication as independently reviewed increments.

**Architecture:** Retain repaired acquisition/comparison primitives. Use a separate SQLite maintenance application and immutable evidence/release artifacts. Publish through one active-release pointer; preserve deterministic chatbot routing.

**Tech stack:** Existing Python 3.12 environment, stdlib unittest/sqlite3, requests, Playwright, Streamlit, existing LLM backend, BGE-M3 and Chroma; document adapters as specified in G2.

**Spec:** [Tracker and evidence-update specification](../specs/2026-09-05-tracker-evidence-update-design.md); [audit reconciliation](../specs/2026-09-05-freshness-reconciliation.md).

## Ownership and authorization

Codex owns architecture, contracts, sequencing, design changes, review and phase acceptance. GLM 5.3 Flash implements one released packet, runs its tests, commits locally, reports and stops. Monirul transports prompts/results between them. These documents are plans, not a request to start implementation in this conversation.

Only P00 is included in the initial ready-to-paste handoff. Later packets are executable contracts, but become dispatchable only after Codex reviews their predecessors. Review may revise a later packet to match accepted interfaces. GLM must not interpret the full roadmap as permission to execute everything.

## Global constraints

- Keep the existing four-service chatbot usable during development. Keep deterministic routing and existing retrieval thresholds unchanged unless a separate change is benchmarked and approved.
- Offer one exact change-set approval followed by automatic preparation, publication and verification.
- Preserve Bangla source wording and distinguish generated translations.
- Keep Visa monitoring/evidence preparation separate from live Visa publishing, routing and embedding.
- News observations are leads; they cannot authorize a corpus patch. Proposal reports stay Watch.
- Use BAAI/bge-m3 and existing Chroma distance/normalization settings. No model/provider migration.
- Use a local SQLite maintenance database, immutable evidence files, a separate Streamlit operator app and a CLI using the same application services.
- Never write live corpora/vector data in an implementation test. All maintenance tests take an explicit temporary root; no default production-path constructors in tests.
- No scheduling activation, external messaging, cloud deployment, production cutover, push, merge or destructive cleanup in a worker packet.
- Do not run the legacy `freshness/reembed.py` executor on the working project. New code cannot use it as an alternate publisher.
- Existing review bundles and local edits are user work. Do not stage them with `git add .`, overwrite, reset or delete them.
- No hidden API calls, model downloads or browser installation during deterministic tests. Missing dependencies are reported, not silently substituted.
- Decisions needing a new dependency/provider, different storage architecture, schema meaning, routing behavior or wider file scope return to Codex. GLM may choose local variable names and small private helper organization.

## Packet map

| Packet | Deliverable | Depends on | Acceptance owner |
|---|---|---|---|
| P00 | Reproducible acquisition baseline; genuinely offline tests | Current audited checkout | Codex |
| P01 | Dry-run fidelity and known-PDF trust regression fixes | P00 | Codex |
| P02 | HTTP destination validation before each redirect | P01 | Codex |
| P03 | Browser navigation policy and unattended robots outcome | P02 | Codex; G0 gate |
| P04 | Maintenance contracts, SQLite store and artifact primitive | G0 | Codex |
| P05 | Curated registry and official/news observation adapters | P04 | Codex |
| P06 | Grounded interpretation, priorities and verification links | P05 | Codex |
| P07 | Event lifecycle, deduplication and legacy import | P06 | Codex |
| P08 | Due jobs, leases, health and local outbox | P07 | Codex |
| P09 | Alert UI/CLI and bounded live-source acceptance | P08 | Codex; G1 gate |
| P10 | Evidence intake, revisions and provenance | G1 | Codex |
| P11 | Digital PDF, DOCX and XLSX extraction | P10 | Codex |
| P12 | Screenshot/scanned-page OCR and legacy conversion | P11 | Codex |
| P13 | e-Passport comparison and exact patch construction | P12 | Codex |
| P14 | Evidence/proposal review UI and representative pilot | P13 | Codex; G2 gate |
| P15 | Exact approvals, dependencies and validity checking | G2 | Codex |
| P16 | Immutable release staging and embedding reuse | P15 | Codex |
| P17 | Candidate verification, atomic activation and recovery | P16 | Codex |
| P18 | Release resolver and chatbot reader pinning | P17 | Codex |
| P19 | Auxiliary reader integration and direct-writer guards | P18 | Codex |
| P20 | Effective-date activation, rollback and complete pilot | P19 | Codex; G3 gate |

Detailed packets: [G0](2026-09-05-glm-g0-baseline-and-sensors.md), [G1](2026-09-05-glm-g1-alert-tracker.md), [G2](2026-09-05-glm-g2-evidence-and-comparison.md), [G3](2026-09-05-glm-g3-approved-publication.md).

P02 is dispatched separately as P02A (policy and HTTP transport) / P02B (caller integration), following Codex's 2026-09-06 P01 acceptance review. P19 is dispatched as P19A/P19B/P19C and P20 as P20A/P20B, giving **25 planned worker handoffs** in total, excluding corrective packets. Each subpacket has the same mandatory review stop as a numbered packet. P02 is accepted only after both parts pass review; P03 still depends on the complete P02. For reports/tests use lowercase suffixes in filenames, for example `test_p02a_http_policy.py`, and uppercase packet labels in reports. The [initial ready-to-paste prompt](2026-09-05-glm-p00-ready-prompt.md) dispatches P00 only. P02A is accepted at `ac2930a477078f500010ab31b05ed50875339fce`; the current release is [P02B](2026-09-06-glm-p02b-ready-prompt.md). P03 remains held.

G4 service expansion and G5 assisted acquisition/Visa are deliberately outside these executable plans, as required by the specification. Their contracts will be designed after the e-Passport pilot. P13 can report unsupported preparation for other service schemas; it must never mislabel that as complete five-service comparison.

## Execution discipline for every packet

1. Read this guide, the specification and only the released packet's detailed plan. Confirm predecessor commit(s) and check current status. Do not infer acceptance from a worker's own passing tests.
2. Work on a `codex/` branch in an isolated worktree. P00 has an explicit procedure for carrying the three existing acquisition edits into that worktree. Later packets start from the accepted predecessor commit; never from an unreviewed successor.
3. Run the baseline checks. Write a failing behavioral test for each new behavior, demonstrate the intended failure, implement the smallest change, then rerun targeted checks. A test that already passes can be retained as a regression; do not fabricate a red result.
4. Use the precise file allowlist in the packet, plus its test fixtures and packet report. If a needed source file is outside it, report the reason to Codex rather than quietly widening scope.
5. Run cumulative deterministic tests, inspect diff and protect corpus/vector paths. Commit only explicit changed paths. Local commit message format: `fix(tracker): P01 ...` or `feat(tracker): P04 ...`; P00 uses `test(tracker): P00 reconcile acquisition baseline`.
6. Export that exact commit to a fresh directory outside the project and run the same deterministic command there. A working-tree pass alone is insufficient. If a correction is required, make a new local corrective commit, re-export it and report the final SHA. Do not amend user/predecessor history.
7. Return the report below and STOP. Passing tests do not release the next packet, accept a phase, or authorize production use.

Keep packets single-purpose. If GLM cannot complete the released packet reliably, return partial work and a precise blocker; do not consume the next packet to hide an unfinished dependency. Codex can split a packet further after inspection.

## Fixed test interface, introduced by P00

Create `tools/run_offline_checks.py`. Run from a worktree or exported commit:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'
python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'
```

The first command is the P00 check; the second is the cumulative command from P01 onward. The runner uses stdlib unittest discovery in `tests/maintenance`, and runs `test_freshness.py`, `test_freshness_v21.py`, `test_acquisition.py`, `test_multiturn.py` as isolated script executions when `--legacy` is present. Print executed counts, failures and skips; nonzero exit for failures or attempted network. No test is accepted merely because a probe script printed false values and exited zero.

Use `tests/maintenance/test_pNN_description.py` for each packet. Test functions shown in plans belong in a `unittest.TestCase` method; fixtures created in an earlier packet are importable from `tests.maintenance.fixtures`. Every new fixture API must be implemented alongside its first use. The shared fixture module contains data builders/fakes, never production bypasses.

The guard records attempts and raises a dedicated exception that is not an OSError. Check the recorded attempt count even if production catches the exception. Block socket connections, DNS lookup and high-level HTTP entry points; use injected session/renderer/robots/model fakes. Real local-browser and isolated Chroma tests have separate explicit integration commands and never replace the offline suite.

Test examples are minimum regression seeds, not the entire required suite. From P04 onward, tests using `self.store` inherit this shared fixture base, introduced in `tests/maintenance/fixtures.py`:

```python
import tempfile
import unittest
from pathlib import Path

class MaintenanceCase(unittest.TestCase):
    def setUp(self):
        from maintenance.store import MaintenanceStore
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.store = MaintenanceStore(self.root)
        self.store.initialize()
        self.addCleanup(self.store.close)
```

P04 defines `MaintenanceStore.close() -> None`; services close connections deterministically so Windows temporary directories can be cleaned. Fixture snippets using `Path`, `tempfile`, `FIXTURES` or earlier fake builders must import them explicitly; `FIXTURES` is `Path(__file__).resolve().parents[1] / 'fixtures' / 'evidence'` in a file under tests/maintenance. Do not copy a test fragment as a module-level script and claim unittest discovered it.

## Exported-commit check

P00 introduces `tools/verify_candidate.py --pattern 'test_p*.py' --legacy`. It must:

```python
# Algorithm contract; implementation belongs to P00.
# 1. Resolve repository root from this script, resolve HEAD, record git status.
# 2. Create a fresh TemporaryDirectory outside the repository.
# 3. git archive --format=tar --output=<scratch>/candidate.tar HEAD
# 4. Inspect archive member paths; reject escapes, links and nonregular members
#    other than directories, then extract under <scratch>/candidate.
# 5. Invoke that candidate's run_offline_checks.py using sys.executable.
# 6. Return its exit code; print HEAD, interpreter, arguments and scratch result.
# 7. Never copy untracked files or install dependencies to make an export pass.
```

Evidence logs go outside the repository or to the worker's response. A short committed `docs/superpowers/reports/PXX-worker-report.md` contains scope, baseline SHA, changes, tests and known limitations. Report the final candidate SHA in the response after committing; do not create a self-referential commit-hash edit loop.

## Shared implementation contracts

Exact public APIs are named in the packet that introduces them. JSON payloads use plain dictionaries validated at module boundaries; no second validation framework is required. UUIDs identify entities; full SHA-256 identifies immutable content. UTC timestamps use ISO 8601 with timezone; Asia/Dhaka rules apply to scheduling and date-only commencement. Money is a decimal string with explicit currency/VAT basis, never binary floats.

`canonical_hash(value: dict) -> str` hashes UTF-8 canonical JSON (`sort_keys=True`, `separators=(',', ':')`, `ensure_ascii=False`, `allow_nan=False`). All hash-input arrays have domain-defined order: record IDs/dependencies are sorted; source document blocks retain their source order. Do not silently reorder meaningful arrays.

`MaintenanceStore(root: Path)` anchors state to the explicit root: `maintenance.sqlite3`, `artifacts/`, `releases/`. It never locates the live corpus itself. Production CLI receives `--root`; the UI uses one explicit configured maintenance root. Importing a module creates no directories, database, model or browser.

## Review report required from GLM

```text
Packet:
Base commit and branch/worktree:
Final local commit SHA(s):
Files changed:
Behavior implemented:
Regression: exact failing command/result before fix:
Targeted checks: commands/counts/failures/skips:
Cumulative offline checks:
Exported-commit checks and interpreter:
Integration checks, if this packet requires them:
Existing user edits carried forward:
Protected corpus/vector paths: unchanged evidence:
Known limitations and unmet acceptance items:
Architectural/scope deviations: none, or exact proposed deviation:
STOPPED; next packet not started.
```

Codex reviews actual diff and production callers, reproduces targeted negative cases, checks scope and accepts or requests a corrective packet. A01-A40 traceability is in the phase plans. A phase cannot be accepted while its required live/real-library gate is skipped.

## Acceptance traceability

| Specification scenarios | Responsible packets |
|---|---|
| A01-A02, A05-A08 | P06 and production wiring P09 |
| A03-A04, A11-A13 | P07, persisted integration P08-P09 |
| A09 | P03/P05/P08/P09 |
| A10 | P05/P13, baseline provenance P16 |
| A14-A15 | P01-P03 |
| A16 | P11/P12, representative evidence P14 |
| A17 | P12/P13/P14 |
| A18 | P10 |
| A19-A22 | P13 |
| A23 | P13/P15/P20A |
| A24 | P06/P10/P11/P12 |
| A25-A27 | P13/P15/P17 |
| A28-A29 | P16 and real Chroma pilot P20B |
| A30-A31 | P16/P17/P20B |
| A32 | P18/P19A/P19B |
| A33 | P20A |
| A34 | P20B |
| A35 | P13/P15/P17 reject unsupported live Visa publication |
| A36 | P00 continuous regression; P18-P20 real reader/pilot checks |
| A37 | P08 and publication fencing P16-P17 |
| A38 | P12/P14 |
| A39 | P15/P17/P20A |
| A40 | P18 consistency and P13/P17 rejection of unapproved routing; positive new-concept publication deferred to a separate routing-change plan |
