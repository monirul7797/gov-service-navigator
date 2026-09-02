# Acquisition Layer — Architecture Note (Phase 3.5)

**Status:** implemented and validated (see `acquisition/`, `crawl.py`,
`test_acquisition.py`)
**Supersedes:** the assumption that every TLS-invalid or normally-unfetchable
page immediately becomes a manual-screenshot task. It does **not** weaken the
verification gate, the anti-evasion rules, or the PII/wizard exclusions.

## The fallback hierarchy (explicit, in order)

```text
verified HTTP/browser fetch
        ↓  (failure or JS-only content)
browser-rendered fallback (headless Chromium, certificate validation ON)
        ↓  (only when the TLS certificate itself is invalid)
invalid-TLS capture-only evidence (Chromium evidence context,
                                   ignore_https_errors=True)
        ↓  (only when the browser itself is blocked)
manual screenshot — LAST fallback, never the first
```

### What each level means

| Level | Method (provenance) | TLS verified? | verification_status |
|---|---|---|---|
| `requests` GET, `verify=True`, static HTML | `verified_normal` | yes | `verified` |
| `requests` GET, `verify=True`, PDF/binary | `direct_pdf_verified` | yes | `verified` |
| Chromium strict context (goto succeeds) | `verified_browser` | yes (Chromium validated) | `verified` |
| Chromium **evidence context** (`ignore_https_errors=True`) | `capture_only_tls_invalid` | **no** | `capture_only` |
| Direct PDF downloaded through the evidence context | `direct_pdf_unverified` | **no** | `capture_only` |

**Critical rule:** `ignore_https_errors=True` does **not** make a source
verified. It exists only on a dedicated evidence context, is only entered
after the strict path failed specifically on a TLS error, and every capture
from it is stamped `verification_status: "capture_only"` +
`requires_secondary_verification: true` + the original TLS error string.
Capture-only material can be consulted by a human; it is never silently
promoted into the verified corpus and never written to corpus files.

## Where the layer lives

```
acquisition/
  urls.py        URL normalization/dedup, allowlist, wizard/boilerplate/PII
                 URL-level exclusion (pure functions, unit-tested)
  safeclick.py   safe interaction policy (§7) — pure classification
  extract.py     deterministic readability-strip + normalization (moved
                 VERBATIM from freshness/fetch.py; hash-compatible)
  robots.py      shared robots.txt policy (2xx honor, 4xx unrestricted per
                 RFC 9309, 5xx proceed-with-note; explicit Disallow always wins)
  transport.py   RequestsTransport (strict) + PlaywrightRenderer with TWO
                 contexts: strict (cert validation ON) and evidence
                 (ignore_https_errors=True)
  acquire.py     layered capture primitive → Capture + provenance JSON
  crawler.py     bounded seed-driven crawler + safe dynamic-state capture +
                 manifest
crawl.py         CLI: python crawl.py --service <name> --seed <url>
```

Freshness integration: `freshness/fetch.py` now imports its extraction,
robots, and URL primitives from `acquisition/` (one implementation, no
drift). Freshness deliberately always uses the rendered-DOM extraction path:
its text is compared hash-to-hash across runs, so a page flipping between
"static" and "JS-rendered" must not flip its hash. Byte-parity with the
pre-refactor extraction was verified before committing.

## Bounded crawler guarantees

* seed-driven; same-domain by default; configurable domain allowlist
* max depth 2 (configurable), max 40 pages per run, ~1 request/second
* URL dedup by normalized URL (fragment dropped; host lowercased; default
  ports dropped; query strings preserved verbatim)
* every discovered-but-not-followed URL records WHY (wizard / boilerplate /
  out-of-allowlist / duplicate)
* crawl graph: each node records `parent` (which page linked to it) + depth

## Safe dynamic-state capture

Candidates: `a[href="#"]`, buttons, `[onclick]`, `[role="button"]` (hidden
elements filtered). For each, the safe-click policy decides:

* **rejected** — anything resembling Submit / Apply / Login / Sign in /
  Continue / Save / Delete / Pay / Confirm / Book / Schedule / Upload / Send /
  Next-in-wizard / Reprint / View-status / incomplete-continuation, form
  inputs, elements inside `<form>`, or write/transactional attributes
* **skipped ambiguous** — recorded as `TODO(Monirul): review interaction`
* **safe** — capture DOM text before → activate → wait for stability →
  compare → if meaningful new content appears, capture as a distinct
  rendered state with provenance: source_url, state_name, trigger_text,
  trigger_locator, parent_page, capture_timestamp, content_sha256

Each state is deduplicated by content hash (identical states → `duplicate`).

## Evidence artifacts

Per captured page under `data/evidence/<service>/`:
`<slug>.html` (raw), `<slug>.txt` (clean text), `<slug>.png` (full-page
screenshot — evidence for human review, never the corpus extraction source),
`<slug>.pdf` (preserved direct government PDFs), and `manifest.json`
(per-capture provenance + dispositions + crawl graph).

Manifest dispositions distinguish at least: `captured_verified_normal`,
`captured_verified_browser`, `capture_only_tls_invalid`, `direct_pdf_verified`,
`js_dynamic_state`, `excluded_wizard`, `excluded_pii`, `excluded_boilerplate`,
`excluded_out_of_allowlist`, `duplicate`, `needs_manual_screenshot`,
`failed_fetch`, `interaction_rejected`, `interaction_skipped_ambiguous`.

## Anti-evasion (unchanged and structural)

No proxy rotation, no CAPTCHA bypass, no login automation, no session-cookie
reuse, no stealth plugins, no user-agent spoofing (the UA is the honest
project identifier `FreshnessChecker`), no rate-limit bypass, no
access-control circumvention. A site that blocks even the browser yields
`needs_manual_screenshot` — the last fallback, not the first.
