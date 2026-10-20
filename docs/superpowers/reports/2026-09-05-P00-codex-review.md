# P00 Codex review

**Decision: changes requested. P01 is not released.**

Reviewed commit: `94072022d0dc64507a261a6f8a91c6ec915b3eec` on `codex/tracker-g0-p00`.

Worker worktree: `C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p00-1788625590422472000/worktree`.

## What passed review

- The three acquisition files match the original checkout byte-for-byte. Their Git-normalized contents also match the committed blobs. These are preserved existing edits, not new fixes attributed to GLM.
- Fixture changes inject robots state at test boundaries; they do not change production robots policy or remove the old assertions.
- Scope is limited to the carried acquisition edits, fixture/test changes, verification tools and worker report: 13 committed files.
- Reproduced the four legacy suite passes and nine new unittest passes, with zero recorded network attempts. Legacy reported counts were 60/31/63; multiturn passed but its numeric count is not captured by this harness.
- Reproduced the same results through the exported-commit verifier against HEAD `9407202`, without copying untracked plans/specs into the export.

## Required corrections

### C1 — Empty discovery is accepted as success

Location: worker `tools/run_offline_checks.py:176` and `:284`.

Command reproduced:

```powershell
python -B tools/run_offline_checks.py --pattern 'test_p00_typo_no_match_*.py'
```

Actual result: exit0, ran0, failures0, errors0, `ok:true`. A misspelled packet pattern can therefore pass this gate without executing the requested tests. Blank patterns also bypass the discovery branch by construction.

Required: reject blank patterns and zero-discovered-test runs with a clear nonzero result. The parent must preserve this failure, including when the legacy suites pass. Add CLI-level regressions rather than only testing a count helper.

### C2 — Direct DNS lookup bypasses the network guard

Location: worker `tools/run_offline_checks.py:87-123`.

The guard replaces `getaddrinfo` but leaves `socket.gethostbyname` and related direct resolver entry points available. A no-network probe replaced the underlying gethostbyname with a harmless sentinel before installing the guard. Calling it afterward reached the sentinel and recorded zero attempts. No DNS query was actually sent by this review.

Required: guard `gethostbyname`, `gethostbyname_ex` and `gethostbyaddr` alongside the existing getaddrinfo guard. Record attempts before raising the same non-OSError violation. Add subprocess regressions proving the underlying resolver is never reached, and that a caught violation still makes the parent run fail through its guard log. Keep deliberate negative-test logs separate from the outer successful test run.

### C3 — Windows archive path escapes are not rejected

Location: worker `tools/verify_candidate.py:38-54`.

The validator checks leading slash and `..` only. Synthetic TarInfo entries `C:/outside/probe.txt` and `C:outside.txt` pass validation and reach extractall. The review replaced extractall with a spy, so nothing was written to those paths. Slash-rooted and parent-traversal controls were correctly rejected.

Required: validate Windows drives, drive-relative paths, UNC/rooted paths, alternate data streams and resolved destination containment, in addition to existing traversal/link/type checks. Validate the entire member list before extraction. Add tests asserting extraction never starts if any member is invalid, and a real safe nested-file extraction succeeds.

The archive is currently generated from local Git; this review does not claim an observed malicious archive or data escape. It is a concrete failure of the P00 contract to reject path escapes on its target Windows platform.

## Verification qualification

The first unmodified command failed in this Codex sandbox because Python-created mode0700 temporary directories could not be populated. The candidate verifier then encountered the equivalent tar-directory problem. These are review-environment limitations, not classified as GLM product defects.

Reproduction used an external `sitecustomize.py` that changes only temporary-directory and tar-directory creation to inherit writable Windows permissions. Worker source, test assertions, network guard and comparison behavior were not changed. Git safe-directory settings were supplied only to the review command environment, not written to Git configuration.

Qualified exported result: 60 freshness, 31 V2.1, 63 acquisition checks, multiturn pass, nine P00 tests, zero failures/errors/skips and zero recorded network attempts. This confirms the selected tests pass; it does not negate C1-C3.

External review evidence is under:

`C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/p00_review_20260905/`

The original project remains on audited HEAD `8d3c83cfe35ac4f5ba7667856b8562c300aeeaee`. No production fixes, corpus changes, vector updates, pushes or merges were performed during review.

## Next handoff

Use [P00-C1 corrective prompt](../plans/2026-09-05-glm-p00-correction-prompt.md). GLM must correct only the verification harness/validator and add their regressions, commit locally and stop. Codex will review that corrective commit before deciding P00 acceptance and issuing P01.
