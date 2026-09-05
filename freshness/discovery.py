"""
freshness/discovery.py — official-source discovery monitoring (Freshness V2
§18–§28).

Problem B of Freshness V2: "did a monitored official hub publish a new
relevant page/document, remove one, or replace one?"

  * reuses the Phase 3.5 bounded acquisition crawler — NO second crawler
  * curated, purpose-documented seeds in freshness/source_registry.json
  * per-seed trusted inventory persisted atomically, separate from source
    content-hash state (§20)
  * inventory comparison produces distinct event types (§21):
      new_source / source_missing / source_removed / source_reappeared /
      source_link_changed / known_source_changed
  * a FAILED hub crawl preserves the previous inventory and creates NO mass
    deletion — only a VERIFIED crawl advances child presence/absence (§22)
  * PDFs are first-class: same URL + different bytes = known_source_changed
  * new official resources go to a review queue; they NEVER automatically
    become corpus records or vectors (§23/§24)
  * PII/login/wizard URL policy and capture-quality gates remain authoritative
  * capture-only material can never enter the trusted inventory (§30-test)
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from acquisition.urls import classify_url, normalize_url

from .paths import atomic_read_json, atomic_write_json

REGISTRY_PATH = Path(__file__).resolve().parent / "source_registry.json"
INVENTORY_PATH = Path(__file__).resolve().parent / "discovery_inventory.json"
REVIEW_QUEUE_PATH = Path(__file__).resolve().parent / "pending_source_review.json"

DEFAULT_MISSING_THRESHOLD = 3   # verified consecutive absences before removal escalation


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    return atomic_read_json(path, default={"services": {}}) or {}


def inventory_entry_allowed(entry: dict) -> bool:
    """Capture-only/unverified material can NEVER enter the trusted inventory
    (§30-test); verified captures and verified dynamic states can (§26)."""
    return entry.get("verification_status") == "verified"


def _entry_key(url: str) -> str:
    return normalize_url(url)


def _matches_relevance(url: str, title: str, seed_cfg: dict) -> tuple[bool, str]:
    """Conservative deterministic relevance rules (§24). include/exclude
    keyword patterns come from the registry seed config; URL-level policy
    (wizard/login/boilerplate) runs first."""
    disposition, reason = classify_url(url)
    if disposition in {"wizard", "invalid"}:
        return False, f"excluded: {reason}"
    if disposition == "boilerplate" and seed_cfg.get("exclude_boilerplate", True):
        return False, "excluded: boilerplate"
    text = f"{url} {title}".lower()
    excludes = seed_cfg.get("exclude_patterns") or []
    for pat in excludes:
        if pat.lower() in text:
            return False, f"excluded by rule {pat!r}"
    includes = seed_cfg.get("include_patterns") or []
    if includes and not any(pat.lower() in text for pat in includes):
        return False, "no include-pattern match (config-gated)"
    return True, ""


def crawl_seed_inventory(seed_cfg: dict, *, transport, renderer, evidence_dir: Path) -> dict:
    """Crawl one discovery seed with the shared crawler and reduce its manifest
    to a verified-only inventory (§22/§30). Returns
    {ok: bool, resources: {norm_url: entry}, failures: [...]}."""
    from acquisition.crawler import CrawlConfig, Crawler

    cfg = CrawlConfig(
        seeds=[seed_cfg["url"]],
        allowlist=set(seed_cfg.get("allowed_domains") or []),
        max_depth=seed_cfg.get("max_depth", 1),
        max_pages=seed_cfg.get("max_pages", 20),
        interval_seconds=seed_cfg.get("interval_seconds", 1.0),
        also_screenshot=seed_cfg.get("screenshots", False),
    )
    crawler = Crawler(cfg, transport=transport, renderer=renderer,
                      out_dir=str(evidence_dir))
    manifest = crawler.run()

    # seed-level failure check (§22): the seed itself must have been verified
    seed_node = next((n for n in manifest["nodes"] if n.get("url") == normalize_url(seed_cfg["url"])
                      and n.get("disposition", "").startswith(("captured", "direct_pdf"))), None)
    if seed_node is None:
        return {"ok": False, "resources": {},
                "failures": [n for n in manifest["nodes"]
                             if n.get("url") == normalize_url(seed_cfg["url"])]}

    resources: dict = {}
    for node in manifest["nodes"]:
        disp = node.get("disposition", "")
        url = node.get("url", "")
        if not url:
            continue
        norm = _entry_key(url)
        verification = None
        rtype = "html"
        content_hash = None
        if disp in ("captured_verified_normal", "captured_verified_browser"):
            verification, rtype = "verified", "html"
            content_hash = (node.get("provenance") or {}).get("content_sha256")
        elif disp == "direct_pdf_verified":
            verification, rtype = "verified", "pdf"
            content_hash = (node.get("provenance") or {}).get("content_sha256")
        elif disp == "js_dynamic_state":
            st = node.get("state") or {}
            if st.get("verification_status") == "verified":
                verification, rtype = "verified", "js_dynamic_state"
                content_hash = st.get("content_sha256")
                url = node.get("url", "") + "#state:" + (st.get("state_name") or "")
                norm = _entry_key(url.split("#state:")[0]) + "#" + (st.get("state_name") or "")
        elif disp == "capture_only_tls_invalid":
            verification = "capture_only"    # never trusted (§30)
        else:
            continue
        relevant, why = _matches_relevance(url, node.get("trigger_text") or node.get("state", {}).get("state_name", "") or "", seed_cfg)
        if verification != "verified":
            resources.setdefault(norm, {
                "url": url, "type": rtype, "verification_status": verification,
                "first_seen": _now(), "last_seen": _now(),
                "reviewed": "unverified_capture", "reason": why or disp,
            })
            continue
        prev = resources.get(norm)
        resources[norm] = {
            "url": url, "type": rtype, "verification_status": verification,
            "acquisition_method": disp,
            "content_hash": content_hash,
            "parent": node.get("parent") or seed_cfg["url"],
            "title": (node.get("state", {}) or {}).get("state_name") or node.get("trigger_text") or "",
            "first_seen": None,      # stamped by inventory reconciliation
            "last_seen": _now(),
            "reviewed": None if relevant else "not_relevant",
            "reason": why,
        }
    return {"ok": True, "resources": resources, "failures": []}


def compare_inventories(previous: dict, current: dict, *, seed_url: str,
                        missing_threshold: int = DEFAULT_MISSING_THRESHOLD,
                        previous_missing: dict | None = None,
                        reviewed: set | None = None) -> list:
    """Pure inventory diff for one seed (§21/§22/§27) → event list.

    previous/current: {seed_url: {"resources": {norm_url: entry}}}
    previous_missing: {norm_url: {"count": n}} from earlier runs
    reviewed: normalized URLs already reviewed/ignored — never re-alert (§24)
    """
    prev_res = (previous or {}).get(seed_url, {}).get("resources", {})
    curr_res = (current or {}).get(seed_url, {}).get("resources", {})
    previous_missing = previous_missing or {}
    reviewed = reviewed or set()
    events: list = []
    now = _now()

    for url, cur in curr_res.items():
        prev = prev_res.get(url)
        if prev is None:
            if cur.get("reviewed") == "not_relevant" or url in reviewed:
                continue
            events.append({"type": "new_source", "seed": seed_url, "url": url,
                           "resource_type": cur.get("type"),
                           "title": cur.get("title", ""),
                           "content_hash": cur.get("content_hash"),
                           "detected_at": now, "status": "pending"})
        elif (cur.get("content_hash") and prev.get("content_hash")
              and cur["content_hash"] != prev["content_hash"]):
            events.append({"type": "known_source_changed", "seed": seed_url, "url": url,
                           "resource_type": cur.get("type"),
                           "previous_hash": prev["content_hash"],
                           "new_hash": cur["content_hash"],
                           "detected_at": now, "status": "pending"})

    for url, prev in prev_res.items():
        if url in curr_res or url in reviewed:
            continue
        # inventory entries are verified-only by construction; the guard is
        # defensive against capture_only/state leaking into a previous inventory
        if prev.get("verification_status", "verified") != "verified":
            continue
        count = previous_missing.get(url, {}).get("count", 0) + 1
        if count >= missing_threshold:
            events.append({"type": "source_removed", "seed": seed_url, "url": url,
                           "resource_type": prev.get("type"),
                           "consecutive_missing": count, "detected_at": now,
                           "status": "pending"})
        else:
            events.append({"type": "source_missing", "seed": seed_url, "url": url,
                           "resource_type": prev.get("type"),
                           "consecutive_missing": count, "detected_at": now,
                           "status": "pending"})

    # reappearing resources (§21/32)
    for url, miss in previous_missing.items():
        if url in curr_res and miss.get("count", 0) >= 1:
            events.append({"type": "source_reappeared", "seed": seed_url, "url": url,
                           "detected_at": now, "status": "pending"})

    # hub points to a materially different URL: exact anchor-text + parent
    # pairing only (§21: never infer identity from title similarity alone)
    missing_urls = {e["url"] for e in events if e["type"] == "source_missing"}
    for new_ev in [e for e in events if e["type"] == "new_source"]:
        anchor = (new_ev.get("title") or "").strip()
        if not anchor:
            continue
        for m_url in list(missing_urls):
            old = prev_res.get(m_url) or {}
            if (old.get("title") or "").strip() == anchor \
                    and old.get("parent") == curr_res.get(new_ev["url"], {}).get("parent"):
                events.append({"type": "source_link_changed", "seed": seed_url,
                               "old_url": m_url, "new_url": new_ev["url"],
                               "anchor": anchor, "detected_at": now,
                               "status": "pending"})
                new_ev["status"] = "paired_link_change"
                missing_urls.discard(m_url)
                break
    return events


def reconcile_seed_inventory(previous_seed_state: dict | None, *, crawl_failed: bool,
                             current_resources: dict | None, seed_url: str,
                             missing_counts: dict | None = None,
                             missing_threshold: int = DEFAULT_MISSING_THRESHOLD) -> dict:
    """Merge one seed's verified crawl into its trusted inventory and produce
    the inventory-diff events (§21/§22/§27). Pure function — hermetic."""
    missing_counts = missing_counts if missing_counts is not None else {}
    prev_state = previous_seed_state or {"resources": {}}
    prev_resources = prev_state.get("resources", {})
    events: list = []

    if crawl_failed:
        # §22: preserve previous trusted inventory; no child marked missing
        return {"preserved": True, "resources": prev_resources, "events": [],
                "seed_failed": True}

    current = current_resources or {}
    now = _now()
    reviewed = {u for u, r in prev_resources.items() if r.get("reviewed")}

    events = compare_inventories(
        {seed_url: {"resources": prev_resources}},
        {seed_url: {"resources": current}},
        seed_url=seed_url, missing_threshold=missing_threshold,
        previous_missing=missing_counts, reviewed=reviewed)

    merged: dict = {}
    for url, cur in current.items():
        prev = prev_resources.get(url)
        entry = dict(cur)
        entry["first_seen"] = (prev or {}).get("first_seen") or now
        entry["last_seen"] = now
        if entry.get("reviewed") is None and url in reviewed:
            entry["reviewed"] = prev_resources[url].get("reviewed")
            entry["reason"] = prev_resources[url].get("reason")
        merged[url] = entry
        missing_counts.pop(url, None)

    for url, prev in prev_resources.items():
        if url in current:
            continue
        if prev.get("verification_status") != "verified":
            continue
        count = missing_counts.get(url, {}).get("count", 0) + 1
        missing_counts[url] = {"count": count, "last_missing": now}
        # keep it in the merged inventory marked missing (not instantly deleted, §21)
        kept = dict(prev)
        kept["missing"] = True
        kept["consecutive_missing"] = count
        merged[url] = kept

    return {"preserved": False, "resources": merged, "events": events,
            "seed_failed": False}


def apply_review_action(queue: list, inventory_state: dict, event_id: str, action: str,
                        *, inventory_path: Path | None = None,
                        save=None) -> list:
    """New-source review actions (§23):
       relevant / not_relevant / duplicate / unsafe_or_excluded / skip."""
    event = next((e for e in queue if e.get("event_id") == event_id), None)
    if event is None:
        raise SystemExit(f"review event {event_id} not found")
    if action == "skip":
        return queue
    if action not in {"relevant", "not_relevant", "duplicate", "unsafe_or_excluded"}:
        raise SystemExit(f"unknown review action {action!r}")
    event["status"] = action
    event["resolved_at"] = _now()
    url = _entry_key(event.get("url", ""))
    for seed_state in (inventory_state or {}).values():
        res = (seed_state.get("resources") or {}).get(url)
        if res is not None:
            res["reviewed"] = action
            res["reason"] = event.get("reason") or action
    if save:
        save(inventory_state)
    return queue
