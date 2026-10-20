# P00-C1 Codex review

**Decision: one test correction required before P00 acceptance. P01 remains unreleased.**

Reviewed commit: `b9e1fe972d85aa3f93aef028b5215ed844db1698`, parent `94072022d0dc64507a261a6f8a91c6ec915b3eec`.

## Code corrections

The prior three implementation findings are addressed in the inspected diff:

- Blank patterns are rejected and zero-discovery results cannot pass the parent readiness check.
- The three direct DNS resolver functions are intercepted and log before raising the non-OSError guard exception.
- Windows drive/ADS/rooted paths and links/nonregular members are rejected, with resolved destination containment and all-member validation before extraction.

The normal-pattern recursion skip is confined by the `P00_NORMAL_PATTERN_CHILD` test flag. It is not a skipped production behavior. Nested-child and top-level counts must still be reported separately.

## Remaining finding: caught-violation regression fails for unrelated reasons

Location: `tests/maintenance/test_p00_harness_failures.py`, `TestC2DirectResolverGuard.test_caught_violation_still_fails_parent_aggregation`.

The temporary fixture contains one of four required legacy scripts and an empty unittest discovery directory. Missing-script and empty-discovery failures independently force the parent to fail. The assertion that the parent exits nonzero therefore does not demonstrate network enforcement, even though the attempt-count assertion demonstrates recording.

Independent mutation check on an external scratch copy removed both parent enforcement decisions while preserving attempt recording:

```python
ok = (code == 0)                         # legacy result: attempt check removed
return 0 if exit_ok else 1               # final result: attempt check removed
```

The existing regression still passed (one test, zero failures). No worker or production file was changed for this probe.

Required correction: complete the fixture with four successful legacy scripts and one passing discovered test. First demonstrate the no-violation control exits0. Then change only the caught network attempt, require every child to exit0 but the parent to fail for its one recorded attempt. Verify that removing enforcement in a scratch copy makes this regression fail.

This is a test-quality defect, not a finding that the inspected parent currently accepts caught violations. It matters because P00's purpose is to establish a regression gate reliable enough for subsequent GLM packets.

## Evidence and next step

Independent exported-commit verification completed for `b9e1fe9`: legacy checks 60/31/63, multiturn pass, 22 discovered P00 tests, zero top-level failures/errors/skips, zero recorded network attempts, exit0. The recursion skip is inside a nested child; it is not a top-level skip in this reproduced run. The export contained 402 archive members and excluded untracked plans/specs.

The review used the same external Windows temporary/tar-directory permission workaround documented in the first P00 review. It adjusts directory creation in the review environment only; no worker source or assertions were modified. This is a qualified sandbox reproduction, not a claim that the original command runs without that environmental adjustment here.

All 364 original tracked files remain unchanged. The worker's tracked tree is clean, and its four-file corrective diff matches the authorized scope.

Mutation evidence is saved in:
`C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/p00_review_20260905/c1_mutation_evidence.json`.

Use the [P00-C2 test-only prompt](../plans/2026-09-06-glm-p00-c2-test-prompt.md). It permits only the regression file and a new worker report. No further harness or production changes are requested.
