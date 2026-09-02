"""
freshness/diff.py — hash + text diff between last-known and newly-fetched
content (Part A §A5).

Per record we keep:
  * a SHA-256 of the extracted text, in freshness_store.json
  * the extracted text itself, in freshness/last_known_text/<record_id>.txt
    (gitignored — operational cache). Without it, a hash mismatch could not
    be turned into a human-readable diff, since the store holds only hashes.
On mismatch, difflib produces a unified diff which is appended to the
pending-review queue (a plain JSON list — no DB).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re

REPO_ROOT = r"C:\Users\Monirul\Documents\Structured Data\NID\gov-service-navigator\gov-service-navigator"
LAST_KNOWN_DIR = os.path.join(REPO_ROOT, "freshness", "last_known_text")
QUEUE_PATH = os.path.join(REPO_ROOT, "freshness", "pending_review.json")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _snapshot_path(record_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", record_id)
    return os.path.join(LAST_KNOWN_DIR, f"{safe}.txt")


def load_last_known_text(record_id: str) -> str | None:
    path = _snapshot_path(record_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def save_last_known_text(record_id: str, text: str) -> None:
    os.makedirs(LAST_KNOWN_DIR, exist_ok=True)
    tmp = _snapshot_path(record_id) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, _snapshot_path(record_id))


def make_unified_diff(old_text: str, new_text: str, fromfile: str = "last_known",
                      tofile: str = "newly_fetched") -> str:
    """Human-readable unified diff; empty string when texts are equal."""
    diff = difflib.unified_diff(
        old_text.splitlines(), new_text.splitlines(),
        fromfile=fromfile, tofile=tofile, lineterm="", n=3,
    )
    return "\n".join(diff)


# --- pending-review queue (plain JSON list, spec §A5) -----------------------

def load_queue(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_queue(queue: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(queue, f, ensure_ascii=False, indent=2)


def append_pending(queue: list[dict], entry: dict, queue_path: str) -> list[dict]:
    queue.append(entry)
    save_queue(queue, queue_path)
    return queue


def is_pending(record_id: str, queue: list[dict]) -> bool:
    return any(e.get("record_id") == record_id for e in queue)
