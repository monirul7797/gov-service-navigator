"""
tests/maintenance/fixtures.py — shared data builders and fakes for the
maintenance packet tests (P00+).

Per the control guide: this module contains data builders/fakes, never
production bypasses. Production robots semantics are untouched — fixture
tests inject pre-seeded robots caches at the same external boundaries the
production code uses (KnownSourceMonitor(robots=...), RobotsCache() inside
the crawler via a test-side patch of the crawler's factory name).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from acquisition.robots import RobotsCache
from urllib.robotparser import RobotFileParser
from types import SimpleNamespace
from unittest.mock import Mock, patch


def robots_for_hosts(hosts) -> RobotsCache:
    """Pre-seeded allow-all cache for the given hosts — no network access.

    Real lookups key parsers by hostname, so every fixture host must be
    pre-seeded explicitly (the historical '*' sentinel was never consulted).
    Explicit disallow/unreachable cases keep their own dedicated fixtures.
    """
    cache = RobotsCache()
    for host in hosts:
        parser = RobotFileParser()
        parser.allow_all = True
        cache.parsers[host] = parser
    return cache


def hosts_from_corpora(corpus_paths) -> set:
    """Every hostname cited by the fixture corpora — used so a monitor built
    without an explicit robots cache still gets a fully pre-seeded one."""
    hosts = set()
    for path in corpus_paths:
        p = Path(path)
        if not p.exists():
            continue
        with open(p, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                url = json.loads(line).get("source_url") or ""
                if url.startswith("http://") or url.startswith("https://"):
                    from urllib.parse import urlparse
                    host = (urlparse(url).hostname or "").lower()
                    if host:
                        hosts.add(host)
    return hosts


def patch_crawler_robots(hosts):
    """Patch the crawler's RobotsCache factory so a Crawler built inside a
    test never performs real robots.txt fetches. External-boundary injection:
    production lookup semantics are unchanged."""
    return patch("acquisition.crawler.RobotsCache",
                 return_value=robots_for_hosts(hosts))


def scripted_session(responses: dict):
    """Fake requests.Session factory for transport tests: responses map
    URL -> SimpleNamespace(status_code, headers, content, url). Records the
    requested URLs in .urls and raises on any unscripted request."""
    from types import SimpleNamespace
    from unittest.mock import Mock

    session = Mock()
    session.urls = []

    def _get(url, *args, **kwargs):
        session.urls.append(url)
        if url not in responses:
            raise AssertionError(f"unscripted request: {url}")
        resp = responses[url]
        return SimpleNamespace(status_code=resp.get("status_code", 200),
                               headers=resp.get("headers", {"content-type": "text/html"}),
                               content=resp.get("content", b""),
                               url=resp.get("url", url))

    session.get.side_effect = _get
    return session


def make_pdf(identifier: str) -> bytes:
    """Deterministic, genuinely parseable one-page PDF fixture (P01).

    Assembles a minimal but structurally complete PDF (catalog, pages, page,
    content stream, computed xref) whose content stream embeds the identifier,
    so two calls with different identifiers produce two distinct valid
    documents. pdfplumber parses these with exactly one accessible page.
    """
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
    ]
    content = f"BT 72 72 Td ({identifier}) Tj ET".encode("latin-1")
    objects.append(b"<< /Length " + str(len(content)).encode() +
                   b" >>\nstream\n" + content + b"\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF").encode()
    return bytes(out)


def scripted_http_session(routes: dict) -> Mock:
    """P02A: scripted fake Requests session for navigation-policy tests.

    routes maps request URL -> response spec dict with keys:
      status (int), headers (dict, case as served), body (bytes),
      final_url (str, the response's own URL), history (list of URLs).

    The session records every requested URL (.urls), every call's kwargs
    (.call_kwargs) and per-URL close counts (.close_counts: the number of
    responses for that URL whose close() ran). Each request returns a FRESH
    response object with its own close() so closure counts are exact. Any
    unscripted request raises AssertionError loudly.
    """
    from unittest.mock import Mock

    session = Mock()
    session.headers = {}          # real dict: production assigns User-Agent
    session.urls = []
    session.call_kwargs = []
    session.close_counts = {}

    def _get(url, **kwargs):
        session.urls.append(url)
        session.call_kwargs.append(kwargs)
        if url not in routes:
            raise AssertionError(f"unscripted request: {url}")
        spec = routes[url]
        if spec.get("exception") is not None:
            raise spec["exception"]
        response = SimpleNamespace(
            status_code=spec.get("status", 200),
            headers=spec.get("headers", {}),
            content=spec.get("body", b""),
            url=spec.get("final_url", url),
            history=[SimpleNamespace(url=h) for h in spec.get("history", [])],
        )
        closes = {"count": 0}

        def close():
            closes["count"] += 1
            session.close_counts[url] = closes["count"]

        response.close = close
        return response

    session.get.side_effect = _get
    return session
