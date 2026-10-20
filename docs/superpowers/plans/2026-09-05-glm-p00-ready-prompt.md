# Ready-to-paste GLM 5.3 Flash prompt — P00 only

Copy the text below into GLM 5.3 Flash in the Govt. Service Navigator project. This dispatch releases P00 only. Do not paste the whole roadmap as an instruction to implement every phase.

---

You are GLM 5.3 Flash, the implementation worker for Govt. Service Navigator. Codex is the top-level orchestrator, architect, planner, critic, reviewer and phase owner. Architectural decisions are already specified. Implement only the bounded packet below, run tests, commit locally, report results and STOP for Codex review.

**Released packet: P00 — Reproducible baseline and offline test harness.**

Project:
`C:/Users/Monirul/Documents/Structured Data/NID/gov-service-navigator/gov-service-navigator`

Read these documents before changing anything:

1. `docs/superpowers/plans/2026-09-05-glm-implementation-control.md`
2. `docs/superpowers/plans/2026-09-05-glm-g0-baseline-and-sensors.md` — execute P00 only; P01-P03 are context, not released work.
3. `docs/superpowers/specs/2026-09-05-freshness-reconciliation.md`
4. `docs/superpowers/specs/2026-09-05-tracker-evidence-update-design.md`

If an execution skill is available, use `superpowers:executing-plans` for this packet only. Do not spawn workers/subagents or change models. Do not start broader planning or architecture changes.

## What you must accomplish

Create a reproducible starting commit that preserves the three existing acquisition edits and proves the existing tests run without attempting network access. Add focused tests for the final_url/provenance contract. Build the offline and exported-commit verification tools exactly as described in P00 and the control guide.

Expected audited HEAD is `8d3c83cfe35ac4f5ba7667856b8562c300aeeaee`. Expected tracked local edits are:

- `acquisition/acquire.py`
- `acquisition/crawler.py`
- `acquisition/transport.py`

Together they were 63 inserted and 5 removed lines at audit time. Inspect the actual diff. If the starting state differs materially, report the mismatch to Codex before integrating it; do not reset, overwrite or assume another person's changes are yours.

Use P00's isolated-worktree procedure and branch `codex/tracker-g0-p00`. Carry the existing three-file patch into that worktree without altering the original checkout. Copy only the plans/specs for reading if they are still untracked; do not stage them or the review bundles as part of the implementation commit.

The existing project Python used by the audit was:
`C:/Users/Monirul/AppData/Local/Programs/Python/Python312/python.exe`

Confirm the interpreter/dependencies. Do not silently switch environments, download models or install browsers/packages to make results appear successful.

## Your exact file boundary

Carry/reconcile the existing changes in the three acquisition files above. Do not add new production behavior beyond those existing edits in P00.

You may change fixture isolation and contract tests in:

- `test_freshness.py`
- `test_acquisition.py`
- `test_freshness_v21.py`

Create:

- `tools/run_offline_checks.py`
- `tools/verify_candidate.py`
- `tests/__init__.py`
- `tests/maintenance/__init__.py`
- `tests/maintenance/fixtures.py`
- `tests/maintenance/test_p00_baseline.py`
- `docs/superpowers/reports/P00-worker-report.md`

Run `test_multiturn.py` unchanged. If a source change outside this boundary is necessary, stop and explain the dependency to Codex. Do not opportunistically repair the other audit findings.

## Important testing requirements

The audit found that the freshness/acquisition tests attempt real robots.txt access even though their headers claim to be hermetic. Fix their fixture injection; do not change production robots semantics, globally force robots to allow, remove assertions, or convert every network failure into an accepted test outcome.

The offline guard must record attempted connections/DNS/HTTP calls and fail the run even if production catches the raised exception. Its exception must not be an OSError. Test the guard itself using the P00 deliberate network-probe subprocess; the probe must fail before contact.

Tests must cover real `RequestsTransport.fetch` result production and a real `KnownSourceMonitor.run` consumer in temporary fixture directories. Do not rely only on dataclass construction. Fake session/renderer/robots values are injected at their external boundaries.

Run:

```powershell
python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'
git diff --check
```

Record exact commands and outcomes. The old audit counts were 60 freshness, 31 V2.1, 63 acquisition and 40 multiturn checks, but those counts are context, not a pass threshold. Explain changed counts. A script printing false predicates and returning zero is not a successful regression test.

Stage only explicit P00 file paths, create a local commit:

```text
test(tracker): P00 reconcile acquisition baseline
```

Then verify the exact committed code:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p00_*.py'
```

The candidate verifier must export HEAD to a fresh temporary directory, run that export's checks with the same interpreter and exclude untracked working files. If you need a correction, create a new corrective local commit, rerun the exported check and report the final SHA. Do not push, merge, amend predecessor history or proceed to P01.

## Explicit exclusions

No tracker database, alert inbox, news fetches, govt browsing, scheduling, OCR, corpus edits, vector writes, embedding, Visa activation or production cutover. Do not run legacy freshness/reembedding commands against live project state. No `git add .`, blanket cleanup, resets, restoring user files or changing global Git configuration.

The existing final_url edits do not prove that redirect destinations are blocked before contact. Do not claim P00 fixes R6/R8 or accepts Freshness V2.1. Those are separately scoped review gates.

## Final report and stopping rule

Return:

1. Packet ID, base commit, branch/worktree and final local commit SHA(s).
2. Files changed and what behavior each change verifies.
3. Which pre-existing edits were carried unchanged or reconciled, with reasons.
4. Exact failing regression command/result before the fix; distinguish already-passing regression coverage.
5. Targeted and cumulative check counts, failures, skips and attempted-network count.
6. Exported-commit verification command/result and interpreter.
7. Evidence that the original checkout and protected corpus/vector files were not changed by your work.
8. Remaining audit defects, blockers or deviations. Do not conceal incomplete checks.
9. The sentence: **“P00 complete for review; stopped. P01 has not been started.”** If P00 is incomplete, say that instead.

Then stop. Codex, not you, decides acceptance and releases the next packet.
