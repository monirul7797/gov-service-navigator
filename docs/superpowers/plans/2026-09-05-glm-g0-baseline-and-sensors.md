# G0 Baseline and Sensor Repair Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` if available. Execute only the released packet, commit locally, report and stop. Do not dispatch subagents.

**Goal:** Establish reproducible offline verification and repair the acquisition paths the tracker will retain.

**Architecture:** Preserve existing local changes, separate comparison from persistence, and enforce destination policy before navigation plus a trust check before accepting content. New unattended robots semantics are explicit; legacy semantics stay covered.

**Tech stack:** Existing Python 3.12, unittest, requests, Playwright and existing freshness modules.

**Spec:** [Design](../specs/2026-09-05-tracker-evidence-update-design.md), [audit](../specs/2026-09-05-freshness-reconciliation.md), [control guide](2026-09-05-glm-implementation-control.md).

## Global constraints

All control-guide constraints apply. G0 adds no maintenance database, alerts, OCR or publisher. No live govt calls. R2-R5 and R7/R9 remain known legacy defects; do not repair them speculatively in this phase. G0 acceptance is not acceptance of legacy Freshness V2.1.

## P00 — Reproducible baseline and offline test harness

**Allowed files:** carry existing changes in `acquisition/acquire.py`, `acquisition/crawler.py`, `acquisition/transport.py`; modify `test_freshness.py`, `test_acquisition.py`, `test_freshness_v21.py` only for fixture isolation/contract tests; create `tools/run_offline_checks.py`, `tools/verify_candidate.py`, `tests/__init__.py`, `tests/maintenance/__init__.py`, `tests/maintenance/fixtures.py`, `tests/maintenance/test_p00_baseline.py`, `docs/superpowers/reports/P00-worker-report.md`. `test_multiturn.py` runs unchanged.

**Consumes:** actual working diff against audited HEAD `8d3c83cfe35ac4f5ba7667856b8562c300aeeaee`; current `StrictResult`, `RenderResult`, `Capture.to_provenance`, `KnownSourceMonitor.run`.

**Produces:** the two runner commands defined in the control guide; reproducible existing final_url/provenance behavior; genuinely injected robots fixtures. No new production behavior beyond deliberately carrying/reconciling the three existing edits.

- [ ] **1. Inventory and isolate the starting state.** Confirm branch, HEAD, diff and untracked names. Expected three tracked modifications: acquire.py, crawler.py, transport.py; expected collectively 63 insertions/5 deletions. A different diff must be reported and reconciled by Codex before carrying it. Do not discard additional work.

```powershell
$repo = (Get-Location).Path
git status --short
git rev-parse HEAD
git diff --stat
$scratch = Join-Path $env:TEMP ('gsn-glm-p00-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$patch = Join-Path $scratch 'existing-acquisition.patch'
git diff --binary --output=$patch -- acquisition/acquire.py acquisition/crawler.py acquisition/transport.py
$worktree = Join-Path $scratch 'worktree'
git worktree add -b codex/tracker-g0-p00 $worktree HEAD
git -C $worktree apply --check $patch
git -C $worktree apply $patch
```

Check each exit code; stop on failure. If Git reports dubious ownership, use a command-local `-c safe.directory=<resolved repo>` for the named repository only, not global configuration. Use the existing project Python interpreter (`C:/Users/Monirul/AppData/Local/Programs/Python/Python312/python.exe` was used in the audit); report a missing interpreter rather than installing a replacement silently.

Plans/specs are currently untracked in the source checkout. Copy only `docs/superpowers/specs` and `docs/superpowers/plans` into the worktree for reading, preserving their relative paths. Do not copy review bundles wholesale. Before copying, verify the destination is inside the new worktree. Keep these planning documents unstaged unless Codex explicitly asks for a separate documentation commit. They are not needed by runtime tests in the candidate archive.

- [ ] **2. Add the guard regression first.** Runner supports `--probe-network` as an internal test mode that deliberately calls `socket.create_connection(('gov.example',443))`. It must exit nonzero before network access and print an attempted-network diagnostic. Add an assertion using `subprocess.run` that checks return code and diagnostic. This proves the guard cannot silently convert attempts into successful offline tests.

