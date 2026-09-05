# P00 worker report — Reproducible baseline and offline test harness

Packet: P00 (G0)
Base commit and branch/worktree: `8d3c83cfe35ac4f5ba7667856b8562c300aeeaee` on branch `codex/tracker-g0-p00`, isolated worktree created per the P00 procedure (patch carried with `git diff --binary` + `git apply`; original checkout untouched).
Final local commit SHA(s): reported in the dispatch response after commit and exported verification.

## Scope

Carry and reconcile the three pre-existing tracked acquisition edits
(`acquisition/acquire.py`, `acquisition/crawler.py`, `acquisition/transport.py`;
63 insertions / 5 deletions, matching the audit), repair test fixture
hermeticity in the three legacy suites, and build the offline runner and
exported-commit verifier. No new production behavior beyond the carried edits.

## Changes and what each verifies

| File | Change | Behavior verified |
|---|---|---|
| `tools/run_offline_checks.py` | new | Offline guard (blocks/records socket connect, connect_ex, create_connection, getaddrinfo, urllib urlopen, requests Session.request; raises `OfflineGuardViolation(RuntimeError)` — not OSError), `--legacy` child execution preserving SystemExit, `--pattern` unittest discovery under `tests/maintenance`, `--probe-network` fail-closed mode, attempt-count failure semantics |
| `tools/verify_candidate.py` | new | Exported-commit verification: `git archive HEAD` → member validation (rejects escapes/links/nonregular) → same-interpreter offline run of the candidate |
| `tests/maintenance/fixtures.py` | new | `robots_for_hosts`, `hosts_from_corpora`, `patch_crawler_robots`, `scripted_session` — fixture fakes injected at external boundaries; no production bypasses |
| `tests/maintenance/test_p00_baseline.py` | new | Guard probe regression (RED before the runner existed), transport final_url contract via injected fake session, StrictResult/RenderResult defaults, Capture.to_provenance destination fields, real `KnownSourceMonitor.run` consumer in temp fixtures, acquire/crawler allowlist-gate regression |
| `test_freshness.py` | fixture isolation | `make_monitor` pre-seeds allow parsers for fixture corpus hosts when no explicit robots cache is supplied (production robots semantics untouched) |
| `test_acquisition.py` | fixture isolation | replaced the unused `allow_all_robots()` `'*'`-sentinel helper with `robots_for_hosts(actual hosts)`; crawler constructions inject pre-seeded caches via `patch("acquisition.crawler.RobotsCache", ...)` |
| `test_freshness_v21.py` | fixture isolation | direct `KnownSourceMonitor`/crawler constructions inject pre-seeded robots caches |
| `acquisition/acquire.py`, `acquisition/crawler.py`, `acquisition/transport.py` | carried unchanged | R1 reconciliation: final_url on StrictResult/RenderResult, allowlist gate + provenance fields, crawler allowlist threading — byte-identical to the audited working diff |
| `tests/__init__.py`, `tests/maintenance/__init__.py` | new | package markers for discovery |

## Commands and outcomes (worktree, Python 3.12.10 `C:/Users/Monirul/AppData/Local/Programs/Python/Python312/python.exe`)

1. Guard regression before the runner existed: `python -B -m unittest discover -s tests/maintenance -p 'test_p00_*.py'` → FAILED (runner missing) — the intended RED.
2. `python -B tools/run_offline_checks.py --probe-network` → exit 1, `NETWORK_ATTEMPT: socket.create_connection ('gov.example', 443)` before contact.
3. First guarded legacy run BEFORE fixture repairs: test_freshness / test_freshness_v21 / test_acquisition each aborted on a real robots.txt fetch attempt (urlopen/create_connection), attempted_network=3 — reproducing the audit's hermeticity finding.
4. After fixture repairs: `python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'` → exit 0; self-reported legacy checks [60, 31, 63] (test_multiturn prints no count), attempted_network=0.
5. `git diff --check` → exit 0.

Known limitations: the legacy suites self-report counts via printed result lines (the runner gates on their exit codes and surfaces those counts); the P00 probe subprocess intentionally does not inherit a guard log (its own attempt is asserted via stderr + nonzero exit).

## Scope discipline

- The three acquisition edits were carried byte-identically from the audited working diff (verified by diff stat before/after apply).
- Plans/specs were copied into the worktree for reading only and remain unstaged; review bundles were not touched, staged, or copied.
- No corpus, vector, tracker-database, news, OCR, embedding, routing or cutover work was performed. No pushes, merges, resets or history rewrites.
- R6/R8 are NOT fixed by P00 (P01/P02/P03 scope); the carried final_url edits do not prove pre-contact redirect blocking.

STOPPED; next packet not started.
