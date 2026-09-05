"""
freshness/mapping.py — corpus → normalized-source mapping (Freshness V2 §2/§4).

Builds, at runtime, the mapping:

    normalized source URL → [{service, record_id, heading}, ...]

from the current corpora. Record lists are NOT persisted in source state
(§2: computed safely from current corpora, so they can never go stale).

Generic reader: every service schema carries id / source_url / heading / text,
so one reader covers nid, epassport, ereturn, bdris (and visa when built).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from acquisition.urls import PDF_RE, normalize_url  # noqa: E402

from freshness import fetch as _legacy_fetch  # noqa: E402  (for _clean_url semantics)


def _clean(url: str | None) -> str | None:
    """Fetchable-URL gate: http(s) only, no raw whitespace/commas (mirrors the
    Phase-1 rule, including the comma-joined ereturn value)."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    if any(c in url for c in ",; \t"):
        return None
    return url


def read_records(corpus_path: Path, service: str) -> list[dict]:
    out = []
    with open(corpus_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                r["_service"] = service
                out.append(r)
    return out


def build_source_mapping(corpus_paths: dict[str, Path],
                         record_ids: set[str] | None = None) -> dict:
    """normalized_url -> {canonical_url, is_pdf, records: [{service, record_id, heading}]}.

    Records with no usable source_url are returned under the special key
    "" so reports can account for them; they are never fetched.
    """
    mapping: dict[str, dict] = {}
    for service, path in corpus_paths.items():
        for r in read_records(Path(path), service):
            if record_ids is not None and r["id"] not in record_ids:
                continue
            clean = _clean(r.get("source_url"))
            if clean is None:
                entry = mapping.setdefault("", {"canonical_url": "", "is_pdf": False,
                                                "records": []})
                entry["records"].append({"service": service, "record_id": r["id"],
                                         "heading": r.get("heading", "")})
                continue
            norm = normalize_url(clean)
            entry = mapping.setdefault(norm, {"canonical_url": clean,
                                              "is_pdf": bool(PDF_RE.search(norm)),
                                              "records": []})
            entry["records"].append({"service": service, "record_id": r["id"],
                                     "heading": r.get("heading", "")})
    return mapping
