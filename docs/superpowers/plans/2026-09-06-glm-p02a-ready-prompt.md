# Ready-to-paste GLM prompt — P02A only

Copy the text below into GLM. P02 is now split into P02A (policy and HTTP transport) and P02B (caller integration). Both require separate Codex acceptance. P02B and P03 are not released by this prompt.

---

You are GLM 5.3 Flash, implementation worker for Govt. Service Navigator. Codex owns architecture, contracts, review and acceptance. Implement only **P02A — explicit navigation policy and bounded HTTP redirects**. Test, commit locally, report, and stop.

## Read and verify the starting state

Read these documents from the original project at `C:/Users/Monirul/Documents/Structured Data/NID/gov-service-navigator/gov-service-navigator`:

1. `docs/superpowers/plans/2026-09-05-glm-implementation-control.md`
2. `docs/superpowers/plans/2026-09-05-glm-g0-baseline-and-sensors.md` — P02, as narrowed by this prompt.
3. `docs/superpowers/specs/2026-09-05-freshness-reconciliation.md`
4. `docs/superpowers/specs/2026-09-05-tracker-evidence-update-design.md`
5. `docs/superpowers/reports/2026-09-06-P01-acceptance.md`

Use executing-plans if available, executing only this packet. Do not delegate architecture or spawn workers. No production crawling, corpus/vector writes, dependency installation, push or merge.

Accepted predecessor: **`41b8eed0db6fdcc2864fe55c50c4dfc4066400e1`**, on `codex/tracker-g0-p01`, at:
`C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p01-1788642519219644900/worktree`.

Verify that exact HEAD and a clean tracked tree. Preserve its untracked planning documents. Create a new isolated worktree from that SHA on **`codex/tracker-g0-p02a`**, under a unique `gsn-glm-p02a-<uuid>` temporary directory. Check every command's exit code. If the branch already exists or the starting state differs, report it; do not reset, delete or reuse unexpected work. Preserve both accepted worktrees and the original checkout, including its three acquisition edits and user bundles. Read plans directly from the original project or copy only needed documents unstaged.

Use the audited Python 3.12.10 interpreter. Do not copy the reviewer's sandbox-only tempfile workaround into the repository.

## Allowed changes

- Create `acquisition/navigation_policy.py`.
- Modify only `StrictResult` and `RequestsTransport` in `acquisition/transport.py`, plus imports needed by those changes. Leave `PlaywrightRenderer` and its behavior unchanged.
- Create `tests/maintenance/test_p02a_http_policy.py`.
- Add reusable scripted HTTP session/response fixtures to `tests/maintenance/fixtures.py`, without changing existing fixture behavior.
- Create `docs/superpowers/reports/P02A-worker-report.md`.

Do not edit acquisition callers, known monitor, robots, shared legacy URL helpers, P00/P01 tests, harness tools, live state, routing or embedding. Caller integration is deliberately P02B. Return any necessary wider scope to Codex before proceeding.

## Fixed contracts

### A. Pure navigation policy

Provide an immutable configuration:

```python
NavigationPolicy(allowed_hosts: frozenset[str], max_redirects: int = 5)
policy.allows(url: str) -> bool
```

An empty host set denies every URL. `max_redirects` is a nonnegative integer (reject bool, negative and noninteger values with ValueError at construction). Host configuration contains hostnames or IP literals, never URL strings, credentials, ports, paths or a bare wildcard. Reject malformed configuration with ValueError; normalize host case consistently. Do not fetch DNS or any network resource to decide policy.

Matching rules:

- `gov.example` allows exactly that host, not `www.gov.example` or `gov.example.evil`.
- `*.gov.example` explicitly allows proper subdomains, including nested subdomains, but not the apex. Configure the apex separately when needed.
- Ports are not an additional allowlist dimension in this contract. URLs with absent ports or valid ports 1–65535 can match the host. Malformed/out-of-range/zero ports are denied.
- Require an absolute HTTP(S) URL, a hostname and syntactically valid authority. Reject credentials, raw whitespace/control characters, backslashes, malformed brackets and invalid ports before calling helpers that may normalize them away. Invalid input returns False rather than raising from `allows`.
- Reuse `acquisition.urls.classify_url(..., skip_boilerplate=False)` for wizard/login exclusion; catch its malformed-URL exceptions. Informational pages, PDF URLs and boilerplate are navigation-eligible when otherwise allowed; content selection remains separate.
- Check the percent-decoded path for the same wizard/login patterns as well, so `/log%69n` cannot avoid classification. Use a private validation representation, preserving the original request path/query. Do not change `normalize_url`, `same_domain`, `allowed_domain` or `classify_url` globally.

Do not silently strip credentials or transform an invalid URL into an allowed one. Keep URL validation in this module so P03 can reuse it.

### B. Backward-compatible HTTP port

Extend the existing constructor:

```python
RequestsTransport(user_agent=DEFAULT_UA, timeout=20.0,
                  navigation_policy=None, session=None)
```

Preserve the positional meaning of the existing two arguments. An injected session replaces `requests.Session()`; do not create another session behind it. Keep the honest user agent, timeout and TLS verification. The session supports the Requests `get` interface, mutable headers, and response `status_code`, `headers`, `content`, `url`, `close()`.

Append this defaulted field to `StrictResult`, preserving existing fields and positional compatibility:

```python
redirect_chain: list[str] = field(default_factory=list)
```

