# Ready-to-paste GLM prompt — P02B only

You are GLM 5.3 Flash, implementation worker. Codex owns architecture and acceptance. Implement **P02B — bind HTTP policies in acquisition and known monitoring, with terminal policy failures**, then test, commit locally and stop. P03 is not released.

## Starting state and governing documents

Accepted P02A final SHA: **`ac2930a477078f500010ab31b05ed50875339fce`**. Verify this HEAD and a clean tracked tree in:
`C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p02a-1788646696036966800/worktree`.

Create a new isolated temporary worktree from that exact SHA, branch `codex/tracker-g0-p02b`. Stop on unexpected state or an existing branch; never reset/delete user work. Preserve original checkout, all accepted worktrees and untracked documents.

Read from the original project at `C:/Users/Monirul/Documents/Structured Data/NID/gov-service-navigator/gov-service-navigator`:

- `docs/superpowers/plans/2026-09-05-glm-implementation-control.md`
- `docs/superpowers/plans/2026-09-05-glm-g0-baseline-and-sensors.md` — P02.
- Both governing specs in `docs/superpowers/specs/`.
- `docs/superpowers/reports/2026-09-06-P02A-acceptance.md`.
- This prompt, which fixes the caller integration decisions below.

Use executing-plans if available, only for this packet. No agents, dependency installation, live crawl, corpus/vector writes, push, merge or scheduler activation. Use the audited Python 3.12.10 environment.

## Allowed files

- `acquisition/transport.py`: add nonmutating policy binding and its small HTTP-only helper(s); preserve browser code and accepted redirect loop behavior.
- `acquisition/navigation_policy.py`: only an immutable intersection wrapper/helper if needed for policy binding; do not rewrite accepted URL rules.
- `acquisition/acquire.py`: bind the HTTP adapter and terminate on policy failures.
- `freshness/known_monitor.py`: bind per-source HTTP adapters and preserve HTTP policy failure reasons.
- New `tests/maintenance/test_p02b_http_integration.py`.
- `tests/maintenance/fixtures.py`: narrowly necessary integration fixtures, preserving existing ones.
- New `docs/superpowers/reports/P02B-worker-report.md`.

No P00/P01/P02A test edits, legacy URL/robots changes, renderer changes, corpus/schema changes or CLI redesign. If accepted tests reveal a necessary broader scope, report the specific conflict to Codex rather than weakening assertions.

## Fixed architecture

### 1. Bind an immutable policy without mutating a shared transport

Add `RequestsTransport.with_navigation_policy(policy)` returning a distinct transport object that shares the existing session and preserves timeout/user-agent configuration. It must not allocate a second session, reconfigure shared session headers, modify the original transport, or perform I/O. Keep the existing constructor and `fetch(url)` interface compatible.

If the original transport already has a policy, the bound transport must enforce the **intersection**: every destination must satisfy both policies and the redirect limit is the smaller limit. Do not replace a narrower configured policy with a wider caller policy. Use a private immutable composed policy with `allows(url)` and `max_redirects`, or equivalent small immutable composition; do not merge host strings, which would accidentally form a union. Preserve the original policy objects unchanged.

This copy shares a session for ordinary sequential reuse; do not introduce concurrency or claim Requests sessions are thread-safe. Separate bound copies for sources A and B must not change each other's policies.

Caller binding is explicitly for the project's concrete `RequestsTransport`: use `isinstance(transport, RequestsTransport)` and call the new binding method. Existing directly injected non-Requests fake ports retain `fetch(url)` compatibility and remain subject to caller post-capture checks. Do not add TypeError retry fallbacks, dynamic method guessing, or silently retry an unbound real transport. This compatibility branch is not evidence that arbitrary third-party transports enforce policy; P05 must require the configured supported adapter for new tracker construction.

### 2. Acquisition derives policy from its existing explicit context

In `acquire_url`:

- `allowed_domains is not None`: construct NavigationPolicy from those exact entries. An empty set denies all. Preserve explicit `*.` semantics; do not expand plain entries into implicit subdomains.
- `allowed_domains is None`: derive a singleton exact hostname from the original requested URL. Do not use an empty/missing-policy bypass.
- Check the original URL before normalization can strip credentials or malformed syntax. Malformed input becomes an ordinary failed/invalid Capture with a reason and no transport/renderer/artifact activity. Preserve existing wizard disposition where applicable. Invalid explicit configuration is a ValueError; do not reinterpret it as an unrestricted policy.
- Bind the real HTTP transport once for this invocation and use it for the strict fetch. Retain original/requested provenance.

Define the terminal HTTP navigation error set once: `destination_disallowed`, `redirect_missing_location`, `redirect_loop`, `redirect_limit`. Immediately return a failed, unverified Capture for any of these. Use existing `redirected_out_of_allowlist` disposition for `destination_disallowed` and existing failed disposition for the other three. Include exact error kind/detail in notes and preserve received status/final_url. No browser goto, TLS-evidence fallback, optional screenshot, alternate HTTP request or content artifact may follow such failure.

