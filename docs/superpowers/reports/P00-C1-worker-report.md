# P00-C1 worker report — close verification false-success paths

Packet: P00-C1 (corrections to P00; P00 remains unaccepted until this is reviewed)
Base commit: `94072022d0dc64507a261a6f8a91c6ec915b3eec` (P00) on `codex/tracker-g0-p00`, same worktree.
Final local commit SHA: reported in the dispatch response after commit and exported verification.

## Changed files

| File | Change | Correction |
|---|---|---|
| `tools/run_offline_checks.py` | blank/whitespace `--pattern` rejected at argument validation (`INVALID_PATTERN`, exit 2); discovery child fails closed with stable `NO_TESTS_DISCOVERED` (exit 3) when a valid pattern matches zero tests; parent requires `ran > 0` from the discovery child (passing legacy suites cannot mask it); guard now also blocks `socket.gethostbyname`, `gethostbyname_ex`, `gethostbyaddr` with `NETWORK_ATTEMPT` recording before the same non-OSError `OfflineGuardViolation`; `--probe-network` isolates its own guard log so its deliberate attempt never pollutes a parent count | C1, C2 |
| `tools/verify_candidate.py` | archive validation rewritten as `_member_errors` + `_plan_extraction`: Windows drive-absolute/drive-relative (`C:/x`, `C:x`), UNC, backslash-rooted, colon-bearing (drive/ADS `file.txt:stream`) names rejected; slash/backslash normalized first; symlinks/hardlinks/nonregular members rejected; containment decided on RESOLVED destinations via `os.path.commonpath` (no string-prefix checks); ALL members validated before the single `extractall` | C3 |
| `tests/maintenance/test_p00_harness_failures.py` | new: 13 integration tests over the real CLI/aggregation paths (see mapping below) | C1, C2, C3 |

`test_p00_baseline.py` was NOT modified (sharing the probe-isolation setup was not necessary — the C1 negative subprocesses isolate their own guard logs directly). No acquisition/production modules, legacy assertions, corpus or vector files changed.

## Evidence mapping

### C1 — empty discovery rejected
- RED: `test_nonmatching_pattern_returns_nonzero_with_diagnostic` (exit 0 before the fix).
- GREEN: `--pattern 'test_p00_typo_no_match_*.py'` → exit 1 + `NO_TESTS_DISCOVERED`; `--legacy` variant → exit 1 with legacy suites genuinely executed inside the child (self-reported 60/31/63 visible in its output) and still masked by nothing; blank/whitespace → exit 2 + `INVALID_PATTERN`; normal `test_p00_*.py` child → exit 0, ran 22, skips 1 (the recursion-guard test documents its own skip in the child's unittest counts).

### C2 — direct DNS resolvers guarded
- RED: the three resolver probes reached the sentinel with zero attempts recorded.
- GREEN: `gethostbyname`/`gethostbyname_ex`/`gethostbyaddr` probes each raise `OfflineGuardViolation` with `SENTINEL_CALLS=0` and `NETWORK_ATTEMPT` on stderr; `getaddrinfo`/socket/HTTP guards and the non-OSError contract retained; `test_caught_violation_still_fails_parent_aggregation` reproduces the real parent path (temp repo layout, copied production runner, legacy-named child catches the violation and exits 0) → parent exits nonzero with `attempted_network=1`. Negative subprocesses use their own guard logs; the outer suite reports attempted_network=0.

### C3 — Windows archive containment
- RED: `C:/outside/probe.txt` and `C:outside.txt` reached extractall under the old validator.
- GREEN: `_plan_extraction` rejects drive-absolute, drive-relative, UNC, backslash-rooted, traversal-resolving-outside, ADS/colon-bearing names, links and nonregular members — verified with synthetic TarInfo members and an extractall spy asserting extraction never starts (including a mixed safe+unsafe archive); a valid nested archive validates with zero extraction calls inside `_plan_extraction` and then really extracts once to the correct nested content.

## Commands and outcomes (worktree, Python 3.12.10)

- `python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'` → exit 0; legacy [60, 31, 63] + multiturn pass; discovery ran 22 (1 documented skip); attempted_network=0.
- `git diff --check` → exit 0.
- Expected-negative demonstrations (failures of the command, not of the enclosing tests): nonmatch+legacy → exit 1 + NO_TESTS_DISCOVERED; blank pattern → exit 2 + INVALID_PATTERN.
- Exported-commit verification: rerun after commit; result reported in the dispatch response.

## Limitations

- The normal-pattern child skips exactly one test (its own recursion guard); the skip is visible in the child's reported unittest counts.
- Resolver probes prove the guard intercepts before the named resolver attribute; they do not send DNS queries by design.
- R2–R9 dispositions and P02/P03 pre-contact redirect blocking remain as previously scoped.

STOPPED; next packet not started.
