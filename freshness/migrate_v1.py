"""
freshness/migrate_v1.py — one-time migration of record-level V1 freshness
state into source-keyed V2 state (Freshness V2 §3; V2.1 B9/B10).

Safety rules:
  * back up ALL V1 operational state locally first (gitignored);
  * group V1 record entries by normalized source URL;
  * collapse a URL into ONE source baseline ONLY if every successful record
    hash agrees AND the corresponding last_known_text snapshots hash to the
    same value;
  * B10: TWO-PHASE — Phase A validates and builds the entire plan in memory;
    ANY conflict means ZERO V2 writes. Phase B commits (backup → snapshots →
    store → report) only after global validation succeeds;
  * B9: stored-count metrics reconcile with the resulting store
    (baseline_sources_stored + status_only_sources_stored; records without a
    usable URL are counted separately and never migrated);
  * V1 files are KEPT as migration evidence — never deleted;
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


def backup_v1_state(backup_root: Path = BACKUP_DIR) -> Path:
    """Local backup of generated operational state (§3) — gitignored."""
    dest = Path(backup_root) / datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for f in (V1_STORE, V1_QUEUE, V1_APPROVED):
        if f.exists():
            shutil.copy2(f, dest / f.name)
    if V1_SNAPSHOTS.exists():
        shutil.copytree(V1_SNAPSHOTS, dest / "last_known_text", dirs_exist_ok=True)
    return dest


def migrate(*, report_only: bool = False,
            v1_store: Path = V1_STORE, v1_snapshots: Path = V1_SNAPSHOTS,
            v2_store: Path = V2_STORE,
            snapshots_dir: Path = FRESHNESS_DIR / "source_snapshots",
            backup_root: Path = BACKUP_DIR) -> dict:
    """B10: two-phase migration.

    Phase A — validate and build the ENTIRE migration plan in memory (grouped
    hashes, snapshot agreement, normalized URLs, status-only entries,
    conflicts). No V2 writes happen in this phase.
    Phase B — only after global validation succeeds: backup, write V2
    snapshots, atomically write the source store, write the report.

    B9: metrics reconcile with what is ACTUALLY stored —
    baseline_sources_stored + status_only_sources_stored; records without a
    usable URL are counted separately (records_without_usable_url).
    """
    if not Path(v1_store).exists():
        raise SystemExit(f"no V1 store at {v1_store} — nothing to migrate")

    v1 = json.loads(Path(v1_store).read_text(encoding="utf-8"))
    report = {"checked_record_entries": len(v1), "urls": {}, "conflicts": [],
              "baseline_sources_stored": 0, "status_only_sources_stored": 0,
              "records_without_usable_url": 0}

    # ---- Phase A: validate + plan entirely in memory -----------------------
    groups: dict[str, list] = {}
    no_url_records = 0
    for record_id, rec in v1.items():
        url = rec.get("source_url") or ""
        norm = normalize_url(url) if url.startswith("http") else ""
        if not norm:
            no_url_records += 1
        groups.setdefault(norm, []).append((record_id, rec))
    report["records_without_usable_url"] = no_url_records

    plan: dict[str, dict] = {}       # norm -> {"entry":..., "snapshot_text":...}
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
            if norm:
                plan[norm] = {"entry": entry, "snapshot_text": None}
            report["urls"][norm or "(no usable url)"] = url_report
            continue

        hashes = {rec["content_hash"] for _, rec in successful}
        if len(hashes) != 1:
            # §3: disagreement → conflict, never guess
            detail = {rid: rec["content_hash"][:12] for rid, rec in successful}
            report["conflicts"].append({"url": norm, "hashes": detail})
            url_report["status"] = "CONFLICT — migration stopped for this URL"
            report["urls"][norm] = url_report
            continue
        trusted_hash = successful[0][1]["content_hash"]

        # snapshot agreement: every record snapshot must hash to the same value
        snapshot_text = None
        for rid, _rec in successful:
            snap = Path(v1_snapshots) / f"{rid}.txt"
            if not snap.exists():
                report["conflicts"].append({"url": norm, "issue": f"missing snapshot {snap.name}"})
                snapshot_text = None
                break
            text = snap.read_text(encoding="utf-8")
            if sha256_text(text) != trusted_hash:
                report["conflicts"].append(
                    {"url": norm, "issue": f"snapshot hash mismatch for {snap.name}"})
                snapshot_text = None
                break
            snapshot_text = text
        if snapshot_text is None:
            url_report["status"] = "CONFLICT — snapshot issue"
            report["urls"][norm] = url_report
            continue

        any_rec = successful[0][1]
        entry = {
            "normalized_url": norm, "canonical_url": any_rec.get("source_url"),
            "trusted_hash": trusted_hash,
            "trusted_snapshot": None,
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
        plan[norm] = {"entry": entry, "snapshot_text": snapshot_text}
        url_report["status"] = f"baselined ({len(successful)} records collapsed)"
        report["urls"][norm] = url_report

    # ---- Phase B: commit only after global validation succeeds --------------
    if not report_only:
        if report["conflicts"]:
            # B10: a conflict anywhere means NOTHING is written to V2 state
            print("MIGRATION HALTED — hash/snapshot conflicts detected; "
                  "zero V2 writes performed.")
            print(json.dumps(report["conflicts"], indent=2))
            sys.exit(1)
        backup = backup_v1_state(backup_root)
        from freshness.paths import atomic_write_text
        for norm, item in plan.items():
            if item["snapshot_text"] is not None:
                snap_path = Path(snapshots_dir) / f"{slug_for_url(norm)}.trusted.txt"
                atomic_write_text(snap_path, item["snapshot_text"])
                item["entry"]["trusted_snapshot"] = str(snap_path)
        new_store = json.loads(Path(v2_store).read_text(encoding="utf-8")) \
            if Path(v2_store).exists() else {}
        new_store.update({norm: item["entry"] for norm, item in plan.items()})
        atomic_write_json(Path(v2_store), new_store)
        report["backup_dir"] = str(backup)

        # B9: reconcile stored-count metrics against the resulting store
        stored = json.loads(Path(v2_store).read_text(encoding="utf-8"))
        report["baseline_sources_stored"] = len([e for e in stored.values()
                                                 if e.get("trusted_hash")])
        report["status_only_sources_stored"] = len([e for e in stored.values()
                                                    if not e.get("trusted_hash")])
        planned_baseline = len([i for i in plan.values() if i["snapshot_text"] is not None])
        planned_status = len(plan) - planned_baseline
        assert report["baseline_sources_stored"] == planned_baseline, "baseline metric mismatch"
        assert report["status_only_sources_stored"] == planned_status, "status-only metric mismatch"
        print(f"migrated: {report['baseline_sources_stored']} baselined sources, "
              f"{report['status_only_sources_stored']} status-only sources, "
              f"{report['records_without_usable_url']} records without usable url "
              f"(counted separately, not migrated)")
        print(f"v1 state preserved (backup: {backup})")
    else:
        report["baseline_sources_stored"] = len([i for i in plan.values()
                                                 if i["snapshot_text"] is not None])
        report["status_only_sources_stored"] = len(plan) - report["baseline_sources_stored"]
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
