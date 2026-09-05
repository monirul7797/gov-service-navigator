"""
freshness/run_freshness_check.py — Freshness V2 orchestrator (§29/§14).

Two monitoring problems, one CLI:

  1. Known-source drift (default): every unique source URL behind the corpus
     is acquired EXACTLY ONCE per run through the shared acquisition layer,
     passed through the capture-quality quarantine gate, and compared to its
     trusted baseline. Changes create ONE source-level pending event listing
     all impacted records — the corpus itself is never touched.

  2. Source discovery (--discovery): curated official hubs (source_registry.json)
     are crawled with the bounded Phase 3.5 crawler; the verified inventory is
     compared against the previous trusted inventory; new/missing/removed/
     reappeared/changed resources produce review-queue events.

TRUE dry run (--dry-run): full computation, ZERO persistent writes — state
files are byte-for-byte untouched (§14).

Semantics after a change: update / acknowledge / invalid / skip — see
freshness/review.py.

One freshness process/operator at a time (documented assumption, §16).
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acquisition.transport import PlaywrightRenderer, RequestsTransport  # noqa: E402
from freshness import source_store as S                                   # noqa: E402
from freshness.known_monitor import KnownSourceMonitor                    # noqa: E402
from freshness.mapping import build_source_mapping                        # noqa: E402
from freshness.paths import (FRESHNESS_DIR, atomic_write_text,            # noqa: E402
                             sha256_text)

SERVICES = ["nid", "epassport", "ereturn", "bdris", "visa"]
STORE_PATH = FRESHNESS_DIR / "source_store.json"
EVENTS_PATH = FRESHNESS_DIR / "pending_source_events.json"
INVENTORY_PATH = FRESHNESS_DIR / "discovery_inventory.json"
SOURCE_REVIEW_PATH = FRESHNESS_DIR / "pending_source_review.json"


def corpus_paths_for(services: list[str]) -> dict[str, Path]:
    return {svc: Path(__file__).resolve().parents[1] / "data" / "chunks" / f"{svc}.jsonl"
            for svc in services}


def run_known_sources(services: list[str], record_ids: set[str] | None,
                      dry_run: bool, transport, renderer) -> dict:
    monitor = KnownSourceMonitor(
        corpus_paths=corpus_paths_for(services), transport=transport, renderer=renderer,
        store_path=STORE_PATH, snapshot_dir=FRESHNESS_DIR / "source_snapshots",
        events_path=EVENTS_PATH, rate_limit_seconds=1.0, dry_run=dry_run,
        record_ids=record_ids)
    summary = monitor.run()
    summary["services"] = services
    return summary


def run_discovery(services: list[str], dry_run: bool, transport, renderer) -> dict:
    from freshness.discovery import (INVENTORY_PATH, REVIEW_QUEUE_PATH,
                                     crawl_seed_inventory, load_registry,
                                     reconcile_seed_inventory)
    registry = load_registry()
    import json
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8")) \
        if (not dry_run and INVENTORY_PATH.exists()) else {}
    queue = json.loads(REVIEW_QUEUE_PATH.read_text(encoding="utf-8")) \
        if (not dry_run and REVIEW_QUEUE_PATH.exists()) else []

    summary = {"seeds_checked": 0, "verified_inventories": 0, "seed_failures": 0,
               "new_sources": 0, "new_pdfs": 0, "missing": 0, "removed": 0,
               "reappeared": 0, "link_changes": 0, "known_changed": 0,
               "pending_reviews": 0}
    corpus_map = build_source_mapping(corpus_paths_for(services))

    for service in services:
        svc_cfg = (registry.get("services") or {}).get(service) or {}
        for seed in svc_cfg.get("discovery_seeds", []):
            summary["seeds_checked"] += 1
            prev = inventory.get(service, {}).get(seed["url"])
            missing_counts = (prev or {}).get("missing_counts", {})
            try:
                evidence_dir = Path(__file__).resolve().parents[1] / "data" / "evidence" / f"discovery_{service}"
                if dry_run:
                    # section 14: a dry run persists nothing - throwaway evidence dir
                    import tempfile
                    evidence_dir = Path(tempfile.mkdtemp(prefix="discovery_dry_"))
                crawled = crawl_seed_inventory(
                    seed, transport=transport, renderer=renderer,
                    evidence_dir=evidence_dir)
            except Exception as exc:  # noqa: BLE001 — a dead hub must not crash the run
                crawled = {"ok": False, "resources": {},
                           "failures": [{"reason": str(exc)[:150]}]}
            merged = reconcile_seed_inventory(
                prev, crawl_failed=not crawled["ok"],
                current_resources=crawled["resources"], seed_url=seed["url"],
                missing_counts=missing_counts)
            if merged["seed_failed"]:
                summary["seed_failures"] += 1
                continue
            summary["verified_inventories"] += 1
            # annotate corpus-known resources (computed at runtime, §20)
            for url, res in merged["resources"].items():
                res["in_corpus"] = url in corpus_map
            inventory.setdefault(service, {})[seed["url"]] = {
                "purpose": seed.get("purpose", ""),
                "last_verified_crawl": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "resources": merged["resources"],
                "missing_counts": missing_counts,
            }
            for ev in merged["events"]:
                if ev["type"] == "new_source":
                    summary["new_sources"] += 1
                    if ev.get("resource_type") == "pdf":
                        summary["new_pdfs"] += 1
                elif ev["type"] == "source_missing":
                    summary["missing"] += 1
                elif ev["type"] == "source_removed":
                    summary["removed"] += 1
                elif ev["type"] == "source_reappeared":
                    summary["reappeared"] += 1
                elif ev["type"] == "source_link_changed":
                    summary["link_changes"] += 1
                elif ev["type"] == "known_source_changed":
                    summary["known_changed"] += 1
                if ev.get("status") == "pending" and ev["type"] in (
                        "new_source", "known_source_changed"):
                    ev.setdefault("service", service)
                    queue = [q for q in queue
                             if not (q.get("url") == ev["url"] and q.get("status") == "pending")]
                    queue.append(ev)

    if not dry_run:
        import json
        import freshness.paths as P
        P.atomic_write_json(INVENTORY_PATH, inventory)
        P.atomic_write_json(REVIEW_QUEUE_PATH, queue)
    summary["pending_reviews"] = len([q for q in queue if q.get("status") == "pending"])
    return summary


def write_report(known: dict | None, discovery: dict | None, dry_run: bool) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# Freshness V2 report — {today}" + ("  (DRY RUN — nothing persisted)" if dry_run else ""), ""]

    if known is not None:
        lines += ["## Known Source Monitoring", "",
                  f"- services: {', '.join(known.get('services', []))}",
                  f"- records mapped: {known.get('records_mapped')}",
                  f"- unique known sources: {known.get('unique_sources')}",
                  f"- sources actually fetched: {known.get('sources_fetched')}",
                  f"- new baselines: {known.get('new_baselines')}",
                  f"- unchanged: {known.get('unchanged')}",
                  f"- changed/pending review: {known.get('changed_pending')}",
                  f"- suspect captures (quarantined): {known.get('suspect_captures')}",
                  f"- blocked/robots: {known.get('blocked_or_robots')}",
                  f"- capture-only (unverified): {known.get('capture_only')}",
                  f"- failed/unreachable: {known.get('failed_or_unreachable')}",
                  f"- acquisition-method transitions: {known.get('method_transitions')}",
                  f"- records with no usable source_url: {known.get('no_source_url_records')}", ""]
        import json
        ev_list = json.loads(EVENTS_PATH.read_text(encoding="utf-8")) if EVENTS_PATH.exists() else []
        pending = [e for e in ev_list if e.get("status") == "pending"]
        if pending:
            lines += ["### Changed sources awaiting review", "",
                      "| source | impacted records | method | detected |", "|---|---|---|---|"]
            for e in pending:
                rec_ids = ", ".join(f"`{i['record_id']}`" for i in e.get("impacted_records", [])[:6])
                lines.append(f"| {e['normalized_url']} | {rec_ids} | {e.get('acquisition_method')} | {e.get('detected_at')} |")
            lines += ["", "Review: `python freshness/review.py` "
                      "(actions: update / acknowledge / invalid / skip)", ""]

    if discovery is not None:
        lines += ["## Source Discovery", "",
                  f"- discovery seeds checked: {discovery.get('seeds_checked')}",
                  f"- verified inventories: {discovery.get('verified_inventories')}",
                  f"- seed failures (previous inventory preserved): {discovery.get('seed_failures')}",
                  f"- new official resources: {discovery.get('new_sources')}",
                  f"- new PDFs/documents: {discovery.get('new_pdfs')}",
                  f"- resources missing: {discovery.get('missing')}",
                  f"- resources removed: {discovery.get('removed')}",
                  f"- resources reappeared: {discovery.get('reappeared')}",
                  f"- hub link changes: {discovery.get('link_changes')}",
                  f"- same-URL content changes: {discovery.get('known_changed')}",
                  f"- pending source reviews: {discovery.get('pending_reviews')}", "",
                  "Review: `python freshness/review.py --discovery` "
                  "(actions: relevant / not_relevant / duplicate / unsafe_or_excluded / skip)", ""]

    lines += ["This run detects and surfaces drift only — no corpus .jsonl and no "
              "data/vectordb file was written.", ""]
    out = FRESHNESS_DIR / f"freshness_report_{today}.md"
    atomic_write_text(out, "\n".join(lines))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Freshness V2: source drift + discovery monitoring")
    ap.add_argument("--services", nargs="+", default=SERVICES, choices=SERVICES)
    ap.add_argument("--record-ids", default=None,
                    help="comma-separated record ids: check only their sources")
    ap.add_argument("--dry-run", action="store_true",
                    help="acquire and compare, but persist NOTHING (§14)")
    ap.add_argument("--discovery", action="store_true",
                    help="also run source-discovery monitoring over curated hubs")
    ap.add_argument("--no-known-sources", action="store_true",
                    help="skip known-source drift check (discovery only)")
    args = ap.parse_args()

    record_ids = set(args.record_ids.split(",")) if args.record_ids else None
    known = discovery = None

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        renderer = PlaywrightRenderer(browser).open()
        try:
            transport = RequestsTransport()
            if not args.no_known_sources:
                known = run_known_sources(args.services, record_ids, args.dry_run,
                                          transport, renderer)
                print("known sources: " + " ".join(f"{k}={v}" for k, v in known.items()
                                                   if k != "services"))
            if args.discovery:
                discovery = run_discovery(args.services, args.dry_run, transport, renderer)
                print("discovery: " + " ".join(f"{k}={v}" for k, v in discovery.items()))
        finally:
            renderer.close()
            browser.close()

    if args.dry_run:
        print("DRY RUN: no state was persisted.")
    else:
        report = write_report(known, discovery, args.dry_run)
        print(f"report: {report}")


if __name__ == "__main__":
    main()
