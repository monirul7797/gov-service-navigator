# Ready-to-paste GLM prompt — P00-C1 correction only

Copy the text below into the existing GLM implementation conversation.

---

You are GLM 5.3 Flash, the implementation worker. Codex reviewed P00 commit `94072022d0dc64507a261a6f8a91c6ec915b3eec` and requests corrections. **P00 is not yet accepted. P01 is not released.**

Execute **P00-C1 only**, correcting the three findings below. Keep architectural ownership with Codex. Do not start tracker features or fix unrelated audit defects.

Continue in the existing worktree:
`C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p00-1788625590422472000/worktree`

Branch: `codex/tracker-g0-p00`.
Expected starting HEAD: `94072022d0dc64507a261a6f8a91c6ec915b3eec`.

Check status/HEAD first. Preserve untracked plans/specs and any user changes. Do not reset or amend the P00 commit. If HEAD or tracked edits differ, report the difference before proceeding.

Read the existing control guide/P00 plan and the new review report in the original project:
`C:/Users/Monirul/Documents/Structured Data/NID/gov-service-navigator/gov-service-navigator/docs/superpowers/reports/2026-09-05-P00-codex-review.md`

The review report and this prompt are not automatically present in your worktree. You can read them directly; do not stage copied planning documents with your correction.

## Allowed files

- Modify `tools/run_offline_checks.py`.
- Modify `tools/verify_candidate.py`.
- Create `tests/maintenance/test_p00_harness_failures.py`.
- Modify `tests/maintenance/test_p00_baseline.py` only if necessary to share the deliberate-probe isolation setup; preserve all nine existing tests.
- Create `docs/superpowers/reports/P00-C1-worker-report.md`.

No acquisition/production modules, legacy suite assertions, corpus files, vector data or existing user edits may change.

## Correction 1 — Reject empty test runs

Reproduction on your P00 commit:

```powershell
python -B tools/run_offline_checks.py --pattern 'test_p00_typo_no_match_*.py'
```

It returns exit0 and `ok:true` with ran0. This is false success.

1. Add failing CLI-level tests for a nonmatching pattern and an explicitly empty pattern. Include the nonmatching-pattern case with `--legacy` to prove passing legacy suites cannot mask the missing requested packet tests.
2. Reject empty/whitespace patterns at argument validation. After discovery, require a nonzero discovered-test count. Emit a stable `NO_TESTS_DISCOVERED` diagnostic for a valid pattern matching zero tests and return nonzero.
3. Preserve normal unittest failures/errors/skips and legacy script exit handling. Do not hardcode nine tests or restrict valid future packet patterns.
4. Normal `--pattern 'test_p00_*.py'` must continue to execute all P00 tests, including your new regressions.

Negative subprocess fixtures must be isolated so they do not recursively execute their own parent tests indefinitely. For the legacy-plus-empty case, the child selects only the deliberately nonmatching pattern.

## Correction 2 — Guard direct DNS resolver calls

The existing guard covers `getaddrinfo` but not `socket.gethostbyname`, `gethostbyname_ex` or `gethostbyaddr`.

1. Add subprocess regressions that replace the underlying resolver with a harmless sentinel before `install_guard`, then invoke the named resolver after installation. No real network/DNS calls are allowed in these tests.
2. Guard all three named functions, record `NETWORK_ATTEMPT` before raising `OfflineGuardViolation`, and ensure the sentinel is not reached. Retain the existing getaddrinfo/socket/HTTP guards and the non-OSError exception contract.
3. Add a regression where the tested code catches the violation and otherwise exits successfully. The parent harness must still return nonzero because its guard log records the attempt. Test the real parent aggregation path, using a separate temporary discovery fixture/root or an injected subprocess boundary; do not merely assert the exception type.
4. Deliberate negative subprocesses get their own guard logs. The outer successful P00 suite must still report attempted_network0. Never suppress ordinary tests' attempts to make them pass.

Use subprocess-isolated tests for guard installation because its monkeypatches are process-wide. Avoid resetting real network functions in the main guarded test process.

## Correction 3 — Validate Windows archive containment

Your current validator allows `C:/outside/probe.txt` and `C:outside.txt` to reach extraction. It only checks leading slash and parent traversal.

1. Add failing tests using synthetic TarInfo members and an extraction spy. Test drive-absolute, drive-relative, UNC, backslash-rooted, `..`, symlink, hardlink, nonregular member and Windows alternate-data-stream names. Do not extract unsafe fixtures for real.
2. Reject drives/roots using explicit Windows path semantics even if tests later run on another platform. Reject colon-bearing components that could select a drive or alternate data stream on Windows. Normalize slash/backslash handling consistently.
3. For each otherwise valid member, resolve its destination against the resolved extraction root and require actual path containment; do not use string-prefix containment. Validate all members before calling extraction once. Keep archive link/nonregular rejection.
4. Add a valid nested-file archive test that actually extracts beneath a temporary root, plus a mixed safe/unsafe archive test proving no extraction starts when any member is invalid.
5. Keep exported verification operating on committed code only. Do not copy untracked files or change the source checkout to make export checks succeed.

## Tests and local commit

Demonstrate the new regressions fail for the intended reasons against the P00 behavior, implement the fixes, then run:

```powershell
python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'
git diff --check
```

Also run the deliberately nonmatching command and show its expected nonzero exit. Expected-negative command failure is not failure of the enclosing regression test; report both accurately.

Stage only the explicit allowed paths that changed and make a new local commit:

```text
fix(tracker): P00-C1 close verification false-success paths
```

Then run:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p00_*.py'
```

Use the existing audited Python interpreter. Codex needed an external Windows temporary-directory permission workaround for its own sandbox; do not copy that reviewer shim into production or treat it as one of these fixes. Report an environment failure separately if it occurs in your environment.

## Report and stop

Return base/final commit SHAs, branch/worktree, changed files, red/green commands, new test counts, ordinary run attempted-network count, expected-negative outcomes, exported-commit result and remaining limitations. Map evidence to all three corrections explicitly. Preserve the original P00 report; put this packet's report in the new report file.

Do not push, merge, modify the original checkout or proceed to P01. End with: **“P00-C1 complete for review; stopped. P01 has not been started.”** If incomplete, state that honestly instead.
