"""
acquisition/navigation_policy.py — explicit navigation policy for the
tracker's acquisition paths (P02A, contract A).

An immutable configuration that decides, WITHOUT any network activity,
whether a URL may be contacted:

    NavigationPolicy(allowed_hosts: frozenset[str], max_redirects: int = 5)
    policy.allows(url: str) -> bool

Rules fixed by the P02A contract:
  * an empty host set denies every URL;
  * `max_redirects` is a nonnegative integer (bool, negative and noninteger
    values are rejected with ValueError at construction);
  * host configuration contains hostnames or IP literals — never URL strings,
    credentials, ports, paths or a bare wildcard; malformed configuration is
    rejected with ValueError; host case is normalized consistently;
  * `gov.example` allows exactly that host (not `www.gov.example` or
    `gov.example.evil`); `*.gov.example` allows proper subdomains including
    nested ones but not the apex — configure the apex separately;
  * ports are not an allowlist dimension: absent or valid ports 1–65535 can
    match the host; malformed, out-of-range and zero ports are denied;
  * absolute HTTP(S) URLs with a hostname and syntactically valid authority
    are required; credentials, raw whitespace/control characters,
    backslashes, malformed brackets and invalid ports are denied before any
    helper could normalize them away; invalid input returns False from
    `allows` rather than raising;
  * wizard/login paths are excluded via the existing
    `acquisition.urls.classify_url(..., skip_boilerplate=False)` (its
    malformed-URL exceptions are caught), and the percent-decoded path is
    checked with the same patterns so `/log%69n` cannot avoid classification;
  * informational pages, PDF URLs and boilerplate remain navigation-eligible
    when otherwise allowed — content selection is a separate concern;
  * this module keeps URL validation local so P03 can reuse it;
    `normalize_url`, `same_domain`, `allowed_domain` and `classify_url`
    themselves are unchanged.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit

from acquisition.urls import classify_url

_WILDCARD_PREFIX = "*."


def _validate_host_entry(entry: str) -> str:
    """Validate one configured host entry; returns the normalized form."""
    if not isinstance(entry, str):
        raise ValueError(f"host entry must be a string, got {type(entry).__name__}")
    host = entry.strip().lower()
    if not host:
        raise ValueError("host entry is empty")
    wildcard = host.startswith(_WILDCARD_PREFIX)
    if "*" in host and not (wildcard and "*" not in host[2:]):
        raise ValueError(f"host entry {entry!r} supports only a leading '*.' wildcard")
    if host == "*":
        raise ValueError("bare wildcard host entries are not allowed")

    # bracketed IPv6 literal in configuration — must be handled BEFORE the
    # generic colon rejection (IPv6 literals legitimately contain colons)
    if host.startswith("[") and host.endswith("]"):
        ip6 = host[1:-1]
        if not ip6 or not re.fullmatch(r"[0-9a-f:]+", ip6) or ":" not in ip6:
            raise ValueError(f"host entry {entry!r} is not a valid IPv6 literal")
        return host

    for ch in (":", "@", "/", "\\", "?", "#"):
        if ch in host:
            raise ValueError(f"host entry {entry!r} must not contain {ch!r} "
                             "(ports, credentials, paths and schemes are not host configuration)")
    if re.search(r"\s", host):
        raise ValueError(f"host entry {entry!r} must not contain whitespace")

    body = host[2:] if wildcard else host
    if not re.fullmatch(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.?",
                        body):
        raise ValueError(f"host entry {entry!r} is not a valid hostname or IP literal")
    return host


class NavigationPolicy:
    """Immutable navigation allowlist (see module docstring)."""

    def __init__(self, allowed_hosts: frozenset[str], max_redirects: int = 5):
        if isinstance(max_redirects, bool) or not isinstance(max_redirects, int) \
                or max_redirects < 0:
            raise ValueError(f"max_redirects must be a nonnegative integer, "
                             f"got {max_redirects!r}")
        if allowed_hosts is None or not isinstance(allowed_hosts, (frozenset, set, list, tuple)):
            raise ValueError("allowed_hosts must be a frozenset/set/list/tuple "
                             "of host entries")
        self.max_redirects = max_redirects
        self._hosts = frozenset(_validate_host_entry(h) for h in allowed_hosts)

    @property
    def allowed_hosts(self) -> frozenset:
        return self._hosts

    def allows(self, url: str) -> bool:
        """Pure policy decision. Invalid input returns False; this method
        never raises and never touches the network."""
        try:
            return self._allows(url)
        except (ValueError, TypeError):
            return False

    def _allows(self, url: str) -> bool:
        if not isinstance(url, str) or not url.strip():
            return False
        # reject raw whitespace/control characters and backslashes before any
        # helper could normalize them away
        if re.search(r"[\s\x00-\x1f\x7f]", url) or "\\" in url:
            return False

        parsed = urlsplit(url)
        scheme = (parsed.scheme or "").lower()
        if scheme not in ("http", "https"):
            return False
        if not parsed.netloc:
            return False
        if "@" in parsed.netloc:
            return False                    # credentials in the authority
        try:
            port = parsed.port              # raises on out-of-range/invalid
        except ValueError:
            return False
        if port is not None and not (0 < port <= 65535):
            return False
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        # raw brackets anywhere outside the authority (path/query) are denied:
        # they are gen-delim characters and must be percent-encoded there
        if ("[" in parsed.path or "]" in parsed.path
                or "[" in parsed.query or "]" in parsed.query):
            return False

        # wizard/login exclusion on the original and the percent-decoded path
        # (malformed-URL exceptions from the classifier are caught by allows)
        disposition, _ = classify_url(url, skip_boilerplate=False)
        if disposition == "wizard":
            return False
        decoded = urlunsplit_parsed(parsed, decoded_path=True)
        decoded_disposition, _ = classify_url(decoded, skip_boilerplate=False)
        if decoded_disposition == "wizard":
            return False

        return self._host_allowed(host)

    def _host_allowed(self, host: str) -> bool:
        if host in self._hosts:
            return True
        for entry in self._hosts:
            if entry.startswith(_WILDCARD_PREFIX):
                domain = entry[len(_WILDCARD_PREFIX):]
                # proper subdomains only — the apex is NOT matched
                if host.endswith("." + domain):
                    return True
        return False


def urlunsplit_parsed(parsed, decoded_path: bool = False) -> str:
    """Rebuild an absolute URL from a SplitResult, optionally percent-decoding
    the path; the original path/query order and case are preserved."""
    from urllib.parse import urlunsplit
    path = unquote(parsed.path) if decoded_path else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