```python
def test_network_probe_fails_closed(self):
    import subprocess, sys
    from pathlib import Path
    runner = Path(__file__).resolve().parents[2] / 'tools' / 'run_offline_checks.py'
    result = subprocess.run([sys.executable, '-B', str(runner), '--probe-network'],
                            capture_output=True, text=True)
    self.assertNotEqual(result.returncode, 0)
    self.assertIn('NETWORK_ATTEMPT', result.stdout + result.stderr)
```

- [ ] **3. Implement the runner and expose existing network dependence.** Use `runpy.run_path(..., run_name='__main__')` for each legacy script in a separate child process/isolated interpreter context with the same guard installed. Preserve each script's SystemExit result. Guard before importing tested modules. Unittest discovery reports skipped counts. A test mode deliberately using network is never part of a passing normal execution.

- [ ] **4. Repair fixtures, not production robots policy.** `test_freshness.make_monitor` currently defaults to `RobotsCache()` through production; preseed allow parsers for fixture corpus hosts only when no explicit robots cache was supplied. `test_acquisition.allow_all_robots()` stores a `'*'` sentinel that real lookup does not use; give every fixture its actual hosts. Direct monitor/crawler constructions in V2.1 tests also require injected caches. Keep explicit disallow/unavailable cases intact. Do not replace `robots_check` globally with “allow.”

```python
def robots_for_hosts(hosts):
    from acquisition.robots import RobotsCache
    from urllib.robotparser import RobotFileParser
    cache = RobotsCache()
    for host in hosts:
        parser = RobotFileParser()
        parser.allow_all = True
        cache.parsers[host] = parser
    return cache
```

- [ ] **5. Test the carried transport contract through producers and consumers.** Mock `RequestsTransport.session.get` with a response containing url/status/headers/content and assert `.fetch()` returns the observed final_url. Test RenderResult/StrictResult defaults and `Capture.to_provenance` fields. Run a real `KnownSourceMonitor.run` against temp fixtures using those result types. Add an acquire/crawler regression showing that the existing local allowed-domain argument reaches capture and rejects an external final URL. This does not yet prove pre-request redirect blocking.

```python
def test_http_result_preserves_observed_destination(self):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from acquisition.transport import RequestsTransport
    t = RequestsTransport()
    t.session = Mock()
    t.session.get.return_value = SimpleNamespace(status_code=200,
        headers={'content-type':'text/html'}, content=b'page',
        url='https://gov.example/final')
    self.assertEqual(t.fetch('https://gov.example/start').final_url,
                     'https://gov.example/final')
```

- [ ] **6. Implement candidate export verification, run tests, commit and stop.** Use the control-guide algorithm. Run targeted P00 and the four legacy suites with actual network blocked. Existing audit counts were 60/31/63/40 under qualified conditions; compare any count change, do not hardcode counts as pass criteria. Require no attempted network and no removed assertions. Stage explicit allowed paths and report preserved local edits separately from new changes.

```powershell
python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'
git diff --check
# Stage only P00's explicit changed files, then:
git commit -m 'test(tracker): P00 reconcile acquisition baseline'
python -B tools/verify_candidate.py --legacy --pattern 'test_p00_*.py'
```

**Reviewer gate:** exported HEAD has the final_url contract and production monitor imports; network attempts fail the harness; no production robots-policy change; original checkout remains untouched. P00 alone does not fix R6/R8 or validate the entire acquisition layer.

## P01 — Dry-run fidelity and PDF trust boundary

**Allowed files:** `freshness/known_monitor.py`, new `acquisition/document_validation.py`, `tests/maintenance/test_p01_monitor.py`, fixtures, packet report. Adjust malformed PDF fixtures in the three legacy suites only when their intended successful-PDF behavior needs valid bytes; retain negative cases.

**Consumes:** P00 result contracts and `make_monitor`, `fixture_corpus`, `rec`, `state_bytes` in `test_freshness.py`.

**Produces:** `validate_pdf(body: bytes, content_type: str) -> tuple[bool,str]`; same dry/live comparison classifications; PDF final-host checks before trusted/candidate state writes. Validation requires a PDF signature and successful structural parse using the existing PDF parser; HTML/error bodies with PDF extension are rejected. A conflicting HTML content type is rejected even if the URL ends in PDF; octet-stream is allowed only after structural validation.

