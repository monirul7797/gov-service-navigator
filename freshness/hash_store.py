"""
freshness/hash_store.py — persisted {record_id: {source_url, content_hash,
last_checked, last_changed}} store (Part A §A4).

Keyed by the record's stable `id` field, NOT by URL: several records share
one source_url (e.g. 13 NID fee records all cite /nid-pub/fees), so a URL key
would collapse them into one check.

The store file (freshness/freshness_store.json) is git-tracked and small —
it holds hashes and timestamps only, never page text.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_store(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_store(store: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def get_hash(store: dict, record_id: str) -> str | None:
    rec = store.get(record_id)
    if not rec:
        return None
    return rec.get("content_hash")


def update_hash(store: dict, record_id: str, source_url: str,
                new_hash: str, changed: bool) -> dict:
    """Record the latest observed hash for a record. `changed=False` on first
    baseline (nothing to compare against yet); last_changed is stamped only
    when a previously-hashed record is seen with a different hash."""
    rec = store.get(record_id) or {}
    rec["source_url"] = source_url
    rec["content_hash"] = new_hash
    rec["last_checked"] = _now_iso()
    if changed:
        rec["last_changed"] = _now_iso()
    elif "last_changed" not in rec:
        rec["last_changed"] = None
    rec.pop("status", None)          # a successful hash clears any prior skip state
    rec.pop("status_reason", None)
    store[record_id] = rec
    return rec


def mark_status(store: dict, record_id: str, source_url: str,
                status: str, reason: str | None = None) -> dict:
    """Persist a non-fetch outcome (needs_manual_screenshot / skipped) so the
    report can enumerate blocked records without re-deriving them each run."""
    rec = store.get(record_id) or {}
    rec["source_url"] = source_url
    rec["status"] = status
    rec["status_reason"] = reason
    rec["last_checked"] = _now_iso()
    store[record_id] = rec
    return rec
