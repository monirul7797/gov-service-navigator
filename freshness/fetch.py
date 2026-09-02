"""
freshness/fetch.py — Playwright re-fetch of each corpus record's source_url.

Part A §A3 of the build spec (gsn-freshness-and-new-service-spec.md).

Mental model:
  Every corpus record carries a source_url. This module walks one or more
  corpora, extracts (record_id, source_url) pairs — one mapper per service,
  because the four .jsonl schemas differ — and re-fetches each URL with a
  headless Chromium, extracting the page's main text.

  The extracted text is NOT compared to the corpus record's text. It is only
  ever compared to itself (hash-to-hash) across runs, so the extraction does
  not need to be a perfect readability clone — it needs to be DETERMINISTIC.

  Records whose URL is missing, non-http, or on a known-blocked domain are
  never fetched. Blocked records are surfaced with status
  "needs_manual_screenshot" so a human can supply a screenshot. We do NOT
  evade blocks (no fake user-agents, no proxy tricks) — see spec §0 note 6.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field

REPO_ROOT = r"C:\Users\Monirul\Documents\Structured Data\NID\gov-service-navigator\gov-service-navigator"

CORPUS_PATHS = {
    "nid": f"{REPO_ROOT}\\data\\chunks\\nid.jsonl",
    "epassport": f"{REPO_ROOT}\\data\\chunks\\epassport.jsonl",
    "ereturn": f"{REPO_ROOT}\\data\\chunks\\ereturn.jsonl",
    "bdris": f"{REPO_ROOT}\\data\\chunks\\bdris.jsonl",
}

# Spec §0 note 6: known blockers. Never fetched, never evaded.
#   epassport.gov.bd        — Akamai bot wall (HTTP 403 "Access Denied")
#   orgbdr.gov.bd           — robots-disallowed / TLS handshake fails
#   bdlaws.minlaw.gov.bd    — broken charset (documented unusable)
KNOWN_BLOCKED_DOMAINS = {
    "epassport.gov.bd",
    "orgbdr.gov.bd",
    "bdlaws.minlaw.gov.bd",
}

FETCH_INTERVAL_SECONDS = 1.0   # rate limit: be polite to gov infrastructure
PAGE_SETTLE_MS = 1500          # let SPA content paint before extraction (validated stable)


@dataclass
class FetchResult:
    record_id: str
    service: str
    source_url: str
    status: str                      # "fetched" | "needs_manual_screenshot" | "skipped"
    reason: str | None = None        # why a fetch did not happen
    text: str | None = None          # extracted main text (only when status == "fetched")
    http_status: int | None = None


@dataclass
class _RobotsCache:
    parsers: dict = field(default_factory=dict)
    unavailable: set = field(default_factory=set)   # domains whose robots.txt could not be read


def _clean_url(url: str) -> str | None:
    """Return a normalized fetchable URL, or None if the value is not one.

    Rejects: empty values, non-http schemes, and malformed netlocs
    (one ereturn record stores two URLs comma-joined in one field).
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    if re.search(r"[\s,;]", url):
        # URLs never legitimately carry raw whitespace/commas — this catches
        # the ereturn record storing two URLs comma-joined in one field.
        return None
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc or any(c in parsed.netloc for c in ",; \t"):
        return None
    return url


def domain_of(url: str) -> str:
    return urllib.parse.urlparse(url).netloc.lower()


def is_known_blocked(url: str) -> bool:
    netloc = domain_of(url)
    return any(netloc == d or netloc.endswith("." + d) for d in KNOWN_BLOCKED_DOMAINS)


# ---------------------------------------------------------------------------
# Per-service record extraction — one mapper per service (schemas differ, §A3)
# ---------------------------------------------------------------------------

def _iter_corpus(path: str):
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def extract_nid(path: str):
    """nid.jsonl: id, source_url, text. All 102 records carry both."""
    for rec in _iter_corpus(path):
        yield rec["id"], rec.get("source_url"), rec.get("text", "")


def extract_epassport(path: str):
    """epassport.jsonl: epassport-style schema; text field holds the chunk."""
    for rec in _iter_corpus(path):
        yield rec["id"], rec.get("source_url"), rec.get("text", "")


def extract_ereturn(path: str):
    """ereturn.jsonl: pure-FAQ schema. One known record has a comma-joined
    URL ('https://etaxnbr.gov.bd/, https://nbr.gov.bd/') which _clean_url
    rejects as malformed — it surfaces as skipped, not fetched."""
    for rec in _iter_corpus(path):
        yield rec["id"], rec.get("source_url"), rec.get("text", "")


def extract_bdris(path: str):
    """bdris.jsonl: mixed build. Several rules2018 records cite a gazette
    ('gazette:S.R.O.79-Law/2018 ...') — not a URL, must be skipped."""
    for rec in _iter_corpus(path):
        yield rec["id"], rec.get("source_url"), rec.get("text", "")


EXTRACTORS = {
    "nid": extract_nid,
    "epassport": extract_epassport,
    "ereturn": extract_ereturn,
    "bdris": extract_bdris,
}