- [ ] **1. Add a failing dry-run regression with real persisted baseline.** Create 1,000 readable characters, run baseline, snapshot all state bytes, then feed a 100-character collapse. Compare dry and real runs from copies of the same starting state. No temporary snapshot path may escape the supplied root.

```python
# Within the test, tmp is a TemporaryDirectory Path; explicit robots are injected.
corpus = fixture_corpus(tmp, [rec('r1', PAGE_A)])
make_monitor(tmp, FakeTransport(), FakeRenderer({PAGE_A: html('A'*1000)}),
             {'nid':corpus}, robots=robots_for_hosts({'gov.example'})).run()
before = state_bytes(tmp)
dry = make_monitor(tmp, FakeTransport(), FakeRenderer({PAGE_A: html('B'*100)}),
                   {'nid':corpus}, dry_run=True,
                   robots=robots_for_hosts({'gov.example'})).run()
self.assertEqual(dry['suspect_captures'], 1)
self.assertEqual(before, state_bytes(tmp))
```

- [ ] **2. Add PDF production-path regressions.** Supply a small valid fixture PDF with final_url on `external.example`; assert no trusted hash, no candidate and no evidence artifact. Repeat with HTML bytes at an allowed `.pdf` URL, a malformed PDF, and a valid same-host PDF. For an existing baseline, assert its hash/bytes survive rejected captures.
- [ ] **3. Repair monitor logic.** Read trusted snapshot during dry run as well as real run. Apply final-domain policy to all verified content methods before hashing/acceptance. Call PDF validation before accepting bytes. Keep empty final_url compatibility for old injected result fakes as requested URL; real transport must report observed final_url. P02/P03 close pre-request enforcement.
- [ ] **4. Run P01, cumulative offline suite, explicit diff/commit and candidate export.** Command: `python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'`; after commit run `python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'`.

**Reviewer gate:** A14/A15; R6/R8 trust acceptance corrected, with dry-run state-byte equality. Do not claim redirect contacts are prevented until P02/P03.

## P02 — HTTP redirect policy before requests

**Dispatch refinement (2026-09-06):** execute this packet in two separately reviewed parts. [P02A](2026-09-06-glm-p02a-ready-prompt.md) releases the policy/transport contract and tests only. P02B will release acquisition/known-monitor wiring after P02A acceptance. The narrower P02A prompt governs its file scope and exact result semantics. This split does not remove any P02 acceptance requirement or release P03 early.

**Allowed files:** `acquisition/transport.py`, new `acquisition/navigation_policy.py`, `acquisition/acquire.py`, `freshness/known_monitor.py`, `tests/maintenance/test_p02_http_policy.py`, fixtures, report.

**Produces:** `NavigationPolicy(allowed_hosts: frozenset[str], max_redirects: int=5)` with `allows(url: str) -> bool`; host match is exact unless an explicitly configured leading `*.` allows subdomains. Reject invalid URLs, credentials in URLs, non-HTTP(S) and blocked wizard/login paths using existing classification. Do not broaden legacy same-domain rules globally. `RequestsTransport(..., navigation_policy=None, session=None)` retains old callers; all new tracker callers supply policy. Add `redirect_chain: list[str]` to StrictResult and keep final_url. Policy-less legacy behavior must be clearly distinguished from the new monitored path.

- [ ] **1. Write a scripted-session test asserting forbidden destination is never requested.** Add `scripted_session` fixture returning mock responses for `(status, headers, bytes)` keyed by URL, recording calls. It raises on any unscripted request.

```python
def test_redirect_is_rejected_before_contact(self):
    from acquisition.navigation_policy import NavigationPolicy
    from acquisition.transport import RequestsTransport
    from tests.maintenance.fixtures import scripted_session
    s = scripted_session({'https://gov.example/a':
        (302, {'location':'https://external.example/b'}, b'')})
    result = RequestsTransport(session=s,
        navigation_policy=NavigationPolicy(frozenset({'gov.example'}))).fetch('https://gov.example/a')
    self.assertFalse(result.ok)
    self.assertEqual(s.urls, ['https://gov.example/a'])
    self.assertEqual(result.error_kind, 'destination_disallowed')
```

