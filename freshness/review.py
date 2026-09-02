"""
freshness/review.py — the human approval gate (Part A §A6).

Interactive CLI over the pending-review queue written by run_freshness_check.py:

  1. Lists all pending diffs (record id, service, source_url, short preview).
  2. For each, prompts accept / reject / skip.
  3. On accept: writes the record id to freshness/approved_for_reembed.json
     AND prints the new fetched text, so the human can manually update the
     corresponding field(s) in the service's data/chunks/<svc>.jsonl.
     THAT .jsonl EDIT IS NOT AUTOMATED — content correctness requires the
     same VAT/fee/dedup judgment as original corpus building (§A1).
  4. This script NEVER writes to data/vectordb/ or any corpus .jsonl file.

Usage:
    python freshness/review.py            # interactive gate
    python freshness/review.py --list     # just list pending diffs
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff          # noqa: E402

REPO_ROOT = diff.REPO_ROOT
FRESHNESS_DIR = os.path.join(REPO_ROOT, "freshness")
QUEUE_PATH = diff.QUEUE_PATH
APPROVED_PATH = os.path.join(FRESHNESS_DIR, "approved_for_reembed.json")

DIFF_PREVIEW_LINES = 15


def load_approved() -> list[dict]:
    if not os.path.exists(APPROVED_PATH):
        return []
    with open(APPROVED_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_approved(approved: list[dict]) -> None:
    with open(APPROVED_PATH, "w", encoding="utf-8") as f:
        json.dump(approved, f, ensure_ascii=False, indent=2)


def show_entry(entry: dict, full: bool = False) -> None:
    print("=" * 78)
    print(f"record_id : {entry['record_id']}")
    print(f"service   : {entry['service']}")
    print(f"source_url: {entry['source_url']}")
    print(f"detected  : {entry.get('detected_at', '?')}")
    print("-" * 78)
    unified = entry.get("diff", "")
    diff_lines = unified.splitlines()
    if not diff_lines:
        print("(no textual diff — hash changed with identical normalized text?)")
    elif full or len(diff_lines) <= DIFF_PREVIEW_LINES:
        print("\n".join(diff_lines))
    else:
        print("\n".join(diff_lines[:DIFF_PREVIEW_LINES]))
        print(f"… ({len(diff_lines) - DIFF_PREVIEW_LINES} more diff lines — [d] to show all)")
    print("-" * 78)


def print_new_text_for_manual_edit(entry: dict) -> None:
    """§A6 step 3: the accepted record's new fetched text goes to stdout for the
    human to apply to the .jsonl by hand. This is the whole point of the gate."""
    print("\n" + "#" * 78)
    print(f"ACCEPTED {entry['record_id']} — NEW FETCHED TEXT FOLLOWS.")
    print("Manually update data/chunks/%s.jsonl (field: text / content) using this."
          % entry["service"])
    print("Then run: python freshness/reembed.py --execute")
    print("#" * 78)
    print(entry.get("new_text", "(new text missing from queue entry)"))
    print("#" * 78 + "\n")


def prompt_action(entry: dict) -> str:
    while True:
        choice = input(
            f"[{entry['record_id'][:24]}…] accept/reject/skip/diff/quit? [a/r/s/d/q] "
        ).strip().lower()
        if choice in {"a", "r", "s", "d", "q"}:
            return choice
        print("  please answer a (accept), r (reject), s (skip), d (full diff), q (quit)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Human approval gate for freshness diffs")
    ap.add_argument("--list", action="store_true", help="list pending diffs and exit")
    args = ap.parse_args()

    queue = diff.load_queue(QUEUE_PATH)
    if not queue:
        print("No pending diffs. Nothing to review.")
        return

    print(f"{len(queue)} pending diff(s) in {os.path.basename(QUEUE_PATH)}\n")
    if args.list:
        for e in queue:
            show_entry(e)
        return

    approved = load_approved()
    remaining = list(queue)
    for entry in queue:
        show_entry(entry)
        action = prompt_action(entry)
        if action == "a":
            approved.append({
                "record_id": entry["record_id"],
                "service": entry["service"],
                "source_url": entry["source_url"],
                "approved_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
            save_approved(approved)
            remaining = [e for e in remaining if e["record_id"] != entry["record_id"]]
            print_new_text_for_manual_edit(entry)
        elif action == "r":
            # Rejected: corpus stays as-is; the store already holds the latest
            # hash, so this drift won't re-appear next run. Drop from queue.
            remaining = [e for e in remaining if e["record_id"] != entry["record_id"]]
            print(f"  rejected {entry['record_id']} — corpus untouched.\n")
        elif action == "q":
            print("  quitting; remaining entries stay pending.\n")
            break
        else:  # skip
            print("  skipped — stays in the queue.\n")

    diff.save_queue(remaining, QUEUE_PATH)
    print(f"Done. {len(remaining)} pending, {len(approved)} approved-for-reembed total.")


if __name__ == "__main__":
    main()
