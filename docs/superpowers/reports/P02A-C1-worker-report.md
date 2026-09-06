# P02A-C1 worker report — close policy validation and cleanup gaps

Packet: P02A-C1 (corrections to P02A)
Base commit: `51a3afeca9e12b1b202b383a6f178c1f6e0d08d5` on `codex/tracker-g0-p02a`, same worktree; untracked docs preserved; P02A not amended.
Final local commit SHA: reported in the dispatch response after commit and exported verification.

## Changed files

| File | Change | Correction |
|---|---|---|
| `acquisition/navigation_policy.py` | Frozen dataclass (`@dataclass(frozen=True)`) with controlled `__post_init__` normalization — ordinary assignment/deletion of any attribute raises; ipaddress-based IP literal validation and canonicalization (bracketed or unbracketed IPv6 accepted, stored canonical bracket-free; equivalent compressed/expanded forms match; scoped `%`-literals rejected; wildcard-over-IP rejected); DNS hostname label validation on URL hosts before wildcard suffix matching (`bad..gov.example` denied); colon/credentials/path checks applied only AFTER IP literal recognition so unbracketed IPv6 works; bracketed IPv6 handled before generic colon rejection; trailing-dot DNS normalization on both config and URL sides; config whitespace rejected (not stripped); C1 fail-closed classification: only `ok`/`pdf`/`boilerplate` dispositions pass (on both the original and percent-decoded representations) — `invalid` (comma, semicolon, decoded space) now denies the URL; raw brackets outside the authority denied | C1, C2, C3 |
| `tests/maintenance/test_p02a_http_policy.py` | 35 new C1/C2/C3/C4 regression tests over the real policy and transport (no production bypasses) | C1–C4 |
| `tests/maintenance/fixtures.py` | `scripted_http_session` gains a real `headers` dict (production User-Agent assignment) and scripted-exception support for transport-error tests | C4 |

No harness tools, P00/P01 tests, browser code, legacy URL helpers, acquisition/monitor callers, corpora or vector data changed.

## Correction mapping

### C1 — invalid classification must not bypass wizard exclusion
RED: all four forbidden URLs (`login?x=1,2`, `log%69n?x=1,2`, `x%20y/log%69n`, `page?x=1;2`) returned `allows=True` and were contacted through the real transport. GREEN: only `ok`/`pdf`/`boilerplate` dispositions pass the classification gate on both representations; all four are now denied through `allows` and the transport (zero calls for initial, only A for redirect destinations). Positive controls (`page?q=1`, `page?x=1&y=2`) pass through transport.

### C2 — host validation and IPv6 canonicalization
RED: `[:::]` accepted; `[2001:db8::1]` config allowed but `allows('https://[2001:db8::1]/')` returned False; `bad..gov.example` matched wildcard; whitespace config stripped into valid; scoped IPv6 not rejected; empty port accepted. GREEN: stdlib `ipaddress` validates and canonicalizes IP literals (bracketed/unbracketed IPv6, IPv4); `[:::]` → ValueError; `[2001:db8::1]` config matches bracketed, bracket-free and expanded-form URLs; scoped `%`-literals rejected; `bad..gov.example` denied via label validation; `*.192.0.2.10` → ValueError (wildcard-over-IP); whitespace config → ValueError; empty port denied; trailing-dot DNS names normalized on both sides; IPv6 URL reaches the scripted session and succeeds.

### C3 — immutable policy
RED: `policy.max_redirects = 100`, `policy._hosts = frozenset({'external.example'})` and `policy.allowed_hosts = ...` all succeeded; `del policy.max_redirects` succeeded. GREEN: frozen dataclass — all ordinary assignments/deletions raise `AttributeError` (FrozenInstanceError subclass); input-set aliasing cannot change decisions; a transport bound to the policy still denies the external host after attempted mutation.

### C4 — response cleanup and exception boundaries
RED: `ChunkedEncodingError` during content access escaped `fetch` (close count 0); unscripted-session `AssertionError` swallowed as `error_kind='connection'`. GREEN: one `try/finally` per received response — closed exactly once even if processing raises; only `requests.exceptions.RequestException` becomes a transport outcome; content-read failures retain received status/final_url/chain with no partial body; `AssertionError`/`RuntimeError` propagate after cleanup; all existing paths (success, HTTP rejection, redirect denial, loop, missing Location, hop limit) still close each received response exactly once.

## Verification (audited interpreter 3.12.10)

- `python -B tools/run_offline_checks.py --pattern 'test_p02a_*.py'` → exit 0, ran 52, failures 0, errors 0, attempts 0.
- `python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'` → exit 0; legacy [60, 31, 63] + multiturn pass; discovery ran 141 (24 P00 + 33 P01 + 84 P02A), failures 0, errors 0, skips 0, attempts 0.
- `git diff --check` → exit 0.
- Exported: `python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'` → exit 0 on the final commit.

## Limitations

- C1 is a post-capture policy check on the transport's policy branch; the external request itself is not prevented (P02B/P03 scope for pre-contact enforcement).
- Scoped IPv6 literals are rejected for now; interface-specific routing is outside the host registry.
- The `urlunsplit_parsed` helper is local to this module and does not alter the shared URL policy.
- The `DeprecationWarning` for tar `filter=` noted in P00 remains cosmetic on 3.12.

STOPPED; next packet not started.
