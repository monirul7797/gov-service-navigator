# P02A review — corrections required

Date: 2026-09-06. Reviewer: Codex, phase owner.

**Decision: hold P02A at `51a3afeca9e12b1b202b383a6f178c1f6e0d08d5`. Release P02A-C1 only. P02B/P03 remain held.**

Reviewed worktree: `C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p02a-1788646696036966800/worktree`, branch `codex/tracker-g0-p02a`. Accepted predecessor: `41b8eed0db6fdcc2864fe55c50c4dfc4066400e1`.

## Findings

### 1. High — invalid classification permits wizard/login contact

`acquisition/navigation_policy.py:142–148` rejects only `wizard`. The reused classifier can return `invalid` before inspecting wizard patterns. That result currently proceeds to hostname matching and permission.

Independent probes using the real policy and real configured RequestsTransport with a scripted session produced:

| URL | policy allows | result ok | Session request trace |
|---|---|---|---|
| `https://gov.example/login?x=1,2` | True | True | The login URL was requested |
| `https://gov.example/log%69n?x=1,2` | True | True | The encoded login URL was requested |
| `https://gov.example/x%20y/log%69n` | True | True | The encoded login URL was requested |

The comma causes original normalization to return invalid; the decoded space can do the same to the decoded-path representation. Neither is treated as a denial. This violates the pre-contact wizard/login boundary even on an explicitly allowed host. Fix both classification stages to fail closed, preserving eligible positive controls and legacy helper behavior.

### 2. Medium — configured IPv6 support does not work and host validation is incomplete

`acquisition/navigation_policy.py:48–83` accepts malformed `[:::]` using a character regex instead of validating an IPv6 address. It stores brackets for valid configured IPv6, while `parsed.hostname` omits them. Independently confirmed that configuration `{ '[2001:db8::1]' }` succeeds but `allows('https://[2001:db8::1]/')` is False.

The committed IPv6 test checks construction only, so it misses unusable matching. URL hostnames also reach wildcard suffix matching without the DNS-label validation applied to configuration. Use consistent local DNS/IP validation and canonicalization, with actual matching tests. Stdlib ipaddress suffices; no dependency or DNS access is needed.

### 3. Medium — the advertised immutable policy is mutable

`acquisition/navigation_policy.py:86–95` is an ordinary mutable class. Independently assigning `max_redirects = 100` and `_hosts = frozenset({'external.example'})` succeeded; the policy then allowed `https://external.example/`.

An immutable set does not prevent replacement of that set or reassignment of the bound. This breaks the released configuration contract for a policy already shared with a transport. Use a frozen configuration and test ordinary reassignment/deletion plus input-set aliasing.

### 4. Medium — response-read exceptions skip cleanup; broad catch hides fixture failures

`acquisition/transport.py:147–149` reads terminal content outside a response-owned finally block. A received response whose content property raises Requests ChunkedEncodingError caused the exception to escape and its close count to remain zero in an independent probe.

At `acquisition/transport.py:136`, the catch-all around session.get has the opposite problem: the scripted session's unscripted-request AssertionError was swallowed and returned as `error_kind='connection'`. This contradicts the fixture's intended loud failure and disguises programming errors as operational failures.

Use one finally cleanup per received response and catch only expected Requests exceptions as transport outcomes. Read failures must return a failed result with provenance and no partial body; unexpected programming exceptions must propagate after cleanup. Preserve existing redirect order and exact close counts.

## Verification and scope evidence

Independently ran the exact committed candidate verifier with Python 3.12.10:

```text
python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'
HEAD: 51a3afeca9e12b1b202b383a6f178c1f6e0d08d5
413 tracked files exported; untracked excluded
Maintenance: 109 tests; failures 0; errors 0; skips 0
Legacy self-reported checks: 60 / 31 / 63; multiturn passed
attempted_network: 0
candidate exit code: 0
```

This pass uses the previously documented external sandbox-only tempfile/tar-directory ACL workaround and command-local Git safe-directory settings. No production source, assertions or network guards were replaced. The pass verifies the existing suite, not the missing cases above. All independent defect probes used local objects and injected sessions; no live requests were made.

Preserved export: `C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/p00_review_20260905/scratch/candidate_verify_a63237662e7342b5bfc5a360c45b09f1/candidate`.

The five committed changed files match the packet scope. The tracked worktree is clean and `git diff --check` passed. AST comparison confirms PlaywrightRenderer, RenderResult and Interactable are unchanged from accepted P01. All 364 tracked original-checkout files match the saved pre-work SHA256 baseline, including the user's existing edits. No production implementation changes were made during this review.

## Next action

Send GLM [the P02A-C1 corrective prompt](../plans/2026-09-06-glm-p02a-correction-prompt.md). It specifies the four bounded fixes, behavioral RED tests, unchanged scope, fresh corrective commit, exported verification and mandatory stop. The original P02A tests remain valuable and must stay passing; do not reduce their coverage to hide these failures.
