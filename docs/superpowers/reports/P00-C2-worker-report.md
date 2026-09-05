# P00-C2 worker report — isolate caught-network enforcement regression

Packet: P00-C2 (correction to P00-C1's aggregation regression)
Base commit: `b9e1fe972d85aa3f93aef028b5215ed844db1698` on `codex/tracker-g0-p00`, same worktree.
Commits: `8f7db8f65bf2a2bc345522016e3e677c580df436` (`test(tracker): P00-C2 isolate caught-network enforcement regression`) plus the report commit; final SHA reported in the dispatch response.

## Scope

Only `tests/maintenance/test_p00_harness_failures.py` changed (allowed). The prior
`test_caught_violation_still_fails_parent_aggregation` was replaced by a
complete-fixture trio: `test_positive_control_all_children_pass`,
`test_caught_violation_still_fails_parent_aggregation` (rewritten),
`test_mutation_detection_enforcement_removal`. No harness tool, production
module, other test, corpus or vector file changed.

## Fixture (shared by positive control, caught violation, mutation)

Temporary repository containing the copied production runner, ALL FOUR
legacy-named scripts (three simple exit-0 fixtures; `test_freshness.py`
conditionally attempts a guarded connection and catches any exception,
selected by a child-only `P00C2_ATTEMPT` env flag), package init files, and
one genuine passing unittest (`tests/maintenance/test_aggregation_fixture.py`)
selected by the fixture-only pattern `test_aggregation_fixture.py`. The real
P00 suite is never discovered recursively. The parent test process makes no
network call; attempts happen only inside guarded grandchildren.

## Results (Python 3.12.10, audited interpreter)

- Positive control: parent exit 0; all four legacy child exits 0; discovery
  ran 1 with failures 0 / errors 0 / skips 0; attempted_network 0; no
  missing-script or NO_TESTS_DISCOVERED diagnostic.
- Caught violation: offending `test_freshness.py` child catches the guard
  exception and exits 0 with `network_attempts=1` on its row; the other three
  children and the discovered test all succeed (exit 0, 0 attempts); parent
  exits nonzero; harness TOTALS show attempted_network=1. Assertions parse the
  harness JSON rows and check these exact causal conditions.
- Mutation demonstration (temp copy of the runner only): both exact decision
  substitutions (`ok = (code == 0) and not attempts` → `ok = (code == 0)`;
  `return 0 if exit_ok and not total_attempts else 1` → `return 0 if exit_ok else 1`)
  matched exactly once each (loud failure if the anchors drift); guard
  installation and attempt recording left intact. Under the mutation the same
  fixture returns exit 0 with attempted_network=1 — the false success the
  committed regression detects. The real `tools/run_offline_checks.py` is
  asserted byte-identical after the demonstration.

## Verification

- Targeted: `python -B -m unittest tests.maintenance.test_p00_harness_failures.TestC2DirectResolverGuard -v` → 6 tests OK.
- Cumulative: `python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'` → exit 0; legacy [60, 31, 63] + multiturn pass; discovery ran 24, failures 0, errors 0, skips 0; attempted_network 0. The recursion-prevention skip appears only inside the nested normal-pattern grandchild, not at the top level.
- `git diff --check` → exit 0.
- Exported: `python -B tools/verify_candidate.py --legacy --pattern 'test_p00_*.py'` → exit 0 on the final commit (reported in the dispatch response).

## Limitations

- The mutation demonstration proves detection of exactly the two documented
  decision removals; other hypothetical mutations are out of scope.
- The synthetic legacy fixtures print no 60/31/63 counts — they are local
  aggregation fixtures; the real legacy suites still run in cumulative and
  exported verification.
- Skips: zero at every top level; the only skip anywhere is the documented
  recursion-prevention skip inside the nested normal-pattern grandchild.

STOPPED; next packet not started.
