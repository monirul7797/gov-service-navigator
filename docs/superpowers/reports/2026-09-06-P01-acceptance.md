# P01 acceptance — Codex review

Date: 2026-09-06

**Decision: accept P01 at `41b8eed0db6fdcc2864fe55c50c4dfc4066400e1`.** No blocking findings remain for the released R6/R8 trust-check scope. P02 is not accepted or implemented by this decision.

## Reviewed state

- P00 accepted base: `f808ce3c564d91544219abc8edcac96a58011f14`.
- Initial P01: `b7289d79e431d77017eae249e226dc15a141334f`.
- P01-C1 final: `41b8eed0db6fdcc2864fe55c50c4dfc4066400e1`.
- Branch: `codex/tracker-g0-p01`.
- Worktree: `C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p01-1788642519219644900/worktree`.

The corrective commit changes the two authorized production files, P01 tests, the genuine encrypted fixture and its worker report. The tracked worktree is clean; untracked planning documents are preserved. `git diff --check` passed.

## Correction assessment

**C1 accepted.** Final-URL syntax/scheme/hostname/port checks run before domain comparison and content acceptance. Invalid destinations become suspect captures rather than trusted content or uncaught parsing exceptions. Tests cover a malformed source followed by a valid source, existing trusted/candidate preservation and dry-run non-persistence. This remains post-capture validation; prevention of unwanted HTTP/browser contacts belongs to P02/P03.

**C2 accepted.** Encryption metadata is inspected inside the real parser context. Independently rechecked the committed 828-byte fixture: SHA256 `9f2cea19ab245c5307614bf627ffff3caedcb62ee993132fbc9a2978f3fcc69e`; pdfplumber successfully opens one page and reports encryption. The corrected validator returns False with `encrypted_pdf: document reports encryption metadata (tuple)`. Thus the regression distinguishes encryption from inability to parse.

**C3 accepted based on the new frozen-state regression.** Independently wrapped the real monitor's run method in memory to corrupt the temporary trusted snapshot after the suspect capture. The actual committed `test_real_collapse_preserves_frozen_trusted_snapshot` then failed at its frozen-byte equality assertion, with one intended failure and zero errors. Production files were not patched. This verifies that the new assertion detects the previously missed damage.

## Independent exported verification

Executed the committed verifier with the audited Python 3.12.10 interpreter:

```text
python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'
HEAD: 41b8eed0db6fdcc2864fe55c50c4dfc4066400e1
410 tracked files exported; untracked excluded
Legacy self-reported checks: 60 / 31 / 63; multiturn passed
Maintenance: ran 57; failures 0; errors 0; skips 0
attempted_network: 0
candidate exit code: 0
```

Qualification: the reviewer sandbox requires the previously documented external `sitecustomize.py` workaround for Windows temporary-directory/extraction ACL inheritance. It redirects temporary directories and adjusts directory creation permissions only; no repository code, test assertion or offline network guard is replaced. Command-local Git safe-directory settings were also used. This is a sandbox-qualified independent pass, not a claim to have reproduced the worker's unmodified shell environment.

Preserved exported candidate:
`C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/p00_review_20260905/scratch/candidate_verify_aa14b3bca306420881a34ce45d879d33/candidate`.

## Nonblocking precision notes

The worker report overstates some test cleanup. The old self-referential collapse assertion remains; a separate, effective frozen-byte test was added. The new `_frozen_state` helper compares snapshot names, not their contents; the pre-existing dry-run `state_bytes` test supplies snapshot-byte coverage. The new corruption/deletion demonstration only compares intentionally altered fixture values; the independent mutation of the actual preservation test, described above, supplies stronger causal evidence. The new rejected-PDF event comparison covers selected fields rather than complete event bytes.

These observations do not negate the corrected production behavior or the effective regression coverage. No further P01 corrective packet is required. Do not describe the old assertion as removed or every new state helper as a complete byte comparison in subsequent reports.

## Preservation and next release

All 364 tracked original-checkout files match the saved pre-work SHA256 baseline, including the user's three pre-existing acquisition edits. Original HEAD remains `8d3c83cfe35ac4f5ba7667856b8562c300aeeaee`; accepted P00 remains at `f808ce3c564d91544219abc8edcac96a58011f14`. No push, merge, live crawl, corpus update or embedding change was performed.

As phase owner, Codex splits P02 into two review stops to keep the GLM tasks bounded:

1. **P02A:** pure navigation policy and configured manual HTTP redirects, with request-trace tests.
2. **P02B:** bind policies in acquisition and known-monitor callers and prevent unsafe fallback after policy failures. Its exact integration prompt follows P02A review.

Only P02A is released now through [the ready-to-paste prompt](../plans/2026-09-06-glm-p02a-ready-prompt.md). P02 remains incomplete until both parts are accepted. P03 and the G0 gate remain held.
