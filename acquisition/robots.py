"""
acquisition/robots.py — robots.txt handling shared by freshness and the
crawler (Phase 3.5 §11 shared primitives).

Policy (deliberately documented, unchanged since Phase 1):
  * robots.txt 2xx        → parse it; explicit Disallow wins (never evaded).
  * robots.txt 4xx / 404  → RFC 9309: no restrictions (unrestricted).
  * robots.txt 5xx/unreachable → proceed with a note. urllib's parser would
    disallow-all on 5xx, but services.nidw.gov.bd has returned a permanent
    503 on robots.txt while serving its public pages normally — a
    misconfigured endpoint must not silently disable acquisition. The note
    surfaces in manifests/reports.
Explicit robots directives are ALWAYS honored: sites that genuinely serve
Disallow rules (or block the browser outright) are never evaded — they land
in needs_manual_screenshot per spec §0 note 6 / Phase 3.5 §3.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field


@dataclass
class RobotsCache:
    parsers: dict = field(default_factory=dict)
    unavailable: dict = field(default_factory=dict)   # domain -> note


def robots_check(robots: RobotsCache, url: str, user_agent: str) -> tuple[bool, str | None]:
    """Returns (allowed, note). See module docstring for the policy."""
    domain = urllib.parse.urlsplit(url).netloc.lower()
    if domain in robots.parsers:
        parser = robots.parsers[domain]
        note = None
    elif domain in robots.unavailable:
        return True, robots.unavailable[domain]
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
                # (observed in Phase 2), so allow_all must be set explicitly.
                parser.allow_all = True
                robots.parsers[domain] = parser
                note = None
            else:
                note = f"robots_unreachable_http_{exc.code}"
                robots.unavailable[domain] = note
                return True, note
        except (urllib.error.URLError, OSError, UnicodeDecodeError):
            note = "robots_unreachable"
            robots.unavailable[domain] = note
            return True, note
    try:
        return parser.can_fetch(user_agent, url), note
    except Exception:
        return True, note
