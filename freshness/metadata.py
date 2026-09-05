"""
freshness/metadata.py — single source of truth for embedding metadata per
service (Freshness V2 §11).

Parity by construction: the freshness single-record upsert imports the SAME
metadata builder the full embed script uses, so a re-embedded record's
metadata is always equivalent to a full re-embed.

Adding a service (e.g. visa in Phase 4): implement `to_metadata_visa` in its
embed script and register it here.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SUPPORTED = ("nid", "epassport", "ereturn", "bdris")


def build_metadata(service: str, record: dict) -> dict:
    """Return the exact metadata the service's full embed path would store."""
    if service == "nid":
        from embed_nid import to_metadata_nid
        return to_metadata_nid(record)
    if service == "epassport":
        from embed_epassport import to_metadata_epassport
        return to_metadata_epassport(record)
    if service == "ereturn":
        from embed_ereturn import to_metadata
        return to_metadata(record)
    if service == "bdris":
        from embed_bdris import to_metadata
        return to_metadata(record)
    if service == "visa":
        # designed-for-later: wire to embed_visa.to_metadata_visa in Phase 4
        raise NotImplementedError(
            "visa embedding is not built yet (Part B §B6); metadata builder "
            "will be added with embed_visa.py")
    raise ValueError(f"unknown service {service!r}; expected one of {SUPPORTED}")