def collect_targets(services: list[str], record_ids: set[str] | None = None):
    """Walk the named corpora and yield FetchResult stubs carrying just the
    fetch decision: known-blocked / malformed URLs are marked here, before
    any network I/O happens."""
    for svc in services:
        if svc not in EXTRACTORS:
            raise ValueError(f"unknown service {svc!r}; expected one of {sorted(EXTRACTORS)}")
        for record_id, source_url, _corpus_text in EXTRACTORS[svc](CORPUS_PATHS[svc]):
            if record_ids is not None and record_id not in record_ids:
                continue
            clean = _clean_url(source_url)
            if clean is None:
                yield FetchResult(record_id, svc, source_url or "", "skipped",
                                  reason="no_source_url_or_non_http")
            elif is_known_blocked(clean):
                yield FetchResult(record_id, svc, clean, "needs_manual_screenshot",
                                  reason="known_blocked_domain")
            else:
                yield FetchResult(record_id, svc, clean, "pending")


# ---------------------------------------------------------------------------
# robots.txt — honor explicit disallow directives; no evasion (spec §0 note 6)
# ---------------------------------------------------------------------------

def _robots_check(robots: _RobotsCache, url: str, user_agent: str) -> tuple[bool, str | None]:
    """Honor robots.txt directives when the file is actually served.

    Returns (allowed, note). Policy (documented deliberately):
      * robots.txt 2xx        → parse it; explicit Disallow wins (never evaded).
      * robots.txt 4xx / 404  → RFC 9309: no restrictions (unrestricted).
      * robots.txt 5xx/unreachable → proceed with a note. urllib's parser would
        disallow-all on 5xx, but services.nidw.gov.bd has returned a permanent
        503 on robots.txt while serving its public pages normally (the whole
        corpus was built from them) — a misconfigured endpoint must not
        silently disable change detection. Noted in the report instead.
    """
    domain = domain_of(url)
    if domain in robots.parsers:
        parser = robots.parsers[domain]
        note = None
    elif domain in robots.unavailable:
        return True, "robots_unreachable"
    else:
        parser = urllib.robotparser.RobotFileParser()
        robots_url = urllib.parse.urljoin(url, "/robots.txt")
        try:
            req = urllib.request.Request(robots_url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            parser.parse(raw.splitlines())
            if not parser.entries and parser.default_entry is None:
                parser.allow_all = True   # 2xx but zero rules = unrestricted
            robots.parsers[domain] = parser
            note = None
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500:
                # RFC 9309: 4xx on robots.txt = no restrictions. NOTE: an empty
                # RobotFileParser's can_fetch() returns False on CPython 3.12
                # (observed), so allow_all must be set explicitly.
                parser.allow_all = True
                robots.parsers[domain] = parser
                note = None
            else:
                robots.unavailable.add(domain)
                return True, f"robots_unreachable_http_{exc.code}"
        except (urllib.error.URLError, OSError, UnicodeDecodeError):
            robots.unavailable.add(domain)
            return True, "robots_unreachable"
    try:
        return parser.can_fetch(user_agent, url), note
    except Exception:
        return True, note


# ---------------------------------------------------------------------------
# Playwright fetch + deterministic main-text extraction
# ---------------------------------------------------------------------------

# A simple readability-style strip (spec §A3: doesn't need to be exact — the
# extracted text is compared to itself hash-to-hash over time, never to the
# corpus text). Runs inside the page; must be a single JS expression.
_STRIP_AND_EXTRACT_JS = """
() => {
    const sels = ['script', 'style', 'noscript', 'nav', 'footer', 'header',
                  'aside', 'iframe', '[role="banner"]', '[role="navigation"]',
                  '[role="contentinfo"]'];
    for (const sel of sels) {
        document.querySelectorAll(sel).forEach(el => el.remove());
    }
    return document.body.innerText;
}
"""


def _normalize(text: str) -> str:
    """Deterministic whitespace normalization so hash comparisons don't flip
    on CRLF or indentation noise."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    blanks = 0
    for ln in lines:
        if ln == "":
            blanks += 1
            if blanks > 1:
                continue
        else:
            blanks = 0
        out.append(ln)
    return "\n".join(out).strip()


def fetch_page_text(page, url: str) -> tuple[str, int | None]:
    """Fetch one URL with the shared browser page and return (text, http_status).
    Raises on navigation failure; callers translate that into a skip."""
    response = page.goto(url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(PAGE_SETTLE_MS)
    raw = page.evaluate(_STRIP_AND_EXTRACT_JS)
    status = response.status if response is not None else None
    if status is not None and status >= 400:
        raise RuntimeError(f"http_{status}")
    return _normalize(raw or ""), status


def fetch_targets(targets, page, robots: _RobotsCache | None = None) -> list[FetchResult]:
    """Execute fetches for 'pending' targets at the rate limit. Non-pending
    targets pass through untouched."""
    robots = robots or _RobotsCache()
    results: list[FetchResult] = []
    first = True
    for t in targets:
        if t.status != "pending":
            results.append(t)
            continue
        if not first:
            time.sleep(FETCH_INTERVAL_SECONDS)
        first = False

        allowed, robots_note = _robots_check(robots, t.source_url, "FreshnessChecker")
        if not allowed:
            results.append(FetchResult(t.record_id, t.service, t.source_url,
                                       "needs_manual_screenshot", reason="robots_disallowed"))
            continue
        try:
            text, http_status = fetch_page_text(page, t.source_url)
            results.append(FetchResult(t.record_id, t.service, t.source_url,
                                       "fetched", text=text, http_status=http_status,
                                       reason=robots_note))
        except Exception as exc:   # noqa: BLE001 — any fetch failure is a skip, never a crash
            reason = str(exc)[:120] or type(exc).__name__
            results.append(FetchResult(t.record_id, t.service, t.source_url,
                                       "needs_manual_screenshot", reason=reason))
    return results
