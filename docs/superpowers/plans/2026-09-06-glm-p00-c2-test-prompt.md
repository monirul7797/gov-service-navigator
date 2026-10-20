# Ready-to-paste GLM prompt — P00-C2 test correction only

Copy the text below into the existing GLM implementation conversation.

---

You are GLM 5.3 Flash, the implementation worker. Codex reviewed corrective commit `b9e1fe972d85aa3f93aef028b5215ed844db1698`. The three code corrections are present, but one required regression has an unrelated-failure false positive. **Execute P00-C2 only. P01 remains unreleased.**

Continue on branch `codex/tracker-g0-p00` in:
`C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p00-1788625590422472000/worktree`

Expected HEAD: `b9e1fe972d85aa3f93aef028b5215ed844db1698`. Check HEAD/status first; preserve user changes and untracked plans/specs. Do not amend previous commits or modify the original checkout.

## Exact scope

Modify only:

- `tests/maintenance/test_p00_harness_failures.py`

Create:

- `docs/superpowers/reports/P00-C2-worker-report.md`

Do not change either harness tool, production modules, other tests, corpora or vector data. This is a test-fixture correction, not another architecture or feature task.

## Finding

`TestC2DirectResolverGuard.test_caught_violation_still_fails_parent_aggregation` creates a temporary repository containing only `test_freshness.py`. The other three required legacy scripts are missing, and the discovery directory has no test. Consequently the parent exits nonzero even without network-attempt enforcement.

Codex proved this with an external scratch copy of the runner: removing the legacy `and not attempts` decision and the final `and not total_attempts` decision still left this regression passing. Recording an attempt is not sufficient evidence that the attempt caused the failed run.

## Required fixture and assertions

1. Keep the copied production runner and the real parent subprocess path.
2. Supply all four legacy-named scripts in the temporary repository. Three can be simple successful fixture scripts. `test_freshness.py` is the one that conditionally attempts a guarded connection and catches the exception.
3. Supply package init files and one genuine passing unittest under `tests/maintenance`, selected by an explicit fixture-only pattern such as `test_aggregation_fixture.py`. Do not discover the real P00 suite recursively.
4. Use the same complete fixture for a positive control and the caught-violation case. A child-only environment flag can select whether the fixture script attempts the connection. The parent test process must never make a network call.
5. Positive control: parent exits0; all four legacy child exits0; discovery ran1 with failures0/errors0/skips0; attempted_network0; no missing-script or NO_TESTS_DISCOVERED diagnostic.
6. Caught violation: the offending child catches the guard exception and exits0; the other three children and the one discovered test also succeed; the parent exits nonzero and attempted_network1. Parse the harness JSON rows and assert these exact causal conditions, rather than relying only on a nonzero parent exit.
7. Each deliberate child has isolated logs so the normal P00 suite still reports attempted_network0.

The synthetic legacy scripts need not print fake 60/31/63 counts. They are local aggregation fixtures, not substitutes for the real legacy suites, which still run in cumulative verification.

## Prove that the regression detects enforcement removal

Demonstrate on a second, temporary copy of the runner only:

- Change the legacy result decision from `ok = (code == 0) and not attempts` to `ok = (code == 0)`.
- Change the final return decision from `return 0 if exit_ok and not total_attempts else 1` to `return 0 if exit_ok else 1`.
- Leave guard installation and attempt recording intact.

With the corrected complete fixture, that copied runner should incorrectly exit0 despite the caught attempt, and the enforcement regression must detect that. Assert that each exact substitution matches once; fail loudly if source changes make the mutation invalid. Never edit or weaken the real `tools/run_offline_checks.py` for this demonstration. A committed mutation regression is acceptable if isolated and clearly explained; otherwise preserve the diagnostic run/output outside tracked source and report it.

## Verification and commit

Run the targeted regression with its positive control and caught-violation case. Then run:

```powershell
python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'
git diff --check
```

Make a new local commit with only the allowed paths:

```text
test(tracker): P00-C2 isolate caught-network enforcement regression
```

Then run:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p00_*.py'
```

Report exact base/final SHAs, file scope, positive-control result, caught-violation result, mutation result and cumulative/exported results. Distinguish top-level test skips from a recursion-prevention skip inside a nested child. Preserve prior reports. Do not push, merge, change the original checkout, or proceed to P01.

End with **“P00-C2 complete for review; stopped. P01 has not been started.”** If incomplete, report that instead.
