"""
acquisition/navigation_policy.py — explicit navigation policy for the
tracker's acquisition paths (P02A contract A; P02A-C1 corrections).

An immutable configuration that decides, WITHOUT any network activity,
whether a URL may be contacted:

    NavigationPolicy(allowed_hosts: frozenset[str], max_redirects: int = 5)
    policy.allows(url: str) -> bool

Rules fixed by the P02A contract and the C1 review:
  * an empty host set denies every URL;
  * `max_redirects` is a nonnegative integer (bool, negative and noninteger
    values are rejected with ValueError at construction);
  * host configuration contains hostnames or IP literals — never URL strings,
    credentials, ports, paths or a bare wildcard; malformed configuration is
    rejected with ValueError (configuration whitespace is rejected, not
    stripped into a valid host); host case is normalized consistently and
    trailing-dot DNS names are normalized on both configuration and URL sides;
  * IP literals are validated with stdlib `ipaddress` (bracketed or
    unbracketed IPv6 accepted, stored canonical and bracket-free; equivalent
    compressed/expanded forms match; scoped literals rejected for now);
    wildcard entries over IP literals are rejected — wildcards are DNS-only
    configuration syntax;
  * `gov.example` allows exactly that host (not `www.gov.example` or
    `gov.example.evil`); `*.gov.example` allows proper subdomains including
    nested ones but not the apex — configure the apex separately; a URL
    hostname must carry nonempty valid labels before any wildcard suffix
    matching (`bad..gov.example` is denied);
  * ports are not an allowlist dimension: absent or valid ports 1-65535 can
    match the host; malformed, out-of-range, zero and explicitly empty ports
    are denied;
  * absolute HTTP(S) URLs with a hostname and syntactically valid authority
    are required; credentials, raw whitespace/control characters,
    backslashes, malformed brackets and raw brackets outside the authority
    are denied before any helper could normalize them away; invalid input
    returns False from `allows` rather than raising;
  * classification fail-closed (C1): wizard/login paths are excluded via the
    existing `acquisition.urls.classify_url(..., skip_boilerplate=False)` on
    the original AND the percent-decoded representation, and only the
    explicitly eligible dispositions (`ok`, `pdf`, `boilerplate`) may pass —
    an `invalid` classification (e.g. a comma or a decoded space the shared
    normalizer rejects) denies the URL instead of bypassing the boundary;
  * informational pages, PDF URLs and boilerplate remain navigation-eligible
    when otherwise allowed — content selection is a separate concern;
  * the configuration is immutable: ordinary assignment/deletion of
    `allowed_hosts`/`max_redirects` (or any other attribute) raises, and the
    constructor stores its own normalized copy so mutating an input set
    cannot change decisions;
  * this module keeps URL validation local so P03 can reuse it;
    `normalize_url`, `same_domain`, `allowed_domain` and `classify_url`
    themselves are unchanged.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

from acquisition.urls import classify_url

_WILDCARD_PREFIX = "*."
_ELIGIBLE_DISPOSITIONS = ("ok", "pdf", "boilerplate")
_HOSTNAME_RE = re.compile(
    r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*\.?")


def _is_ip_literal(host: str) -> bool:
    """True when `host` (bracket-free) parses as an IP literal via stdlib.
    Scoped IPv6 literals (interface-specific meaning) are rejected."""
    if not host or "%" in host:
        return False
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _canonical_host(host: str) -> str:
    """Canonical bracket-free form for a validated IP literal."""
    return str(ipaddress.ip_address(host))


def _validate_dns_host(host: str) -> str:
    """Validate a DNS hostname (config or URL side): nonempty valid labels,
    case-insensitive, optional single trailing dot normalized away."""
    if not host or not _HOSTNAME_RE.fullmatch(host):
        raise ValueError(f"invalid DNS hostname: {host!r}")
    return host[:-1] if host.endswith(".") else host


def _validate_host_entry(entry: str) -> str:
    """Validate one configured host entry; returns the normalized form.
    Configuration whitespace is rejected, not stripped into a valid host."""
    if not isinstance(entry, str):
        raise ValueError(f"host entry must be a string, got {type(entry).__name__}")
    if entry.strip() != entry:
        raise ValueError(f"host entry {entry!r} carries leading/trailing whitespace")
    host = entry.lower()                     # normalize host case consistently
    if not host:
        raise ValueError(f"host entry {entry!r} is empty")

    wildcard = host.startswith(_WILDCARD_PREFIX)
    if "*" in host and not (wildcard and "*" not in host[2:]):
        raise ValueError(f"host entry {entry!r} supports only a leading '*.' wildcard")
    if host == "*":
        raise ValueError("bare wildcard host entries are not allowed")

    body = host[len(_WILDCARD_PREFIX):] if wildcard else host

    # IP literals (bracketed or unbracketed IPv6, IPv4) — checked BEFORE the
    # generic colon rejection (IPv6 literals legitimately contain colons)
    if body.startswith("[") and body.endswith("]"):
        inner = body[1:-1]
        if not _is_ip_literal(inner):
            raise ValueError(f"host entry {entry!r} is not a valid bracketed "
                             f"IPv6 literal")
        if wildcard:
            raise ValueError(f"wildcard host entry {entry!r} must not apply to "
                             f"an IP literal")
        return _canonical_host(inner)
    if "[" in body or "]" in body:
        raise ValueError(f"host entry {entry!r} has malformed brackets")
    if _is_ip_literal(body):
        if wildcard:
            raise ValueError(f"wildcard host entry {entry!r} must not apply to "
                             f"an IP literal")
        return _canonical_host(body)

    for ch in (":", "@", "/", "\\", "?", "#"):
        if ch in host:
            raise ValueError(f"host entry {entry!r} must not contain {ch!r} "
                             "(ports, credentials, paths and schemes are not host configuration)")

    result = _validate_dns_host(body)
    return f"{_WILDCARD_PREFIX}{result}" if wildcard else result


def _canonical_url_host(host: str) -> tuple:
    """Canonicalize a URL hostname: returns (canonical_host, is_ip_literal).
    Bracket-free input is expected (urlsplit's hostname strips brackets);
    scoped literals are rejected."""
    lowered = (host or "").lower()
    if not lowered:
        raise ValueError("empty URL hostname")
    if "%" in lowered:
        raise ValueError(f"scoped IPv6 URL hostname rejected: {lowered!r}")
    if _is_ip_literal(lowered):
        return _canonical_host(lowered), True
    return _validate_dns_host(lowered), False


@dataclass(frozen=True)
class NavigationPolicy:
    """Immutable navigation allowlist (see module docstring)."""

    allowed_hosts: frozenset
    max_redirects: int = 5

    def __post_init__(self):
        if isinstance(self.max_redirects, bool) or not isinstance(self.max_redirects, int) \
                or self.max_redirects < 0:
            raise ValueError(f"max_redirects must be a nonnegative integer, "
                             f"got {self.max_redirects!r}")
        if not isinstance(self.allowed_hosts, (frozenset, set, list, tuple)):
            raise ValueError("allowed_hosts must be a frozenset/set/list/tuple "
                             "of host entries")
        # controlled initialization: store the normalized copy so mutating an
        # input set cannot change decisions (and the frozen dataclass blocks
        # ordinary assignment/deletion afterwards)
        object.__setattr__(self, "allowed_hosts",
                           frozenset(_validate_host_entry(h) for h in self.allowed_hosts))

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
        # an explicitly empty port ("host:") is denied, not treated as absent
        if parsed.netloc.endswith(":"):
            return False
        try:
            host, is_ip = _canonical_url_host(parsed.hostname or "")
        except ValueError:
            return False
        # raw brackets anywhere outside the authority are denied
        if ("[" in parsed.path or "]" in parsed.path
                or "[" in parsed.query or "]" in parsed.query):
            return False

        # wizard/login exclusion on the original and the percent-decoded
        # representation — fail closed: only explicitly eligible dispositions
        # (ok/pdf/boilerplate) may pass; `invalid` (e.g. comma or decoded
        # space the shared normalizer rejects) denies the URL (C1)
        disposition, _ = classify_url(url, skip_boilerplate=False)
        if disposition not in _ELIGIBLE_DISPOSITIONS:
            return False
        decoded = urlunsplit_parsed(parsed, decoded_path=True)
        decoded_disposition, _ = classify_url(decoded, skip_boilerplate=False)
        if decoded_disposition not in _ELIGIBLE_DISPOSITIONS:
            return False

        return self._host_allowed(host, is_ip)

    def _host_allowed(self, host: str, is_ip: bool) -> bool:
        for entry in self.allowed_hosts:
            if entry == host:
                return True
            if is_ip:
                continue    # DNS wildcard matching never applies to IP hosts
            if entry.startswith(_WILDCARD_PREFIX):
                domain = entry[len(_WILDCARD_PREFIX):]
                if not _is_ip_literal(domain) and host.endswith("." + domain):
                    return True   # proper subdomain; the apex is NOT matched
        return False


def urlunsplit_parsed(parsed, decoded_path: bool = False) -> str:
    """Rebuild an absolute URL from a SplitResult, optionally percent-decoding
    the path; the original path/query order and case are preserved."""
    from urllib.parse import urlunsplit
    path = unquote(parsed.path) if decoded_path else parsed.path
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))


@dataclass(frozen=True)
class IntersectedNavigationPolicy:
    """Immutable intersection of two navigation policies (P02B).

    `allows(url)` requires BOTH constituent policies to allow the URL.
    `max_redirects` is the smaller limit. Both original policies remain
    unchanged. This composition avoids merging host sets (which would
    accidentally form a union) and is safe to bind to a transport copy.
    """

    _first: NavigationPolicy
    _second: NavigationPolicy

    @property
    def max_redirects(self) -> int:
        return min(self._first.max_redirects, self._second.max_redirects)

    def allows(self, url: str) -> bool:
        return self._first.allows(url) and self._second.allows(url)
