"""
freshness/source_store.py — source-keyed freshness state (Freshness V2 §2/§8).

Core principle: **freshness belongs to official sources** — state is keyed by
normalized source URL, never by corpus record id. Corpus editing and vector
updates stay record-level (handled downstream via review + reembed tasks).

Each source entry separates:

  * TRUSTED state — the accepted baseline (hash + snapshot) that corpus
    records were built from; never overwritten by a mere observation
  * CANDIDATE state — the latest observed capture that differs from trusted,
    pending human review; repeated identical candidates update metadata only

All writes are atomic (§16). Failed/suspect/capture-only acquisitions never
destroy or advance trusted state (§6/§7).
"""
from __future__ import annotations

from pathlib import Path

from .paths import (atomic_read_json, atomic_write_json, atomic_write_text,
                    sha256_bytes, sha256_text, slug_for_url)

STORE_FILE = Path(__file__).resolve().parent / "source_store.json"
SNAPSHOT_DIR = Path(__file__).resolve().parent / "source_snapshots"

# source-level statuses
STATUS_OK = "ok"
STATUS_FAILED = "failed_acquisition"
STATUS_BLOCKED = "blocked"
STATUS_ROBOTS = "robots_disallowed"
STATUS_CAPTURE_ONLY = "capture_only_tls_invalid"
STATUS_SUSPECT = "suspect_capture"
STATUS_MALFORMED = "malformed_or_non_http"

# acquisition methods (canonical comparison basis, §5)
METHOD_BROWSER = "verified_browser"      # certificate-validated rendered DOM text
METHOD_STATIC = "verified_static"        # strict verified HTTP text (fallback only)
METHOD_PDF = "direct_pdf_verified"       # verified original bytes
CAPTURE_ONLY = "capture_only_tls_invalid"


def new_entry(normalized_url: str, canonical_url: str) -> dict:
    return {
        "normalized_url": normalized_url,
        "canonical_url": canonical_url,
        "trusted_hash": None,
        "trusted_snapshot": None,
        "trusted_acquisition_method": None,
        "candidate_hash": None,
        "candidate_snapshot": None,
        "candidate_acquisition_method": None,
        "candidate_invalid": False,          # last candidate was ruled invalid
        "last_checked": None,
        "last_successful_check": None,
        "last_changed": None,
        "first_seen": None,
        "verification_status": None,         # verified / capture_only / unreachable
        "status": None,                      # STATUS_* (current failure/skip state)
        "status_reason": None,
        "content_type": None,
        "content_length": None,
    }


def load_store(path: Path = STORE_FILE) -> dict:
    return atomic_read_json(path, default={}) or {}


def save_store(store: dict, path: Path = STORE_FILE) -> None:
    atomic_write_json(path, store)


def get_entry(store: dict, normalized_url: str, canonical_url: str | None = None) -> dict:
    """Get-or-create the state entry for a normalized URL."""
    entry = store.get(normalized_url)
    if entry is None:
        entry = new_entry(normalized_url, canonical_url or normalized_url)
        store[normalized_url] = entry
    return entry


def snapshot_path_for(normalized_url: str, kind: str = "trusted",
                      snapshot_dir: Path | None = None) -> Path:
    """Collision-resistant snapshot path: slug + sha256(normalized_url)[:16] (§17).
    snapshot_dir overrides the module default (hermetic tests inject tmp dirs)."""
    base = Path(snapshot_dir) if snapshot_dir else SNAPSHOT_DIR
    ext = {"trusted_pdf": "pdf"}.get(kind, "txt")
    return base / f"{slug_for_url(normalized_url)}.{kind}.{ext}"


