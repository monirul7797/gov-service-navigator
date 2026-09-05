"""
freshness/migrate_v1.py — one-time migration of record-level V1 freshness
state into source-keyed V2 state (Freshness V2 §3).

Safety rules:
  * back up ALL V1 operational state locally first (gitignored);
  * group V1 record entries by normalized source URL;
  * collapse a URL into ONE source baseline ONLY if every successful record
    hash agrees AND the corresponding last_known_text snapshots hash to the
    same value;
  * any disagreement → STOP for that URL, report it, never guess;
  * V1 files are KEPT as migration evidence/back-up — never deleted;
  * V1 baselines were rendered-DOM text hashes → trusted method is
    'verified_browser' (§5 canonical method), preserving hash continuity.

Run:
    python freshness/migrate_v1.py            # migrate + write source_store.json
    python freshness/migrate_v1.py --report   # audit only, write nothing
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from acquisition.urls import normalize_url                     # noqa: E402
from freshness.paths import (FRESHNESS_DIR, atomic_write_json,  # noqa: E402
                             sha256_text, slug_for_url)

V1_STORE = FRESHNESS_DIR / "freshness_store.json"
V1_SNAPSHOTS = FRESHNESS_DIR / "last_known_text"
V1_QUEUE = FRESHNESS_DIR / "pending_review.json"
V1_APPROVED = FRESHNESS_DIR / "approved_for_reembed.json"
V2_STORE = FRESHNESS_DIR / "source_store.json"
BACKUP_DIR = FRESHNESS_DIR / "backup_v1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def backup_v1_state() -> Path:
    """Local backup of generated operational state (§3) — gitignored."""
    dest = BACKUP_DIR / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for f in (V1_STORE, V1_QUEUE, V1_APPROVED):
        if f.exists():
            shutil.copy2(f, dest / f.name)
    if V1_SNAPSHOTS.exists():
        shutil.copytree(V1_SNAPSHOTS, dest / "last_known_text", dirs_exist_ok=True)
    return dest


def migrate(*, report_only: bool = False) -> dict:
    if not V1_STORE.exists():
        raise SystemExit(f"no V1 store at {V1_STORE} — nothing to migrate")

    v1 = json.loads(V1_STORE.read_text(encoding="utf-8"))
    report = {"checked_record_entries": len(v1), "urls": {}, "conflicts": [],
              "baseline_sources": 0, "status_only_sources": 0}

    # group successful record entries by normalized URL
    groups: dict[str, list] = {}
    for record_id, rec in v1.items():
        url = rec.get("source_url") or ""
        norm = normalize_url(url) if url.startswith("http") else ""
        groups.setdefault(norm, []).append((record_id, rec))

    new_store = json.loads(V2_STORE.read_text(encoding="utf-8")) if V2_STORE.exists() else {}

    for norm, entries in sorted(groups.items()):
        url_report = {"record_ids": [e[0] for e in entries], "status": None}
        successful = [(rid, rec) for rid, rec in entries if rec.get("content_hash")]
        if not successful:
            # blocked/skipped-only URL: carry the status forward, no baseline
            any_rec = entries[0][1]
            entry = {
                "normalized_url": norm, "canonical_url": any_rec.get("source_url"),
                "trusted_hash": None, "trusted_snapshot": None,
                "trusted_acquisition_method": None,
                "candidate_hash": None, "candidate_snapshot": None,
                "candidate_acquisition_method": None, "candidate_invalid": False,
                "first_seen": any_rec.get("last_checked"), "last_checked": any_rec.get("last_checked"),
                "last_successful_check": None, "last_changed": None,
                "verification_status": "unreachable",
                "status": any_rec.get("status") or "unknown",
                "status_reason": any_rec.get("status_reason"),
                "content_type": None, "content_length": None,
            }
            url_report["status"] = f"status-only ({entry['status']})"
            report["status_only_sources"] += 1
            if not report_only and norm:
                new_store.setdefault(norm, entry)
            report["urls"][norm or "(no url)"] = url_report
            continue

        hashes = {rec["content_hash"] for _, rec in successful}
        if len(hashes) != 1:
            # §3: disagreement → STOP for this URL, report, never guess
            detail = {rid: rec["content_hash"][:12] for rid, rec in successful}
            report["conflicts"].append({"url": norm, "hashes": detail})
            url_report["status"] = "CONFLICT — migration stopped for this URL"
            report["urls"][norm] = url_report
            continue
        trusted_hash = successful[0][1]["content_hash"]

        # snapshot agreement: every record snapshot must hash to the same value
        snapshot_ok = True
        snapshot_text = None
        for rid, _rec in successful:
            snap = V1_SNAPSHOTS / f"{rid}.txt"
            if not snap.exists():
                snapshot_ok = False
                report["conflicts"].append({"url": norm, "issue": f"missing snapshot {snap.name}"})
                snapshot_ok = "missing"
                break
            text = snap.read_text(encoding="utf-8")
            if sha256_text(text) != trusted_hash:
                snapshot_ok = False
                report["conflicts"].append(
                    {"url": norm, "issue": f"snapshot hash mismatch for {snap.name}"})
                break
            snapshot_text = text
        if snapshot_ok is not True:
            url_report["status"] = f"CONFLICT — snapshot issue ({snapshot_ok})"
            report["urls"][norm] = url_report
            continue

        any_rec = successful[0][1]
        entry = {
            "normalized_url": norm, "canonical_url": any_rec.get("source_url"),
            "trusted_hash": trusted_hash,
            "trusted_snapshot": None,   # written below (hash-verified copy)
            "trusted_acquisition_method": "verified_browser",  # V1 = rendered DOM
            "candidate_hash": None, "candidate_snapshot": None,
            "candidate_acquisition_method": None, "candidate_invalid": False,
            "first_seen": min((r.get("last_checked") or "") for _, r in successful) or None,
            "last_checked": max((r.get("last_checked") or "") for _, r in successful),
            "last_successful_check": max((r.get("last_checked") or "") for _, r in successful),
            "last_changed": any((r.get("last_changed") for _, r in successful)),
            "verification_status": "verified",
            "status": None, "status_reason": None,
            "content_type": "text/html",
            "content_length": len(snapshot_text or ""),
        }
        if not report_only and norm:
            from freshness.paths import atomic_write_text
            snap_path = FRESHNESS_DIR / "source_snapshots" / f"{slug_for_url(norm)}.trusted.txt"
            atomic_write_text(snap_path, snapshot_text)
            entry["trusted_snapshot"] = str(snap_path)
            new_store[norm] = entry
        report["baseline_sources"] += 1
        url_report["status"] = f"baselined ({len(successful)} records collapsed)"
        report["urls"][norm] = url_report

    if not report_only:
        if report["conflicts"]:
            print("MIGRATION HALTED — hash/snapshot conflicts detected; nothing written.")
            print(json.dumps(report["conflicts"], indent=2))
            sys.exit(1)
        backup = backup_v1_state()
        atomic_write_json(V2_STORE, new_store)
        report["backup_dir"] = str(backup)
        print(f"migrated: {report['baseline_sources']} baselined sources, "
              f"{report['status_only_sources']} status-only sources")
        print(f"v1 state preserved (backup: {backup})")
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="V1 record-level → V2 source-level migration")
    ap.add_argument("--report", action="store_true", help="audit only, write nothing")
    args = ap.parse_args()
    report = migrate(report_only=args.report)
    if not args.report:
        out = FRESHNESS_DIR / f"migration_report_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
        atomic_write_json(out, report)
        print(f"migration report: {out}")


if __name__ == "__main__":
    main()