Successful HTTP results still require post-capture final-URL checking, including directly injected fakes. Use the same invocation policy for successful HTTP PDF and HTML final URLs, with empty final_url compatibility falling back to requested URL. Check before content parsing, artifact writes or browser fallback for a short HTTP page. An invalid/disallowed final URL is terminal even if the injected result says ok=True. Do not silently normalize an invalid final URL into the requested URL. Existing browser post-capture checks remain; pre-contact browser enforcement belongs to P03.

This packet does not change legacy PDF structural acceptance in acquire_url or implement unrelated audit defects. P01's known-monitor PDF validator remains intact.

### 3. Known monitor binds separately for each source

Keep browser-first HTML acquisition and PDF HTTP acquisition order. For each source, construct a NavigationPolicy for its exact requested hostname and bind the real HTTP adapter using the method above. Reuse that source-bound adapter for its PDF fetch or static HTTP fallback. Never mutate `self.transport.navigation_policy` while iterating sources.

Validate an initial source destination before its acquisition activity; reject a disallowed/invalid source as a diagnostic failure and continue to other sources. Do not convert an explicit narrower transport policy into a broader per-source policy.

For PDF and static HTTP fallback, preserve each terminal HTTP navigation error's kind/detail and received final URL in the returned failure reason. A renderer's earlier generic failure must not overwrite an HTTP `destination_disallowed` outcome. After that HTTP failure, do not invoke the renderer again or retry unbound HTTP. Existing failure handling may change diagnostics only: trusted/candidate hashes, paths, bytes, and pending content-change events remain preserved. Dry mode must write nothing.

Preserve P01's post-capture malformed-final-URL and PDF validation regressions for injected fakes. Use the exact source HTTP policy for successful HTTP results before they can become evidence; retain existing browser post-capture checks. Do not remove P01 guards on the premise that real adapters now prevent redirects.

The monitor's initial browser attempt and existing robots fetches are outside this packet's pre-contact guarantee. Do not reorder/remove them to make tests look safer. P03 will enforce those ports. Tests use injected robots fixtures and renderers and must distinguish an allowed initial browser attempt from an impermissible retry after HTTP denial.

## Required behavioral tests

Use real RequestsTransport with the scripted session and real `acquire_url` / `KnownSourceMonitor.run`. Never substitute a fake that merely returns the desired policy error for the main integration proof. All stores, corpora, events and artifacts live under temporary test roots.

1. Binding returns a distinct object sharing the same session, preserves timeout and headers, performs no calls/session construction, leaves original policy unchanged and intersects restrictions and hop limits. Two independently bound source adapters cannot widen each other.
2. Acquisition A→external→A returns terminal unverified failure; only A appears in session trace, renderer has zero calls, output directory contains no capture artifacts. Repeat for PDF, login, loop, missing Location and limit results. Include `also_screenshot=True` on a denied case.
3. Exact host denies unconfigured subdomains; explicit wildcard allows a proper subdomain; empty allowlist denies before HTTP/browser activity. With no explicit allowlist, per-request exact host applies. Malformed original URL and credentials are rejected before normalization hides them.
4. Allowed same-host HTML and real valid PDF succeed through the actual adapter. A transport constructor policy narrower than caller context still denies; there is no retry through its original unbound instance.
5. Fake ok=True HTTP results with invalid/external final URLs cannot write artifacts or trigger browser fallback. Cover both long and short HTML, plus PDF; preserve empty-final-url compatibility.
6. Monitor PDF redirect to external is terminal, requests only the source URL and does not create trusted/candidate evidence. Establish old trusted/candidate state first and freeze actual snapshot bytes/paths/hashes and complete event bytes for preservation assertions.
7. Monitor HTML browser fails, strict fallback redirects off-policy: only the initial renderer call occurs, HTTP requests only A, failure reason retains the HTTP error instead of the generic renderer error. Dry run preserves all persisted bytes.
8. Two sources on different hosts in one monitor run use separate host restrictions; a failed source does not prevent the valid one from being processed. The shared original transport remains unchanged.
9. Ordinary unrelated HTTP/TLS fallback behavior and all 141 accepted maintenance tests remain passing. Tests do not claim browser/robots pre-contact protection.

Show genuine RED failures on the accepted code before implementation: real caller auto-redirect kwargs / attempted external destination or renderer fallback after an HTTP policy denial. Missing-method errors alone are not sufficient. Assert exact request traces and persisted bytes, not just summary counts.

## Verification and stop

Run:

```powershell
python -B tools/run_offline_checks.py --pattern 'test_p02b_*.py'
python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'
git diff --check
```

Require zero ordinary network attempts, zero unexplained top-level skips and unchanged legacy 60/31/63 plus multiturn results. Report actual discovered counts, not a stale subset. Write the worker report before staging; stage explicit allowed files only and verify the staged diff includes every implementation change.

Commit locally with `fix(tracker): P02B bind HTTP policy at acquisition boundaries`, then run:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'
```

Verify that exact final SHA, including the report. Make new corrective commits if needed; no amends to accepted history. Report all SHAs, branch/worktree, scoped file changes, request traces, no-fallback evidence, state preservation, targeted/cumulative/export results and the remaining P03 limitation.

End with **“P02B complete for review; stopped. P03 has not been started.”** Codex decides whether P02 is accepted.
