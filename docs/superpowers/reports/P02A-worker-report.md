# P02A worker report — explicit navigation policy and bounded HTTP redirects

Packet: P02A (G0, narrowed by dispatch prompt)
Base commit: `41b8eed0db6fdcc2864fe55c50c4dfc4066400e1` (accepted P01) on branch `codex/tracker-g0-p02a`, new isolated worktree created from the accepted SHA. P00/P01 worktrees and the original checkout untouched.
Final local commit SHA: reported in the dispatch response after commit and exported verification.

## Changes and behavior verified

| File | Change | Contract |
|---|---|---|
| `acquisition/navigation_policy.py` (new) | `NavigationPolicy(frozenset[str], max_redirects=5)` with `allows(url) -> bool`: configuration validation (ValueError for bool/negative/noninteger limits; URL strings, credentials, ports, paths, bare/non-leading wildcards, whitespace, invalid hostnames and malformed IPv6 in the host set), case-normalized exact-host matching, explicit `*.domain` proper-subdomain wildcards (apex excluded, nested subdomains included), empty allowlist denies everything, no DNS/network use, invalid input returns False rather than raising; wizard/login exclusion reuses `classify_url(..., skip_boilerplate=False)` with exceptions caught, applied to the original AND the percent-decoded path (`/log%69n` cannot evade); raw whitespace/control characters, backslashes, credentials in the authority, malformed brackets and invalid ports denied before normalization helpers; raw brackets outside the authority denied; PDFs and boilerplate remain navigation-eligible | A |
| `acquisition/transport.py` | `StrictResult` gains `redirect_chain: list[str]` (defaulted, positional compatibility kept); `RequestsTransport.__init__(..., navigation_policy=None, session=None)` — injected session replaces `requests.Session()` with no second session behind it, user agent/timeout/TLS verification preserved; `_fetch_legacy` (policy is None) retains the exact automatic-redirect behavior and error semantics and populates `redirect_chain` from response history when the fake/response provides one; `_fetch_with_policy` (policy supplied) is a visibly separate bounded manual-redirect loop: every get uses `allow_redirects=False, verify=True`, the configured timeout; initial destination validated before the first get; only 301/302/303/307/308 followed; relative Location resolved with `urljoin`; case-insensitive headers; fragments stripped for requests and loop identity (identity equates scheme/host case, default ports, empty path with `/`; query preserved); deterministic evaluation order Location presence → destination policy → loop detection → hop limit → next request; explicit `destination_disallowed` / `redirect_missing_location` / `redirect_loop` / `redirect_limit` / `http_status` outcomes; terminal bytes read before closing; every response closed exactly once on every path; the injected session never closed | B, C |
| `tests/maintenance/fixtures.py` | `scripted_http_session(routes)` added (existing `scripted_session` untouched): fresh response per request, `.urls`/`.call_kwargs`/`.close_counts` recording, unscripted-request failure, scripted exception support for transport-error classification tests; module imports added | tests |
| `tests/maintenance/test_p02a_http_policy.py` | 52 tests across the nine required evidence groups | all |

Not changed: `PlaywrightRenderer` (unchanged behavior), known monitor, robots, shared legacy URL helpers (`normalize_url`, `same_domain`, `allowed_domain`, `classify_url`), P00/P01 tests, harness tools, live state.

## RED → GREEN

Behavioral baseline captured BEFORE implementation (inline probe against the pre-change transport): constructor signature lacked `navigation_policy`/`session`; `StrictResult` lacked `redirect_chain`; a legacy `fetch` issued `session.get(..., allow_redirects=True)` — recorded verbatim in the dispatch response. The first test run then failed on the missing `scripted_http_session`/`NavigationPolicy` APIs, followed by iterative failures (mock session headers, IPv6 configuration, malformed-bracket classification, TLS scripting through the fixture, redirect-limit destination choice, closure counting) — each fixed without weakening assertions. Final: 52/52 P02A checks pass with zero network attempts.

## Key verifications

- Forbidden initial URL: zero session calls, empty chain, `destination_disallowed`.
- Allowed A→B: chain `[A, B]`, `final_url` B; relative and mixed-case Location; all five statuses.
- A→external: external never contacted, chain `[A]`, `destination_disallowed`; same for external PDF and same-host login.
- Loops: self, two-hop, fragment-only, default-port-equivalent — all `redirect_loop` with exact traces.
- Limits: five redirects then success = six calls; sixth redirect = six calls + `redirect_limit`; zero and custom limits exact.
- Closures: exactly one per response on success and all rejection paths; every policy call has `allow_redirects=False, verify=True`.
- Legacy: one automatic-redirect call, chain from history, 404 classification unchanged; independent `StrictResult` chain lists.

## Verification (audited interpreter 3.12.10)

- `python -B tools/run_offline_checks.py --pattern 'test_p02a_*.py'` → exit 0, ran 52, failures 0, errors 0, attempts 0.
- `python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'` → exit 0; legacy [60, 31, 63] + multiturn pass; discovery ran 109 (24 P00 + 33 P01 + 52 P02A), failures 0, errors 0, skips 0, attempts 0. The P00/P01 baselines are unchanged (60/31/63 preserved; the documented nested P00 recursion skip stays inside its grandchild).
- `git diff --check` → exit 0 (after removing a trailing blank line the check flagged).
- Exported: `python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'` → exit 0 on the final commit.

## Explicit statements

- Existing callers are NOT wired to the policy yet: `RequestsTransport()` callers without arguments keep legacy behavior, and the known monitor/acquisition paths do not construct a policy — that integration is P02B.
- Browser navigation and unattended robots protection are NOT implemented by this packet (P03).
- P00/P01 acceptance is not re-asserted by P02A; R2–R5/R7/R9 remain out of scope.

STOPPED; next packet not started.