def save_trusted_baseline(entry: dict, content, method: str,
                          content_type: str | None = None,
                          content_hash: str | None = None,
                          snapshot_dir: Path | None = None) -> None:
    """First trusted baseline OR an explicitly reviewed promotion. Writes the
    trusted snapshot atomically and stamps provenance. `content` is HTML/JSON
    text (str) or original document bytes (bytes, e.g. verified PDFs)."""
    is_bytes = isinstance(content, bytes)
    base = Path(snapshot_dir) if snapshot_dir else SNAPSHOT_DIR
    ext = "pdf" if is_bytes else "txt"
    path = base / f"{slug_for_url(entry['normalized_url'])}.trusted.{ext}"
    if is_bytes:
        from .paths import atomic_write_bytes
        atomic_write_bytes(path, content)
    else:
        atomic_write_text(path, content)
    entry["trusted_hash"] = content_hash or (sha256_bytes(content) if is_bytes
                                             else sha256_text(content))
    entry["trusted_snapshot"] = str(path)
    entry["trusted_acquisition_method"] = method
    entry["content_type"] = content_type
    entry["content_length"] = len(content)
    entry["candidate_hash"] = None
    entry["candidate_snapshot"] = None
    entry["candidate_acquisition_method"] = None
    entry["candidate_invalid"] = False
    entry["status"] = None
    entry["status_reason"] = None
    entry["verification_status"] = "verified"


def save_candidate(entry: dict, content, method: str,
                   snapshot_dir: Path | None = None) -> Path:
    """Store a differing observation as the candidate — trusted state untouched.
    str content → .txt snapshot (sha256 of text); bytes → .pdf snapshot
    (sha256 of bytes)."""
    is_bytes = isinstance(content, bytes)
    base = Path(snapshot_dir) if snapshot_dir else SNAPSHOT_DIR
    # 13C: hash-specific name — a later candidate can never overwrite the
    # evidence file a resolved event still references
    digest = sha256_bytes(content) if is_bytes else sha256_text(content)
    ext = "pdf" if is_bytes else "txt"
    path = base / f"{slug_for_url(entry['normalized_url'])}.candidate.{digest[:12]}.{ext}"
    if is_bytes:
        from .paths import atomic_write_bytes
        atomic_write_bytes(path, content)
    else:
        atomic_write_text(path, content)
    entry["candidate_hash"] = digest
    entry["candidate_snapshot"] = str(path)
    entry["candidate_acquisition_method"] = method
    entry["candidate_invalid"] = False
    return path


def promote_candidate(entry: dict) -> None:
    """Review action 'update'/'acknowledge': the candidate is the new trusted
    baseline. The old trusted snapshot file is kept on disk for history."""
    if entry.get("candidate_hash") is None:
        raise ValueError("no candidate to promote")
    entry["trusted_hash"] = entry["candidate_hash"]
    entry["trusted_snapshot"] = entry["candidate_snapshot"]
    entry["trusted_acquisition_method"] = entry.get("candidate_acquisition_method")
    entry["content_length"] = entry.get("candidate_length", entry.get("content_length"))
    entry["candidate_hash"] = None
    entry["candidate_snapshot"] = None
    entry["candidate_acquisition_method"] = None
    entry["candidate_invalid"] = False
    entry["last_changed"] = entry.get("last_checked")
    entry["status"] = None
    entry["status_reason"] = None
    entry["verification_status"] = "verified"


def refresh_unchanged(entry: dict, method: str, verification: str = "verified",
                      canonical_url: str | None = None) -> None:
    """Successful unchanged check (§13): refresh metadata, clear stale failure
    state, keep the trusted hash/snapshot exactly as they are."""
    entry["last_successful_check"] = entry.get("last_checked")
    if canonical_url:
        entry["canonical_url"] = canonical_url
    entry["trusted_acquisition_method"] = method
    entry["verification_status"] = verification
    if entry.get("status") in (STATUS_FAILED, STATUS_BLOCKED, STATUS_SUSPECT):
        entry["status"] = None
        entry["status_reason"] = None


def mark_failure(entry: dict, status: str, reason: str) -> None:
    """Failed/suspect/robots/capture-only acquisition (§6/§7): preserve the
    prior trusted baseline; record the current status."""
    entry["status"] = status
    entry["status_reason"] = reason
    if status in (STATUS_FAILED, STATUS_BLOCKED):
        entry["verification_status"] = "unreachable"
    elif status == STATUS_CAPTURE_ONLY:
        entry["verification_status"] = "capture_only"
    # STATUS_SUSPECT keeps the previous verification_status (the site answered;
    # what it answered was not trustworthy)
