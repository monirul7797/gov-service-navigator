"""
freshness/review.py — human review gates for Freshness V2.

Two queues, one CLI:

1. Source-change events (pending_source_events.json) — actions per §9:
     update       the change is legitimate AND corpus records need editing:
                  select which impacted record ids get edit/reembed tasks
                  (the manual .jsonl edit itself is NOT automated, §A1);
                  promotes the candidate source state to trusted
     acknowledge  source changed legitimately, corpus needs no edit:
                  candidate promoted to trusted, no record tasks
     invalid      the capture is not a valid representation (maintenance page,
                  bot wall, bad render): trusted baseline kept; the same
                  candidate will not re-alert
     skip         no decision; event stays pending

2. Discovery candidates (pending_source_review.json, --discovery) — actions
   per §23:
     relevant            worth future corpus extraction (does NOT create rows)
     not_relevant        official but irrelevant; suppressed from alerts
     duplicate           already represented; suppressed from alerts
     unsafe_or_excluded  PII / login / case-specific material; reason kept
     skip                leave unresolved

This CLI NEVER writes to data/vectordb/ or corpus .jsonl. Record reembedding
happens only via `python freshness/reembed.py --execute` after the manual
corpus edit, and only for tasks created by an 'update' action.

Usage:
    python freshness/review.py                  # source-change events
    python freshness/review.py --list           # list only
    python freshness/review.py --discovery      # discovery candidates
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from freshness.paths import FRESHNESS_DIR                        # noqa: E402
from freshness.review_actions import (apply_source_action,        # noqa: E402
                                      load_events)
from freshness.discovery import INVENTORY_PATH, REVIEW_QUEUE_PATH  # noqa: E402
from freshness.paths import atomic_read_json                      # noqa: E402

EVENTS_PATH = FRESHNESS_DIR / "pending_source_events.json"
DIFF_PREVIEW_LINES = 14


def _show_event(e: dict) -> None:
    print("=" * 78)
    print(f"event_id : {e['event_id']}")
    print(f"type     : {e.get('type')}  status: {e.get('status')}")
    print(f"source   : {e.get('normalized_url')}")
    print(f"method   : {e.get('acquisition_method')}  detected: {e.get('detected_at')}")
    print(f"trusted  : {(e.get('trusted_hash') or '')[:16]}  "
          f"candidate: {(e.get('candidate_hash') or '')[:16]}")
    impacted = e.get("impacted_records") or []
    print(f"impacted : {len(impacted)} record(s)")
    for im in impacted[:8]:
        print(f"   - [{im['service']}] {im['record_id']}  {im.get('heading', '')[:60]}")
    if len(impacted) > 8:
        print(f"   … and {len(impacted) - 8} more")
    snap = e.get("candidate_snapshot")
    if snap and Path(snap).exists() and Path(e.get("trusted_snapshot", "")).exists():
        from difflib import unified_diff
        old = Path(e["trusted_snapshot"]).read_text(encoding="utf-8").splitlines()
        new = Path(snap).read_text(encoding="utf-8").splitlines()
        diff = list(unified_diff(old, new, "trusted", "candidate", lineterm="", n=2))
        print("-" * 78)
        print("\n".join(diff[:DIFF_PREVIEW_LINES]))
        if len(diff) > DIFF_PREVIEW_LINES:
            print(f"… ({len(diff) - DIFF_PREVIEW_LINES} more diff lines)")
    print("-" * 78)


def _prompt_action(e: dict) -> str | None:
    raw = input(
        f"[{e['normalized_url'][:50]}…] update/acknowledge/invalid/skip/quit? [u/a/i/s/q] "
    ).strip().lower()
    return {"u": "update", "a": "acknowledge", "i": "invalid",
            "s": "skip", "q": "quit"}.get(raw, raw)


def review_events() -> None:
    events = load_events(EVENTS_PATH)
    pending = [e for e in events if e.get("status") == "pending"]
    if not pending:
        print("No pending source-change events.")
        return
    print(f"{len(pending)} pending source-change event(s)\n")
    for e in pending:
        _show_event(e)
        action = _prompt_action(e)
        if action == "quit":
            print("quitting; remaining events stay pending.")
            return
        if action == "update":
            ids = [im["record_id"] for im in e.get("impacted_records", [])]
            sel = input(f"select record ids to create edit/reembed tasks for "
                        f"(comma-separated, empty=ALL of {len(ids)}): ").strip()
            selected = [s.strip() for s in sel.split(",") if s.strip()] or ids
            out = apply_source_action(FRESHNESS_DIR, e["event_id"], "update",
                                      selected_record_ids=selected)
            print(f"  → {out['effect']}")
            print("  Manually edit the selected records in data/chunks/<svc>.jsonl, "
                  "then run: python freshness/reembed.py --execute")
        elif action in ("acknowledge", "invalid", "skip"):
            out = apply_source_action(FRESHNESS_DIR, e["event_id"], action)
            print(f"  → {out['effect']}")
        else:
            print("  unrecognized answer; skipping (event stays pending)")


def review_discovery() -> None:
    queue = atomic_read_json(REVIEW_QUEUE_PATH, default=[]) or []
    pending = [q for q in queue if q.get("status") == "pending"]
    if not pending:
        print("No pending discovery candidates.")
        return
    print(f"{len(pending)} pending discovery candidate(s)\n")
    inventory = atomic_read_json(INVENTORY_PATH, default={}) or {}
    save_inventory = lambda inv: __import__("freshness.paths", fromlist=["atomic_write_json"]) \
        .atomic_write_json(INVENTORY_PATH, inv)
    for q in pending:
        print("=" * 78)
        print(f"event_id : {q.get('event_id')}")
        print(f"type     : {q.get('type')}  service: {q.get('service')}")
        print(f"url      : {q.get('url')}")
        print(f"seed     : {q.get('seed')}")
        print(f"title    : {q.get('title', '')}")
        print(f"hash     : {(q.get('content_hash') or '')[:20]}")
        print("-" * 78)
        raw = input("relevant/not_relevant/duplicate/unsafe_or_excluded/skip/quit? "
                    "[r/n/d/x/s/q] ").strip().lower()
        action = {"r": "relevant", "n": "not_relevant", "d": "duplicate",
                  "x": "unsafe_or_excluded", "s": "skip", "q": "quit"}.get(raw, raw)
        if action == "quit":
            print("quitting; remaining candidates stay pending.")
            return
        if action == "skip":
            continue
        try:
            from freshness.discovery import apply_review_action
            apply_review_action(queue, inventory, q["event_id"], action,
                                inventory_path=INVENTORY_PATH, save=save_inventory)
            atomic_write_json(REVIEW_QUEUE_PATH, queue)
            print(f"  → marked {action}")
        except SystemExit as exc:
            print(f"  {exc}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Freshness V2 human review gates")
    ap.add_argument("--list", action="store_true", help="list pending items and exit")
    ap.add_argument("--discovery", action="store_true",
                    help="review discovery candidates instead of source-change events")
    args = ap.parse_args()

    if args.discovery:
        if args.list:
            queue = [q for q in atomic_read_json(REVIEW_QUEUE_PATH, default=[])
                     if q.get("status") == "pending"]
            print(json.dumps(queue, indent=2, ensure_ascii=False))
            return
        review_discovery()
        return

    if args.list:
        pending = [e for e in load_events(EVENTS_PATH) if e.get("status") == "pending"]
        for e in pending:
            _show_event(e)
        if not pending:
            print("No pending source-change events.")
        return
    review_events()


if __name__ == "__main__":
    main()