- [ ] **2. Implement a bounded manual redirect loop.** Use `allow_redirects=False`, resolve relative Location against current URL, validate each next URL before `.get`, close each response, cap five followed redirects, track loops, preserve HTTP/TLS errors. Never fall back to uncontrolled requests after a policy failure. Test allowed A→B, disallowed A→external→A, relative redirect, loop, missing Location and sixth redirect.
- [ ] **3. Wire acquisition/known-monitor policies.** Thread policy from explicit allowlists and per-source host context to real HTTP adapters. A directly injected fake may still return a forbidden final_url; P01's post-capture guard must reject it. Caller tests assert both layers exist. Missing tracker policy will be a construction error at P05.
- [ ] **4. Run targeted/cumulative offline and candidate-export checks; commit and stop.** Use the fixed runner commands; inspect attempted URLs, not only returned status.

**Reviewer gate:** no off-policy HTTP request through the configured production path, including PDFs. Existing source provenance survives error results.

## P03 — Browser navigation and unattended robots

**Allowed files:** `acquisition/transport.py`, `acquisition/robots.py`, `acquisition/navigation_policy.py`, `tests/maintenance/test_p03_browser_robots.py`, `tests/integration/test_browser_navigation_policy.py`, fixtures, report.

**Produces:** `PlaywrightRenderer(..., navigation_policy=None)`; configured policy enforced for document navigation/redirects in all pages/frames before contact, including clicks and script-driven navigation. Context route interception is installed before creating pages; service workers are blocked in policy-enforced contexts. Do not assume final_url inspection prevents a prior request. Third-party assets are not evidence authorities; document/download destinations require approved hosts. Unsupported interception/download paths return an explicit blocked/manual outcome.

Add `robots_decision(cache, url, user_agent, *, fetcher, unattended: bool) -> dict` returning `decision` (`allow|deny|defer`) and `reason`. The injected fetcher returns status/body/final URL. P02 policy covers robots redirects too. Legacy `robots_check` contract stays intact; the tracker uses only this explicit decision API. Interpret 2xx rules, 404 absence, 401/403/429 access limits, 5xx/TLS/network/parse failures explicitly; unattended failures/access limits defer. Do not silently convert parse errors to allow.

- [ ] **1. Add policy callback tests.** Fake route/request objects record continue/abort; external navigation aborts, allowed navigation continues. Simulate late script navigation, popup document, iframe and TLS-evidence context. Callback exception aborts and records failure; it must not continue.
- [ ] **2. Add robots decision tests with injected fetcher.**

```python
def test_unattended_robots_failure_defers(self):
    from acquisition.robots import RobotsCache, robots_decision
    def unavailable(url):
        return {'status':503, 'body':'', 'final_url':url}
    result = robots_decision(RobotsCache(), 'https://gov.example/a',
                             'FreshnessChecker', fetcher=unavailable, unattended=True)
    self.assertEqual(result['decision'], 'defer')
```

- [ ] **3. Implement configured-context interception and robots API.** Keep legacy semantics covered by P00 tests. Preserve TLS failure classification instead of overwriting certificate errors with generic `net::ERR` blocked status. Count redirect hops per document chain, not every image request. Share NavigationPolicy semantics rather than copying URL tests.
- [ ] **4. Run deterministic checks, then local-browser integration.** `python -B -m unittest discover -s tests/integration -p 'test_browser_navigation_policy.py' -v` launches an already installed Chromium against loopback fixtures only. Bind separate fixture hosts/ports, use request counters and assert forbidden destination contact count zero. Test HTTP redirect and JavaScript navigation in a real browser. If Chromium is missing, report this gate incomplete; do not substitute fake-only acceptance or enable browser sources.
- [ ] **5. Commit, export, rerun offline checks and the browser integration against the export; stop for G0 review.**

**G0 acceptance:** P00-P03 accepted; no regression in the four-service deterministic suite; A14/A15 satisfied; R1 reproducible; R6 and retained R8 paths repaired. Legacy six other bugs remain explicitly outside enabled tracker paths.
