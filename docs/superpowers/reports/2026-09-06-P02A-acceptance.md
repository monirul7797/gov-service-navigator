# P02A acceptance — Codex review

Date: 2026-09-06.

**Accept P02A at `ac2930a477078f500010ab31b05ed50875339fce`.** The four findings in the preceding review are resolved for the released configured HTTP policy/transport scope. Release P02B only; P02 is not complete until caller integration passes review, and P03 remains held.

Worktree: `C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p02a-1788646696036966800/worktree`; branch `codex/tracker-g0-p02a`.

Reviewed corrective range: `51a3afeca9e12b1b202b383a6f178c1f6e0d08d5` through final `ac2930a477078f500010ab31b05ed50875339fce`, including the separate transport corrective commit. Acceptance applies to the final combined committed tree, not the intermediate unstaged state.

## Findings resolved

- **Classification:** both original and decoded validation representations now fail closed unless their disposition is explicitly eligible. Independently reran the three reproduced login URL probes through real configured RequestsTransport with scripted sessions: each returned destination_disallowed with zero session calls.
- **Hosts:** stdlib IP canonicalization supports actual IPv6 matching, malformed configured IPv6 is rejected, and URL host validation precedes wildcard matching. Independently confirmed the allowed IPv6 positive case and malformed bracket-authority negatives. The new host tests cover equivalent IPv6 forms, whitespace, empty ports, scoped literals and malformed DNS labels.
- **Immutability:** the frozen dataclass prevents ordinary reassignment. Independently reproduced a rejected redirect-bound assignment. The committed tests cover assignment/deletion, input-set aliasing and bound-transport behavior.
- **Exceptions/cleanup:** the configured path now owns every received response with a finally block, catches Requests exceptions for expected transport outcomes and lets unexpected session errors propagate. The new tests exercise raising content properties, provenance, zero partial body, cleanup and the unscripted AssertionError case.

Independently ran all four new correction test classes: **32 tests passed**, with no skips or errors. These include the actual response-processing and transport boundaries, not patched redirect decisions.

## Exact committed export

```text
python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'
HEAD: ac2930a477078f500010ab31b05ed50875339fce
Interpreter: audited Python 3.12.10
414 tracked files exported; untracked excluded
Maintenance: 141 tests; failures 0; errors 0; skips 0
Legacy self-reported checks: 60 / 31 / 63; multiturn passed
attempted_network: 0
candidate exit code: 0
```

Qualification: this independent export run used the previously documented external sandbox-only tempfile/tar directory ACL workaround and command-local Git safe-directory settings. No repository implementation, test assertion or network guard was replaced. The separate correction-class run and local no-contact probes needed no temporary-directory workaround and made no network requests.

Preserved export: `C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/p00_review_20260905/scratch/candidate_verify_3ee13f6be1e74219a19e6679d3db6b42/candidate`.

## Worker report corrections

The committed P02A test module has **84 tests: 52 original plus 32 new**, not 52 total or 35 new. Combined with the accepted P00/P01 57-test baseline, this matches the independently verified 141 total. The corrective commit range changes four files; fixtures.py is not changed in that range. These reporting errors do not negate the final committed verification.

The statement that this correction is only post-capture is inaccurate. Configured P02A HTTP transport enforces destination policy **before contact**. Existing policy-less callers retain legacy behavior until P02B binds them. Browser and robots ports await P03. Do not describe the whole application as protected yet.

## Scope and preservation

The tracked worktree is clean; its untracked P01 acceptance report is preserved. `git diff --check` passed. AST comparisons confirm PlaywrightRenderer, RenderResult and Interactable are unchanged across the corrective range. All 364 tracked original-checkout files match the saved baseline, including the user's three existing acquisition edits. No production implementation or corpus/vector changes were made by this review.

## Next packet

[P02B ready-to-paste prompt](../plans/2026-09-06-glm-p02b-ready-prompt.md) fixes the integration architecture: nonmutating policy binding, intersection with existing restrictions, explicit allowlist/per-source policy derivation, terminal HTTP navigation failures and real-caller integration tests. Directly injected legacy fake ports remain a test-compatibility boundary, not a new transport safety guarantee. P05 will require supported configured adapters for tracker construction.

P02B starts from this accepted final SHA in a new isolated worktree and stops for review. No P03 release, merge, live crawl or production activation is authorized by this acceptance.
