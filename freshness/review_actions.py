"""
freshness/review_actions.py — explicit source-change review semantics
(Freshness V2 §9).

Actions on a pending source event:

  update       the change is legitimate AND corpus records need editing.
               Promotes the candidate to trusted baseline; creates
               record-level edit/reembed tasks ONLY for operator-selected
               record IDs (with pre-edit corpus text hash, §10).
  acknowledge  the source legitimately changed but the corpus needs no edit.
               Promotes candidate to trusted; no record tasks.
  invalid      the candidate is not a valid representation (maintenance page,
               bot wall, bad render...). Trusted baseline kept; candidate
               marked invalid so the same capture never re-alerts (§9).
  skip         no decision; event stays pending; nothing changes.

One freshness process/operator at a time (documented assumption, §16).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .paths import atomic_read_json, atomic_write_json
from . import source_store as S


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_events(path: Path) -> list:
    return atomic_read_json(path, default=[]) or []


def save_events(path: Path, events: list) -> None:
    atomic_write_json(path, events)


def load_store_for_events(store_path: Path) -> dict:
    return S.load_store(store_path)


def save_store_for_events(store_path: Path, store: dict) -> None:
    S.save_store(store, store_path)


def apply_source_action(freshness_dir: Path, event_id: str, action: str,
                        *, selected_record_ids: list[str] | None = None) -> dict:
    """Apply one review action. freshness_dir holds source_store.json,
    pending_source_events.json and (for update) approved_for_reembed.json.
    Returns a small result dict for CLI/test consumption."""
    freshness_dir = Path(freshness_dir)
    store_path = freshness_dir / "source_store.json"
    events_path = freshness_dir / "pending_source_events.json"
    tasks_path = freshness_dir / "approved_for_reembed.json"

    store = S.load_store(store_path)
    events = load_events(events_path)
    event = next((e for e in events if e["event_id"] == event_id), None)
    if event is None:
        raise SystemExit(f"event {event_id} not found")
    if event.get("status") != "pending":
        raise SystemExit(f"event {event_id} is already {event['status']!r}; "
                         f"only pending events can be actioned")
    entry = store.get(event["normalized_url"])
    if entry is None:
        raise SystemExit(f"no source state for {event['normalized_url']}")

    result: dict = {"event_id": event_id, "action": action}

    if action == "skip":
        result["effect"] = "event left pending; nothing changed"

    elif action == "invalid":
        entry["candidate_invalid"] = True
        event["status"] = "invalid"
        event["resolved_at"] = _now()
        result["effect"] = ("trusted baseline kept; candidate marked invalid "
                            "(no re-alert for the same capture)")

    elif action == "acknowledge":
        S.promote_candidate(entry)
        event["status"] = "acknowledged"
        event["resolved_at"] = _now()
        result["effect"] = "candidate promoted to trusted baseline; no record tasks"

    elif action == "update":
        impacted_ids = [im["record_id"] for im in event.get("impacted_records", [])]
        selected = list(selected_record_ids) if selected_record_ids else []
        unknown = [r for r in selected if r not in impacted_ids]
        if unknown:
            raise SystemExit(f"selected record ids not impacted by this event: {unknown}")
        if not selected:
            raise SystemExit("update requires at least one selected record id "
                             "(use acknowledge if no record needs editing)")
        S.promote_candidate(entry)
        tasks = atomic_read_json(tasks_path, default=[]) or []
        existing_task_ids = {t["record_id"] for t in tasks}
        created = []
        for rec_info in event.get("impacted_records", []):
            rid = rec_info["record_id"]
            if rid not in selected or rid in existing_task_ids:
                continue
            pre_edit_hash = _corpus_text_hash(freshness_dir, rec_info["service"], rid)
            if pre_edit_hash is None:
                raise SystemExit(f"record {rid} not found in its corpus — cannot "
                                 f"create a reembed task for it")
            created.append({
                "task_id": f"task_{rid}_{event['event_id']}",
                "record_id": rid,
                "service": rec_info["service"],
                "heading": rec_info.get("heading", ""),
                "source_url": event["canonical_url"],
                "event_id": event_id,
                "approved_at": _now(),
                "pre_edit_text_sha256": pre_edit_hash,
            })
        tasks.extend(created)
        atomic_write_json(tasks_path, tasks)
        event["status"] = "updated"
        event["resolved_at"] = _now()
        event["reembed_task_ids"] = [t["task_id"] for t in created]
        result["effect"] = (f"candidate promoted; {len(created)} record task(s) created "
                            f"for manual corpus editing")
        result["tasks"] = created
    else:
        raise SystemExit(f"unknown action {action!r}; use update/acknowledge/invalid/skip")

    S.save_store(store, store_path)
    save_events(events_path, events)
    return result


def _corpus_text_hash(freshness_dir: Path, service: str, record_id: str) -> str | None:
    """Pre-edit corpus text hash for a record (§10). Only verifies that the
    approved corpus row exists and captures its current hash — never compares
    corpus text to page source text. Corpus lookup: freshness_dir/<svc>.jsonl
    (fixture/test layouts) first, then the standard repo data/chunks/ layout.
    """
    candidates = [
        Path(freshness_dir) / f"{service}.jsonl",
        Path(freshness_dir) / "data" / "chunks" / f"{service}.jsonl",
    ]
    for corpus_path in candidates:
        if not corpus_path.exists():
            continue
        with open(corpus_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("id") == record_id:
                    from .paths import sha256_text
                    return sha256_text(r.get("text", ""))
    return None
