"""Canonical hashing of record content: the identity of a record version.

A record version is identified by ``content_hash(record)``. The hash must therefore be stable
forever: the same record always serialises to the same bytes, whatever the key order, and Bangla text
is hashed as UTF-8 rather than as ``\\uXXXX`` escapes. Any change to this format changes every stored
identity, so it must come with a new ``HASH_VERSION``.
"""
from __future__ import annotations

import hashlib
import json

HASH_VERSION = 1


def canonical_json(record: dict) -> bytes:
    """Serialise ``record`` as compact, key-sorted JSON in UTF-8 (non-ASCII characters kept literal)."""
    return json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def content_hash(record: dict) -> str:
    """SHA-256 (lowercase hex) of the record's canonical JSON."""
    return hashlib.sha256(canonical_json(record)).hexdigest()


def text_hash(text: str) -> str:
    """SHA-256 (lowercase hex) of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
