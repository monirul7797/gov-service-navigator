# P00 acceptance and P01 release

**Decision: P00 accepted at `f808ce3c564d91544219abc8edcac96a58011f14`. P01 is released; P02 is not.**

Date: 6 September 2026. Reviewer/phase owner: Codex.

## Accepted history and scope

- Initial P00: `94072022d0dc64507a261a6f8a91c6ec915b3eec`.
- Harness corrections P00-C1: `b9e1fe972d85aa3f93aef028b5215ed844db1698`.
- Test correction P00-C2: `8f7db8f65bf2a2bc345522016e3e677c580df436`.
- Final worker report: `f808ce3c564d91544219abc8edcac96a58011f14`.

The final test correction changes only `tests/maintenance/test_p00_harness_failures.py` and adds its report. Both harness tools remain identical to P00-C1. The worker tracked tree is clean. The existing acquisition edits were preserved, and all 364 original tracked files still match the original audit hashes in the original checkout.

## Verification

Independently ran the exported-commit verifier against final HEAD, including the report commit:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p00_*.py'
```

Result: exit0; 24 P00 tests; zero top-level failures/errors/skips; zero recorded network attempts. Legacy checks: freshness60, V2.1 31, acquisition63; multiturn also passes. The documented recursion-prevention skip occurs inside a nested child, not the reproduced top-level result. Export excluded untracked plans/specs.

The corrected aggregation fixture contains all four legacy scripts and one actual passing unittest. Its positive control succeeds. With one caught guard violation, all children still exit0 but the parent rejects the recorded attempt.

Independently removed both parent network-enforcement decisions in an external scratch copy while retaining recording, then ran the actual corrected caught-violation regression against that copy. It failed at `assertNotEqual(result.returncode, 0)` with `0 == 0`, as expected. This proves the regression now detects the enforcement failure, rather than passing because unrelated fixture files/tests are missing. Worker source was never mutated for this check.

Evidence: `C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/p00_review_20260905/c2_mutation_evidence.json`.

The exported run used the previously documented external Windows temporary/tar-directory permission workaround. It alters directory creation in the review environment only; it does not modify tested code, assertions or network enforcement. Interpreter: the audited Python3.12.10. This qualification remains part of the review evidence.

## Acceptance boundary

P00 establishes the reproducible baseline and tested verification harness. It does not accept Freshness V2.1 for unattended production, close all original audit defects, complete G0, enable monitoring or authorize live corpus/vector publication. The tar filter deprecation warning is recorded and does not block this Python3.12 gate.

No merge, push or original-checkout source change occurred. Accepted commits remain on `codex/tracker-g0-p00`; P01 must start from the final accepted SHA in its own worktree.

## Next worker packet

Use the [ready-to-paste P01 prompt](../plans/2026-09-06-glm-p01-ready-prompt.md).

P01 fixes dry-run baseline comparison and rejects untrusted/invalid known-source PDFs before trusted/candidate state changes. It explicitly excludes pre-request redirect enforcement (P02/P03), tracker features, corpus changes and embedding. GLM must commit locally, report and stop for Codex review.
