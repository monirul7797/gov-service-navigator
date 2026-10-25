"""Per-record columns derived at build time: ``url_kind``, ``verified_at``, ``source_verified``.

``url_kind`` is an exact port of ``answer.url_kind`` from the legacy chatbot (``gsn`` never imports
legacy root code: the engine image does not contain it). A test lifts the original out of
``answer.py`` and compares the two on every corpus URL, so the markers cannot drift apart unnoticed.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime

from gsn.hashing import canonical_json
from gsn.release.inputs import ServiceEntry

# General portal-vs-informational URL classification, copied from answer.py: read-only
# static/FAQ/notice/help pages are references; anything else that is a real web link is treated
# as an actionable application portal.
_INFO_URL_MARKERS = ("/static-pages/", "/pages/static", "/faqs/", "/faq", "/all-faq",
                     "/notices/", "/notice/", "/help/", "/help?")

# An ISO 8601 date, optionally followed by a time of day (and a zone). ASCII digits only: ``\d``
# would also accept Bengali numerals, which are not ISO.
_ISO_DATETIME = re.compile(
    r"([0-9]{4}-[0-9]{2}-[0-9]{2})"
    r"(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?"
)


def url_kind(url: str) -> str:
    """``"portal"`` for an actionable web link, ``"info"`` for a reference page or a non-web source."""
    u = (url or "").lower()
    if not u.startswith("http"):
        return "info"          # non-web reference (e.g. a gazette citation): never an "apply here" URL
    return "info" if any(m in u for m in _INFO_URL_MARKERS) else "portal"


def parse_verified_at(value: object) -> date | None:
    """The date part of an ISO date or date-time string; ``None`` stays ``None``.

    Anything else (another type, an empty or malformed string, an impossible date) raises
    ``ValueError``. The date is the one written in the string: a zone offset is not applied.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected an ISO date or date-time string, got {type(value).__name__}")
    match = _ISO_DATETIME.fullmatch(value)
    if match is None:
        raise ValueError(f"not an ISO date or date-time: {value!r}")
    try:
        if len(value) > len(match.group(1)):
            datetime.fromisoformat(value)  # validates the time of day, which the pattern only shapes
        return date.fromisoformat(match.group(1))
    except ValueError:
        raise ValueError(f"not a valid ISO date or date-time: {value!r}") from None


@dataclass(frozen=True)
class VersionFields:
    """The columns of a record version that are derived from, or copied out of, the record."""

    category: str | None
    subcategory: str | None
    heading: str | None
    lang: str | None
    source_url: str
    url_kind: str
    official: bool
    source_verified: bool
    verified_at: date | None


def version_fields(record: dict, entry: ServiceEntry) -> VersionFields:
    """Columns for ``record`` of the service described by ``entry``.

    ``verified_at`` comes from the record field the manifest maps (``None`` when the service maps no
    field or the record lacks it); an unparseable value raises ``ValueError``. ``official`` is true
    only for a literal ``true``: a missing or odd value never claims official status.
    """
    field = entry.verified_at_field
    return VersionFields(
        category=record.get("category"),
        subcategory=record.get("subcategory"),
        heading=record.get("heading"),
        lang=record.get("lang"),
        source_url=record["source_url"],
        url_kind=url_kind(record["source_url"]),
        official=record.get("official") is True,
        source_verified=not record.get("source_url_unverified", False),
        verified_at=parse_verified_at(record.get(field)) if field else None,
    )


def fields_hash(fields: VersionFields) -> str:
    """SHA-256 (lowercase hex) of the canonical JSON of the derived columns of a version.

    Together with the record's ``content_hash`` it is the identity of a stored version: the same record
    under a different manifest mapping (``verified_at_field``) or a different derivation rule
    (``url_kind``, ``official``, ``source_verified``) is a different version, so a stale derived column
    is never reused. ``verified_at`` enters as an ISO date string, or null. Any change to this payload
    changes every stored identity (the pinned value in the tests says so).
    """
    return hashlib.sha256(canonical_json({
        "category": fields.category,
        "subcategory": fields.subcategory,
        "heading": fields.heading,
        "lang": fields.lang,
        "source_url": fields.source_url,
        "url_kind": fields.url_kind,
        "official": fields.official,
        "source_verified": fields.source_verified,
        "verified_at": fields.verified_at.isoformat() if fields.verified_at is not None else None,
    })).hexdigest()
