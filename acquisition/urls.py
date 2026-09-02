"""
acquisition/urls.py — URL normalization, dedup, allowlist, and URL-level
exclusion policy (Phase 3.5 §4/§5, §7).

Pure functions only — no network, no I/O — so the policy is unit-testable
hermetically (test_acquisition.py exercises every rule here).
"""
from __future__ import annotations

import re
import urllib.parse

# Application-wizard / logged-in / personal-data URL patterns (spec §0 note 3,
# Phase 3.5 §7). Any URL whose path matches is excluded BEFORE navigation —
# we never load it, let alone interact with it. Path segments may carry an
# .aspx/.php extension (visa.gov.bd is an ASP.NET site).
_WIZ_SEGMENTS = (r"viewstatus|reprint|incomplete|applyvisa|apply|login|signin|"
                 r"sign-in|logout|password|otp|checkout|payment|pay|upload|"
                 r"applicant|personal|myapplication|applicationstatus|"
                 r"continueapplication|register\.aspx")
WIZARD_URL_RE = re.compile(rf"/(?:{_WIZ_SEGMENTS})(?:\.(?:aspx|php|jsp))?(?:[/?#]|$)", re.I)

# Site boilerplate that carries no service information — skipped by the
# crawler as a configurable disposition (recorded, not silently dropped).
BOILERPLATE_URL_RE = re.compile(
    r"/(?:termscondition|terms|privacypolicy|privacy|compfeedback|cookie)"
    r"(?:\.(?:aspx|php|jsp))?(?:[/?#]|$)",
    re.I,
)

# Non-HTML resource extensions we still want (direct government documents).
PDF_RE = re.compile(r"\.pdf(?:[?#]|$)", re.I)


def normalize_url(url: str) -> str:
    """Canonical form for dedup and graph provenance.

    - lowercase scheme and host
    - drop default ports (80/443)
    - drop fragment (fragments are client-side; a fragment that corresponds to
      a meaningful rendered state is handled by the dynamic-state capture
      layer instead, keyed by the parent URL)
    - collapse an empty path to "/"
    - everything else (query string, path case) is preserved verbatim: query
      parameter order can be semantically meaningful, and percent-encoding is
      left exactly as the site emitted it.
    """
    if not url or not isinstance(url, str):
        return ""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        return ""
    if re.search(r"[\s,;]", url):
        # URLs never legitimately carry raw whitespace/commas — catches the
        # ereturn record storing two URLs comma-joined in one field.
        return ""
    parsed = urllib.parse.urlsplit(url)
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not host:
        return ""
    port = parsed.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = parsed.path or "/"
    return urllib.parse.urlunsplit((scheme, host, path, parsed.query, ""))


def same_domain(url: str, domain: str) -> bool:
    """True when url is on `domain` or a subdomain of it."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host == domain.lower() or host.endswith("." + domain.lower())


def allowed_domain(url: str, allowlist: set[str]) -> bool:
    return any(same_domain(url, d) for d in allowlist)


def classify_url(url: str, *, skip_boilerplate: bool = True) -> tuple[str, str]:
    """URL-level disposition policy.

    Returns (disposition, reason) with disposition one of:
      'invalid'               — not a usable http(s) URL (malformed, comma-joined…)
      'wizard'                — application-wizard / login / PII-risk flow: never loaded
      'boilerplate'           — terms/privacy/etc.: recorded, not staged as content
      'pdf'                   — direct government document to download
      'ok'                    — informational page eligible for capture
    """
    normalized = normalize_url(url)
    if not normalized:
        return "invalid", "not a usable http(s) URL"
    if WIZARD_URL_RE.search(urllib.parse.urlsplit(normalized).path):
        return "wizard", "application-wizard/login/PII-risk URL pattern"
    if skip_boilerplate and BOILERPLATE_URL_RE.search(urllib.parse.urlsplit(normalized).path):
        return "boilerplate", "terms/privacy/feedback boilerplate (recorded, not staged)"
    if PDF_RE.search(normalized):
        return "pdf", "direct PDF document"
    return "ok", ""