For the policy-enforced path, `redirect_chain` is the ordered list of URLs actually submitted to `session.get`, including the initial URL. A denied initial URL gives an empty chain. A denied redirect target is never appended or contacted. The chain may include a final attempted request that failed at the transport layer. `final_url` is the last received response URL, or empty when no response was received; it must never claim a rejected target was contacted. Preserve received status and existing error detail where applicable. Failed results must not expose partial bytes as successful evidence.

When `navigation_policy is None`, retain legacy automatic-redirect behavior and existing result/error semantics. Document this explicitly; do not claim legacy callers now have pre-request protection. Populate the new chain from response history when available, without breaking existing simple injected response fakes. The policy-enforced and legacy branches must be visibly separate. P02B will wire the real monitored/acquisition paths; P05 will require tracker policy construction.

### C. Manual redirects when policy is supplied

Validate the initial destination before the first `get`. Every `get` in this branch uses `allow_redirects=False`, `verify=True`, and the configured timeout. Never retry through the legacy branch or another session after a failure.

Follow only 301, 302, 303, 307 and 308. Resolve a relative Location against the current response URL using standard URL joining. Treat headers case-insensitively. Validate the resolved destination before contacting it. Strip fragments for the actual HTTP request and redirect-loop identity; do not reorder query parameters or lowercase paths. Loop identity should equate scheme/host case, default ports and empty path with `/`.

The default limit permits **five followed redirects**, meaning at most six requests including the initial request. A redirect returned by the sixth request must not lead to a seventh request. Test zero and a smaller custom limit too.

Return explicit failed `StrictResult` outcomes:

| Condition | error_kind |
|---|---|
| Initial or redirect destination fails policy, including malformed Location destination | `destination_disallowed` |
| Redirect response has absent/blank Location | `redirect_missing_location` |
| Destination repeats a previously requested URL | `redirect_loop` |
| Following another redirect would exceed the configured bound | `redirect_limit` |
| Other 3xx status in the policy branch | `http_status` |
| HTTP status >=400 | Existing `http_status` |
| TLS failure / connection or other Requests exception | Existing `tls` / `connection` classification |

For a redirect, evaluate in this order: Location presence, resolved destination policy, loop detection, hop limit, then next request. This makes diagnostics deterministic. Keep certificate failures distinguishable and never disable TLS checks.

Read terminal response bytes before closing it. Close every received response exactly once on success, HTTP rejection, redirect transition, policy rejection, loop and limit paths. Do not close the injected session after each fetch; its owner may reuse it. Do not catch unrelated programmer errors as a successful capture.

## Required tests and evidence

Use real `NavigationPolicy` and real `RequestsTransport` with a scripted fake session. Record each requested URL and kwargs; fail loudly on unscripted requests. Track response close counts. The session boundary is mocked, not the production redirect loop or policy decision. Ordinary tests must make zero network attempts.

1. Policy table: exact host, explicit subdomains and apex, suffix impostor, mixed case, empty allowlist, invalid configuration, HTTP/HTTPS, invalid scheme/authority, credential URL, invalid port, raw whitespace/backslash, malformed bracket, wizard/login path, encoded login path, ordinary PDF, boilerplate.
2. Forbidden initial URL produces no calls and `destination_disallowed`.
3. Allowed A→B succeeds; relative redirect and mixed-case Location header work. Exercise all five supported redirect statuses.
4. A→external→A stops after A; external is not contacted. Repeat for a PDF destination and a same-host login destination. Assert exact call trace, error kind and provenance.
5. Missing/blank Location; malformed Location destination; A→A and A→B→A loops; fragment-only self-loop; default-port-equivalent loop.
6. Five redirects then success: six calls. Sixth redirect: still six calls, `redirect_limit`. Zero/custom limits have exact boundary assertions.
7. HTTP 4xx/5xx and unsupported 3xx, TLS exception and connection exception at the initial URL and after an allowed redirect preserve classifications. No hidden retry or unsafe fallback.
8. Every received response closes once in successful and rejected flows. All policy-mode calls explicitly disable automatic redirects and enable certificate verification.
9. Policy-less legacy successful fetch and error behavior remain compatible. Independent `StrictResult` instances do not share chain lists.

Show RED→GREEN honestly. Before implementing the new API, capture at least one behavioral baseline showing the current transport requests automatic redirects (`allow_redirects=True`); missing imports alone are not evidence of the original flaw. After implementation, the decisive assertion is the exact request trace excluding forbidden destinations, not merely a nonzero return or `ok=False`.

Run the targeted and cumulative checks:

```powershell
python -B tools/run_offline_checks.py --pattern 'test_p02a_*.py'
python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'
git diff --check
```

Do not edit old tests or their counts to make a regression pass. Require zero ordinary network attempts, no unexplained top-level skips and all accepted P00/P01 tests passing. Preserve their 57-test baseline; report the added tests separately. The documented nested P00 recursion skip remains acceptable.

Write the worker report before committing. Stage only allowed changed files, then create a local commit:

```text
feat(tracker): P02A enforce configured HTTP redirect policy
```

Verify the exact committed tree:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'
```

Use new corrective commits if necessary; do not amend accepted history. Report base/final SHA, branch/worktree, changed files, RED→GREEN observations, exact redirect request traces, resource closure evidence, targeted/cumulative/exported results and limitations.

State explicitly that existing callers are not wired yet and browser/robots protection is not implemented by this packet. P02B will address policy binding and terminal handling of policy failures in acquisition and known monitoring; Codex will release its exact integration contract after reviewing P02A.

End with **“P02A complete for review; stopped. P02B and P03 have not been started.”** Codex decides acceptance.
