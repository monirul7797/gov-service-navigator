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
