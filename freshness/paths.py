"""
freshness/paths.py — repo-relative paths, atomic writes, collision-resistant
snapshot identity (Freshness V2 §14/§15/§16/§17).

One freshness process/operator at a time (documented assumption, §16):
no locking in this phase.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

# freshness/ lives directly under the repo root — resolve portably.
REPO_ROOT = Path(__file__).resolve().parents[1]
FRESHNESS_DIR = REPO_ROOT / "freshness"
DATA_DIR = REPO_ROOT / "data"


def atomic_write_text(path: Path, text: str) -> None:
    """Temp file + atomic replace, for snapshots and reports (§16)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    os.replace(tmp, path)


def atomic_write_json(path: Path, data) -> None:
    """Temp file + atomic replace for all operational JSON state (§16)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def atomic_read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def slug_for_url(normalized_url: str) -> str:
    """Collision-resistant snapshot identity (§17): readable slug + short
    sha256 of the normalized URL. Distinct normalized URLs cannot collide."""
    digest = hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", normalized_url.split("//", 1)[-1]).strip("_")[:48]
    return f"{slug}_{digest}" if slug else digest


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_read_text(path: Path) -> str | None:
    path = Path(path)
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return f.read()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Binary atomic write — for verified PDF/document snapshots (§27)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
