# Ready-to-paste GLM prompt — P02A-C1 corrections only

You are GLM 5.3 Flash, the implementation worker. Codex reviewed P02A at `51a3afeca9e12b1b202b383a6f178c1f6e0d08d5` and has **not accepted it**. Implement this corrective packet only, then stop. P02B/P03 remain held.

Read the original P02A prompt and `docs/superpowers/reports/2026-09-06-P02A-codex-review.md` in the original project:
`C:/Users/Monirul/Documents/Structured Data/NID/gov-service-navigator/gov-service-navigator`.

Work in the existing P02A worktree:
`C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p02a-1788646696036966800/worktree`, branch `codex/tracker-g0-p02a`.

Verify HEAD is exactly `51a3afeca9e12b1b202b383a6f178c1f6e0d08d5` and the tracked tree is clean. Preserve untracked documents, all accepted worktrees, original checkout and user files. Do not amend the existing commit. No production crawling, push, merge, package installation, corpus or vector changes.

Allowed changes: `acquisition/navigation_policy.py`; the P02A HTTP-only methods/helpers in `acquisition/transport.py`; `tests/maintenance/test_p02a_http_policy.py`; narrowly necessary additions to `tests/maintenance/fixtures.py`; new `docs/superpowers/reports/P02A-C1-worker-report.md`. Do not change browser code, legacy URL helpers, acquisition/monitor callers, harnesses or P00/P01 tests. Do not implement P02B while correcting these findings.

## Correction 1 — invalid classification must not bypass wizard exclusion

The real policy and transport currently allow/contact all of these on an allowed host:

```text
https://gov.example/login?x=1,2
https://gov.example/log%69n?x=1,2
https://gov.example/x%20y/log%69n
```

`classify_url` returns `invalid` before its wizard check when its normalization rejects a comma or a decoded space. P02A rejects only `wizard`, so it converts that invalid result into permission.

Fix the local policy to fail closed for `invalid` as well as `wizard` on both original and decoded validation representations. Only the explicitly eligible dispositions (`ok`, `pdf`, `boilerplate`) may pass this classification stage. Do not globally loosen `classify_url` to admit invalid URLs. Keep path/query bytes unchanged in actual allowed requests.

The decoded representation is for validation only. Conservatively rejecting a representation the existing classifier cannot validate is acceptable for this packet; do not invent a separate tolerant classifier. Keep the documented informational/PDF/boilerplate positive controls.

Write RED tests for all three URLs through `allows` and the real configured transport. For each, assert forbidden initial URL causes zero session calls; then serve it as a Location from an allowed A and assert only A is requested, with `destination_disallowed`. Include a semicolon query variant and a normal allowed query positive control.

## Correction 2 — validate and canonicalize hosts consistently

The constructor accepts `[:::]` as an IPv6 literal. It accepts `[2001:db8::1]`, but `allows('https://[2001:db8::1]/')` returns False because configuration retains brackets while `parsed.hostname` does not. Host validation is also absent on the actual URL hostname before wildcard suffix matching.

Use stdlib `ipaddress` for IP literals, not a character regex. Accept bracketed or unbracketed IPv6 host configuration and store a canonical bracket-free representation. Match equivalent compressed/expanded IPv6 forms consistently. Keep bracket syntax in URLs valid and reject junk following a closing bracket, unmatched brackets, and explicitly empty ports. Reject scoped IPv6 literals for now; their interface-specific meaning is outside this host registry. No DNS lookup.

For DNS configuration and URL hosts, share local validation/canonicalization: case-insensitive names, nonempty valid labels, no wildcard in an actual URL hostname. Wildcards are only configuration syntax for proper DNS subdomains. Preserve the apex-versus-subdomain rule. Reject configuration whitespace instead of stripping it into a valid host; invalid host configuration raises ValueError. If trailing-dot DNS names are supported, normalize them consistently on both configuration and URL sides. Do not apply DNS wildcard matching to IP literals.

