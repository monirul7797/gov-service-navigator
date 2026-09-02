"""
freshness/run_freshness_check.py — orchestrator entrypoint (Part A §A8).

Fetch → hash → diff → pending-review queue → freshness_report_<date>.md.

This pipeline DETECTS and SURFACES drift; it never rewrites the corpus and
never touches data/vectordb/ (§A1). Content fixes are human-reviewed via
freshness/review.py; only the post-approval re-embed step (freshness/reembed.py)
writes to Chroma.

Usage (run from anywhere; paths are repo-root anchored):
    python freshness/run_freshness_check.py                     # all 4 services
    python freshness/run_freshness_check.py --services nid      # subset
    python freshness/run_freshness_check.py --record-ids id1,id2   # dry run / targeted
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import diff          # noqa: E402
import fetch         # noqa: E402
import hash_store    # noqa: E402

REPO_ROOT = fetch.REPO_ROOT
FRESHNESS_DIR = os.path.join(REPO_ROOT, "freshness")
STORE_PATH = os.path.join(FRESHNESS_DIR, "freshness_store.json")
QUEUE_PATH = diff.QUEUE_PATH


def run_check(services: list[str], record_ids: set[str] | None) -> dict:
    """One full fetch→diff pass. Returns the summary dict used by the report."""
    store = hash_store.load_store(STORE_PATH)
    queue = diff.load_queue(QUEUE_PATH)

    targets = list(fetch.collect_targets(services, record_ids))
    summary = {
        "services": services,
        "targets": len(targets),
        "fetched": 0, "new": 0, "changed": 0, "unchanged": 0,
        "blocked": 0, "skipped": 0,
        "changed_records": [], "blocked_records": [], "skipped_records": [],
        "new_records": [],
        "robots_notes": set(),
    }

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        results = fetch.fetch_targets(targets, page)
        browser.close()

    for r in results:
        if r.status == "fetched":
            summary["fetched"] += 1
            if r.reason:
                summary["robots_notes"].add(r.reason)
            new_hash = diff.sha256_text(r.text)
            old_hash = hash_store.get_hash(store, r.record_id)
            if old_hash is None:
                summary["new"] += 1
                summary["new_records"].append(r)
                hash_store.update_hash(store, r.record_id, r.source_url, new_hash, changed=False)
                diff.save_last_known_text(r.record_id, r.text)
            elif old_hash != new_hash:
                summary["changed"] += 1
                summary["changed_records"].append(r)
                old_text = diff.load_last_known_text(r.record_id) or ""
                unified = diff.make_unified_diff(old_text, r.text)
                entry = {
                    "record_id": r.record_id,
                    "service": r.service,
                    "source_url": r.source_url,
                    "detected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "previous_hash": old_hash,
                    "new_hash": new_hash,
                    "diff": unified,
                    "new_text": r.text,
                }
                # one entry per record — a re-detection replaces stale evidence
                queue = [e for e in queue if e.get("record_id") != r.record_id]
                queue.append(entry)
                diff.save_queue(queue, QUEUE_PATH)
                hash_store.update_hash(store, r.record_id, r.source_url, new_hash, changed=True)
                diff.save_last_known_text(r.record_id, r.text)
            else:
                summary["unchanged"] += 1
        elif r.status == "needs_manual_screenshot":
            summary["blocked"] += 1
            summary["blocked_records"].append(r)
            hash_store.mark_status(store, r.record_id, r.source_url, r.status, r.reason)
        else:  # skipped — no source_url / malformed
            summary["skipped"] += 1
            summary["skipped_records"].append(r)
            hash_store.mark_status(store, r.record_id, r.source_url, r.status, r.reason)

    hash_store.save_store(store, STORE_PATH)
    summary["robots_notes"] = sorted(summary["robots_notes"])
    return summary


def write_report(summary: dict, out_path: str) -> None:
    """Human-readable markdown report — the MVP output of Part A (§A8)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [
        "# Freshness report — " + today,
        "",
        f"- Services checked: {', '.join(summary['services'])}",
        f"- Records targeted: {summary['targets']}",
        f"- Fetched: {summary['fetched']}  "
        f"(new baselines: {summary['new']}, changed: {summary['changed']}, "
        f"unchanged: {summary['unchanged']})",
        f"- Needs manual screenshot: {summary['blocked']}",
        f"- Skipped (no usable source_url): {summary['skipped']}",
        f"- Pending review queue: {len(diff.load_queue(QUEUE_PATH))} → "
        "`freshness/pending_review.json` (approve via `python freshness/review.py`)",
    ]
    if summary["robots_notes"]:
        lines += ["", "## Notes", ""]
        for note in summary["robots_notes"]:
            lines.append(f"- robots.txt was unreachable on at least one domain ({note}); "
                         "pages were fetched because no robots rules were actually served. "
                         "Explicit robots directives are always honored.")
    if summary["changed_records"]:
        lines += ["", "## Changed pages (awaiting human review)", ""]
        lines.append("| record_id | service | source_url |")
        lines.append("|---|---|---|")
        for r in summary["changed_records"]:
            lines.append(f"| `{r.record_id}` | {r.service} | {r.source_url} |")
    if summary["new_records"]:
        lines += ["", "## New baselines (first check — no comparison yet)", ""]
        lines.append("| record_id | service | source_url |")
        lines.append("|---|---|---|")
        for r in summary["new_records"]:
            lines.append(f"| `{r.record_id}` | {r.service} | {r.source_url} |")
    if summary["blocked_records"]:
        lines += ["", "## Needs manual screenshot (not fetched — do not evade)", ""]
        lines.append("| record_id | service | reason | source_url |")
        lines.append("|---|---|---|---|")
        for r in summary["blocked_records"]:
            lines.append(f"| `{r.record_id}` | {r.service} | {r.reason} | {r.source_url} |")
    if summary["skipped_records"]:
        lines += ["", "## Skipped (missing or malformed source_url)", ""]
        lines.append("| record_id | service | source_url value |")
        lines.append("|---|---|---|")
        for r in summary["skipped_records"]:
            shown = (r.source_url[:70] + "…") if len(r.source_url) > 70 else r.source_url
            lines.append(f"| `{r.record_id}` | {r.service} | {shown} |")
    lines += ["", "This run detects drift only — no corpus .jsonl or data/vectordb file "
              "was written by this script.", ""]

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main() -> None:
    ap = argparse.ArgumentParser(description="Freshness check: fetch → diff → report")
    ap.add_argument("--services", nargs="+", default=["nid", "epassport", "ereturn", "bdris"],
                    choices=["nid", "epassport", "ereturn", "bdris"])
    ap.add_argument("--record-ids", default=None,
                    help="comma-separated record ids to check (dry run / targeted)")
    args = ap.parse_args()

    record_ids = set(args.record_ids.split(",")) if args.record_ids else None
    summary = run_check(args.services, record_ids)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out_path = os.path.join(FRESHNESS_DIR, f"freshness_report_{today}.md")
    write_report(summary, out_path)

    print(f"targets={summary['targets']} fetched={summary['fetched']} "
          f"new={summary['new']} changed={summary['changed']} "
          f"unchanged={summary['unchanged']} blocked={summary['blocked']} "
          f"skipped={summary['skipped']}")
    print(f"report: {out_path}")


if __name__ == "__main__":
    main()