Tests must exercise actual matching, not merely successful construction: valid IPv4 and IPv6; equivalent IPv6 forms; invalid `[:::]`; invalid brackets/authority; wildcard matching a normal subdomain but rejecting malformed labels such as `bad..gov.example`; malformed host configuration; empty port. Prove an allowed IPv6 URL can reach the scripted session and succeed without live network. Existing exact-host and suffix-impostor tests must remain intact.

## Correction 3 — make the policy immutable as specified

These ordinary assignments currently succeed:

```python
policy.max_redirects = 100
policy._hosts = frozenset({'external.example'})
```

The latter changes the effective allowlist of an already bound transport. A frozenset value alone does not make its containing configuration immutable.

Use a frozen dataclass or equivalent ordinary assignment protection. Preserve the public constructor, `allowed_hosts` and `max_redirects` interfaces; use controlled initialization to store normalized values. This is an immutable configuration contract, not an attempt to defeat deliberate Python reflection.

Write RED tests proving reassignment/deletion of the effective host set and redirect bound are rejected and policy decisions stay unchanged. Also prove mutating an input set after construction cannot change the policy. Confirm a transport bound to that policy still denies the external host after attempted mutation.

## Correction 4 — response cleanup and honest exception boundaries

A response whose `.content` raises `requests.exceptions.ChunkedEncodingError` currently escapes `fetch` and its `close()` count remains zero. Conversely, the blanket `except Exception` around `session.get` swallows a scripted session's `AssertionError('unscripted request ...')` and calls it a connection error.

Restructure the configured HTTP path so every received response is owned by one `try/finally` cleanup, closed exactly once even if response processing fails. Remove scattered close calls that would double-close after this change. Preserve all existing redirect status/order/limit behavior and read successful bytes before closing.

Catch only `requests.exceptions.RequestException` for expected transport failures, including content-read failures. Preserve `SSLError` classification as `tls`, other Requests exceptions as `connection` according to the existing classifier. For a read failure after receiving a response, retain that received status/final URL and attempted chain, return `ok=False`, and expose no partial body. An initial get failure has no received status/final URL; a later get failure retains the last received provenance as already specified.

Unexpected programming/test exceptions such as AssertionError or RuntimeError must propagate; if a response was already received, its finally cleanup still runs. Do not turn them into successful captures or routine network diagnostics. Keep legacy behavior unchanged in this corrective packet.

Required RED→GREEN tests:

- Content property raises ChunkedEncodingError, both at the initial URL and after one allowed redirect: explicit failed result, correct provenance, no partial bytes, each received response closed once.
- Unexpected RuntimeError during content access: propagates and the response still closes once.
- Unscripted session request: AssertionError propagates rather than becoming `connection`.
- Genuine Requests connection and TLS failures retain their existing classifications and request traces.
- Existing success, HTTP rejection, redirect denial, loop, missing Location and hop-limit paths still close each received response exactly once.

Use a response fake with a real raising property for the read-error test; patching `fetch` or the whole redirect method would not exercise this defect. Keep test fixture errors loud. Do not catch the harness guard exception as an ordinary connection failure.

## Verify, commit and stop

Demonstrate each correction's intended RED failure on the current code before changing production files. The reviewer has already reproduced the defects; do not substitute assertions that merely restate fixture setup. Implement the fixes, then run:

```powershell
python -B tools/run_offline_checks.py --pattern 'test_p02a_*.py'
python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'
git diff --check
```

Preserve all 109 existing maintenance tests and the legacy 60/31/63 plus multiturn checks. Require zero ordinary network attempts and zero unexplained top-level skips. Do not edit previous tests to hide new failures. Use the same audited Python 3.12.10 interpreter.

Write the worker report before committing; stage explicit allowed paths only. Create a new corrective commit, for example:

```text
fix(tracker): P02A-C1 close policy validation and cleanup gaps
```

Then verify the exact final commit, including its report:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'
```

Report base/final SHAs, paths, correction mapping, RED→GREEN evidence, exact no-contact traces, immutable configuration and IPv6 matching evidence, response closure/exception results, cumulative/exported counts and limitations. Do not claim P02A accepted yourself.

End with **“P02A-C1 complete for review; stopped. P02B and P03 have not been started.”**
