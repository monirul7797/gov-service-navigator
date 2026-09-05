"""
test_freshness_v21.py — Freshness V2.1 integration-path tests (§15).

These exercise the PUBLIC production paths the pure-helper suite missed:
real repo-like layouts, persisted inventory/queue round-trips, robots policy
integration, redirect allowlist enforcement, migration conflict zero-write,
and dry-run current-state simulation. Hermetic: fakes only, no network.

Run:  python test_freshness_v21.py
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acquisition.transport import RenderResult, StrictResult  # noqa: E402
from test_freshness import (CHECKS, FakeRenderer, FakeTransport, PAGE_A,  # noqa: E402
                            fixed_now, fixture_corpus, html, make_monitor, rec)
from freshness.known_monitor import KnownSourceMonitor  # noqa: E402

SEED = "https://gov.example/hub"


def check(name: str, ok: bool, detail: str = ""):
    if ok:
        CHECKS["pass"] += 1
        print(f"  \u2713 PASS  {name}")
    else:
        CHECKS["fail"] += 1
        print(f"  \u2717 FAIL  {name}  {detail}")


def _robot_parser(disallow_all=False):
    import urllib.robotparser
    p = urllib.robotparser.RobotFileParser()
    if disallow_all:
        p.parse(["User-agent: *", "Disallow: /"])
    else:
        p.allow_all = True
    return p


# ---------------------------------------------------------------------------
# B2 — known-source monitor honors the shared robots policy
# ---------------------------------------------------------------------------

def test_b2_robots_integration():
    print("V2.1 B2 — known-source monitor honors shared robots policy:")
    tmp = Path(tempfile.mkdtemp())
    corpus = fixture_corpus(tmp, [rec("r1", PAGE_A)])
    from acquisition.robots import RobotsCache

    cache = RobotsCache()
    cache.parsers["gov.example"] = _robot_parser()
    renderer = FakeRenderer({PAGE_A: html("ROBOTS ALLOWED " * 30)})
    mon = make_monitor(tmp, FakeTransport(), renderer, {"nid": corpus}, robots=cache)
    mon.run()
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    check("B2a robots allow -> acquisition proceeded",
          store[PAGE_A]["trusted_hash"] == hashlib.sha256(("ROBOTS ALLOWED " * 30).encode()).hexdigest())

    renderer.script[PAGE_A] = html("NEW CONTENT AFTER DISALLOW " * 30)
    cache2 = RobotsCache()
    cache2.parsers["gov.example"] = _robot_parser(disallow_all=True)
    mon2 = make_monitor(tmp, FakeTransport(), renderer, {"nid": corpus}, robots=cache2)
    s = mon2.run()
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    check("B2b disallow -> no fetch, robots status, baseline preserved",
          store[PAGE_A]["status"] == "robots_disallowed"
          and store[PAGE_A]["trusted_hash"] != hashlib.sha256(("NEW CONTENT AFTER DISALLOW " * 30).encode()).hexdigest()
          and s["blocked_or_robots"] == 1)

    cache3 = RobotsCache()
    cache3.unavailable["gov.example"] = "robots_unreachable"
    mon3 = make_monitor(tmp, FakeTransport(), renderer, {"nid": corpus}, robots=cache3)
    mon3.run()
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    check("B2c unreachable robots follows shared policy (proceed + note)",
          store[PAGE_A]["status"] != "robots_disallowed"
          and store[PAGE_A]["candidate_hash"] == hashlib.sha256(("NEW CONTENT AFTER DISALLOW " * 30).encode()).hexdigest())


# ---------------------------------------------------------------------------
# B1 + 13B — production repo layout for `update`; accepted source hash
# ---------------------------------------------------------------------------

def test_b1_update_production_layout():
    print("V2.1 B1 — update resolves the production repo layout:")
    repo = Path(tempfile.mkdtemp()) / "repo"
    (repo / "freshness").mkdir(parents=True)
    (repo / "data" / "chunks").mkdir(parents=True)
    corpus = repo / "data" / "chunks" / "nid.jsonl"
    with open(corpus, "w", encoding="utf-8") as f:
        f.write(json.dumps(rec("r1", PAGE_A)) + "\n")
    renderer = FakeRenderer({PAGE_A: html("BASE " * 30)})
    tmp_freshness = repo / "freshness"
    from tests.maintenance.fixtures import robots_for_hosts
    mon = KnownSourceMonitor(
        corpus_paths={"nid": corpus}, transport=FakeTransport(), renderer=renderer,
        store_path=tmp_freshness / "source_store.json",
        snapshot_dir=tmp_freshness / "snapshots",
        events_path=tmp_freshness / "pending_source_events.json",
        rate_limit_seconds=0, now_fn=fixed_now,
        robots=robots_for_hosts({"gov.example"}))
    mon.run()
    renderer.script[PAGE_A] = html("CHANGED " * 30)
    mon.run()
    from freshness.review_actions import apply_source_action
    events = json.loads((tmp_freshness / "pending_source_events.json").read_text(encoding="utf-8"))
    ev = next(e for e in events if e["status"] == "pending")
    out = apply_source_action(tmp_freshness, ev["event_id"], "update",
                              selected_record_ids=["r1"])
    check("B1a production-layout update creates the task", len(out.get("tasks", [])) == 1)
    want = hashlib.sha256(b"corpus text r1").hexdigest()
    check("B1b pre_edit hash correct", out["tasks"][0]["pre_edit_text_sha256"] == want)
    check("B1c task carries accepted source hash (13B)",
          out["tasks"][0]["accepted_source_sha256"] is not None)


def test_13b_stale_source_detection():
    print("V2.1 13B — stale task after newer accepted source version:")
    tmp = Path(tempfile.mkdtemp())
    corpus = fixture_corpus(tmp, [rec("r1", PAGE_A)])
    recs = [json.loads(l) for l in open(corpus, encoding="utf-8")]
    recs[0]["text"] = "EDITED CONTENT"
    with open(corpus, "w", encoding="utf-8") as f:
        f.write(json.dumps(recs[0], ensure_ascii=False) + "\n")
    tasks = [{"task_id": "t1", "record_id": "r1", "service": "nid",
              "source_url": PAGE_A, "event_id": "ev", "approved_at": fixed_now(),
              "pre_edit_text_sha256": hashlib.sha256(b"corpus text r1").hexdigest(),
              "accepted_source_sha256": "0000"}]
    (tmp / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    store = {PAGE_A: {"trusted_hash": "cccc"}}
    (tmp / "source_store.json").write_text(json.dumps(store), encoding="utf-8")
    calls = []

    class FC:
        def upsert(self, ids, documents, embeddings, metadatas):
            calls.append(("upsert", list(ids)))

        def get(self, ids, include=None):
            return {"ids": ids, "documents": ["x"]}

        def count(self):
            return 1

    from freshness.reembed import ReembedService
    svc = ReembedService(corpus_paths={"nid": corpus}, tasks_path=tmp / "tasks.json",
                         embedder=lambda t: [[0.1]] * len(t),
                         collection_factory=lambda s: FC(),
                         source_store_path=tmp / "source_store.json")
    out = svc.run(execute=True)
    check("13Ba stale task fails as source_advanced_since_approval",
          len(calls) == 0
          and "source_advanced_since_approval" in json.dumps(out)
          and len(json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))) == 1)


# ---------------------------------------------------------------------------
# B11 — malformed task isolation
# ---------------------------------------------------------------------------

def test_b11_malformed_task_isolation():
    print("V2.1 B11 — malformed task cannot abort valid later tasks:")
    tmp = Path(tempfile.mkdtemp())
    corpus = fixture_corpus(tmp, [rec("r1", PAGE_A)])
    recs = [json.loads(l) for l in open(corpus, encoding="utf-8")]
    recs[0]["text"] = "EDITED CONTENT"
    with open(corpus, "w", encoding="utf-8") as f:
        f.write(json.dumps(recs[0], ensure_ascii=False) + "\n")
    tasks = [
        {"task_id": "bad", "record_id": "x"},
        {"task_id": "good", "record_id": "r1", "service": "nid", "source_url": PAGE_A,
         "event_id": "ev", "approved_at": fixed_now(),
         "pre_edit_text_sha256": hashlib.sha256(b"corpus text r1").hexdigest()},
    ]
    (tmp / "tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
    calls = []

    class FC:
        def upsert(self, ids, documents, embeddings, metadatas):
            calls.append(("upsert", list(ids)))
            self.rows = {i: {"document": d} for i, d in zip(ids, documents)}

        def get(self, ids, include=None):
            return {"ids": ids, "documents": [self.rows[i]["document"] for i in ids]}

        def count(self):
            return len(self.rows)

    from freshness.reembed import ReembedService
    svc = ReembedService(corpus_paths={"nid": corpus}, tasks_path=tmp / "tasks.json",
                         embedder=lambda t: [[0.1]] * len(t),
                         collection_factory=lambda s: FC())
    out = svc.run(execute=True)
    check("B11a malformed task failed, valid task processed",
          ("upsert", ["r1"]) in calls
          and any(f["task_id"] == "bad" for f in out["failed"]))
    remaining = json.loads((tmp / "tasks.json").read_text(encoding="utf-8"))
    check("B11b malformed task remains pending",
          len(remaining) == 1 and remaining[0]["task_id"] == "bad")


# ---------------------------------------------------------------------------
# 13C — candidate snapshot immutability
# ---------------------------------------------------------------------------

def test_13c_candidate_snapshot_immutability():
    print("V2.1 13C — resolved-event candidate evidence cannot be overwritten:")
    tmp = Path(tempfile.mkdtemp())
    corpus = fixture_corpus(tmp, [rec("r1", PAGE_A)])
    renderer = FakeRenderer({PAGE_A: html("V1 CONTENT " * 30)})
    mon = make_monitor(tmp, FakeTransport(), renderer, {"nid": corpus})
    mon.run()
    renderer.script[PAGE_A] = html("V2 CONTENT " * 30)
    mon.run()
    snap1 = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))[PAGE_A]["candidate_snapshot"]
    from freshness.review_actions import apply_source_action, load_events
    ev = next(e for e in load_events(tmp / "pending_source_events.json") if e["status"] == "pending")
    apply_source_action(tmp, ev["event_id"], "acknowledge")
    renderer.script[PAGE_A] = html("V3 CONTENT " * 30)
    mon.run()
    snap2 = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))[PAGE_A]["candidate_snapshot"]
    check("13C distinct candidate files; resolved event's evidence intact",
          snap1 != snap2 and Path(snap1).exists() and Path(snap2).exists())


# ---------------------------------------------------------------------------
# B12 — redirect allowlist
# ---------------------------------------------------------------------------

def test_b12_redirect_allowlist():
    print("V2.1 B12 — final redirect destinations are allowlist-validated:")
    from acquisition.acquire import acquire_url
    from acquisition.robots import RobotsCache

    def run(transport, renderer, allow):
        cache = RobotsCache()
        cache.parsers["gov.example"] = _robot_parser()
        return acquire_url("https://gov.example/start", transport=transport,
                           renderer=renderer, robots=cache,
                           out_dir=tempfile.mkdtemp(), user_agent="FreshnessChecker",
                           allowed_domains=allow)

    t = FakeTransport({"https://gov.example/start": StrictResult(
        ok=True, status=200, content_type="text/html",
        body=("<html><body>" + "CONTENT " * 40 + "</body></html>").encode(),
        final_url="https://gov.example/final")})
    cap = run(t, FakeRenderer(), {"gov.example"})
    check("B12a same-domain redirect allowed",
          cap.disposition == "captured" and cap.final_url == "https://gov.example/final"
          and cap.redirect_occurred is True)

    t = FakeTransport({"https://gov.example/start": StrictResult(
        ok=True, status=200, content_type="text/html",
        body=("<html><body>" + "CONTENT " * 40 + "</body></html>").encode(),
        final_url="https://www.gov.example/final")})
    cap = run(t, FakeRenderer(), {"gov.example"})
    check("B12b allowed subdomain redirect allowed", cap.disposition == "captured")

    t = FakeTransport({"https://gov.example/start": StrictResult(
        ok=True, status=200, content_type="text/html",
        body=("<html><body>" + "CONTENT " * 40 + "</body></html>").encode(),
        final_url="https://unrelated.example.net/final")})
    cap = run(t, FakeRenderer(), {"gov.example"})
    check("B12c unrelated-domain redirect excluded",
          cap.disposition == "redirected_out_of_allowlist" and cap.text_path == "")

    t = FakeTransport({"https://gov.example/start": StrictResult(
        ok=True, status=200, content_type="application/pdf", body=b"%PDF",
        final_url="https://unrelated.example.net/doc.pdf")})
    cap = run(t, FakeRenderer(), {"gov.example"})
    check("B12d redirected PDF outside allowlist untrusted",
          cap.disposition == "redirected_out_of_allowlist" and cap.pdf_path == "")

    A = "https://gov.example/"
    renderer = FakeRenderer({A: html("HUB " * 30)})
    renderer.script[(A, False)] = RenderResult(
        ok=True, status=200, text="HUB " * 30, html="<html></html>",
        screenshot_png=b"", final_url="https://unrelated.example.net/")
    from acquisition.crawler import CrawlConfig, Crawler
    from tests.maintenance.fixtures import patch_crawler_robots
    with patch_crawler_robots({"gov.example"}):
        crawler = Crawler(CrawlConfig(seeds=[A], allowlist={"gov.example"}, max_depth=2,
                                      interval_seconds=0),
                          transport=FakeTransport(), renderer=renderer,
                          out_dir=tempfile.mkdtemp())
    manifest = crawler.run()
    check("B12e crawler does not trust/discover from out-of-allowlist destination",
          not any(n.get("disposition", "").startswith("captured")
                  and n.get("url") == A for n in manifest["nodes"])
          and not any(str(n.get("url", "")).startswith("https://gov.example/kid")
                      for n in manifest["nodes"]))


# ---------------------------------------------------------------------------
# B5 — capture-only trust boundary (integration through reconciliation)
# ---------------------------------------------------------------------------

def test_b5_capture_only_trust_boundary():
    print("V2.1 B5 — capture-only resources never reach trusted inventory:")
    from freshness.discovery import reconcile_seed_inventory
    prev = {"resources": {}}
    current = {
        "https://gov.example/hub/ok": {"type": "html", "verification_status": "verified",
                                       "content_hash": "h1"},
        "https://gov.example/hub/cap": {"type": "html", "verification_status": "capture_only",
                                        "content_hash": "h2"},
    }
    out = reconcile_seed_inventory(prev, crawl_failed=False, current_resources=current,
                                   seed_url=SEED, missing_counts={})
    check("B5a capture-only excluded from trusted resources",
          "https://gov.example/hub/cap" not in out["resources"]
          and "https://gov.example/hub/ok" in out["resources"])
    check("B5b capture-only preserved as separate diagnostics",
          out["unverified_diagnostics"].get("https://gov.example/hub/cap") is not None)
    check("B5c capture-only produces no new_source event",
          not any(e["url"] == "https://gov.example/hub/cap" for e in out["events"]))

    manifest = {"nodes": [
        {"url": "https://gov.example/hub", "parent": "", "depth": 0,
         "disposition": "captured_verified_normal",
         "provenance": {"content_sha256": "seed"}},
        {"url": "https://gov.example/hub/cap", "parent": "https://gov.example/hub",
         "depth": 1, "disposition": "capture_only_tls_invalid", "provenance": {}},
    ]}
    seed_cfg = {"url": "https://gov.example/hub", "allowed_domains": ["gov.example"],
                "max_depth": 1, "max_pages": 5}
    from freshness.discovery import crawl_seed_inventory
    from tests.maintenance.fixtures import patch_crawler_robots
    with patch_crawler_robots({"gov.example"}):
        reduced = crawl_seed_inventory(seed_cfg, transport=FakeTransport(),
                                       renderer=FakeRenderer({SEED: html("HUB " * 30)}),
                                       evidence_dir=Path(tempfile.mkdtemp()))
    check("B5d reduction path separates unverified nodes from verified resources",
          all(r.get("verification_status") == "verified"
              for r in reduced["resources"].values()))


# ---------------------------------------------------------------------------
# B3 + B8 — discovery lifecycle, stable event ids, queue persistence
# ---------------------------------------------------------------------------

def test_b3_b8_lifecycle_and_event_ids():
    print("V2.1 B3/B8 — discovery lifecycle + stable event ids:")
    from freshness.discovery import make_event_id, reconcile_seed_inventory
    URL = "https://gov.example/hub/page"
    missing_counts = {}
    res = {"resources": {URL: {"type": "html", "verification_status": "verified"}}}
    out1 = reconcile_seed_inventory(
        {"resources": {URL: {"type": "html", "verification_status": "verified"}}},
        crawl_failed=False, current_resources={}, seed_url=SEED,
        missing_counts=missing_counts)
    out2 = reconcile_seed_inventory({"resources": out1["resources"]}, crawl_failed=False,
                                    current_resources={}, seed_url=SEED,
                                    missing_counts=missing_counts)
    out3 = reconcile_seed_inventory({"resources": out2["resources"]}, crawl_failed=False,
                                    current_resources={}, seed_url=SEED,
                                    missing_counts=missing_counts)
    out4 = reconcile_seed_inventory({"resources": out3["resources"]}, crawl_failed=False,
                                    current_resources=res["resources"], seed_url=SEED,
                                    missing_counts=missing_counts)
    types = [e["type"] for e in out1["events"] + out2["events"]
             + out3["events"] + out4["events"]]
    check("B8a lifecycle present->missing->missing->removed->reappeared",
          types == ["source_missing", "source_missing", "source_removed",
                    "source_reappeared"], str(types))
    ids = [make_event_id("nid", SEED, "new_source", URL) for _ in range(2)]
    check("B3a event ids deterministic (no process-randomized hash)", ids[0] == ids[1])
    check("B3b event ids distinguish state",
          make_event_id("nid", SEED, "new_source", URL, "a")
          != make_event_id("nid", SEED, "new_source", URL, "b"))


# ---------------------------------------------------------------------------
# B6 + B7 — dry run reads current state, writes nothing, cleans temp
# ---------------------------------------------------------------------------

def test_b6_b7_dry_run_current_state():
    print("V2.1 B6/B7 — dry run reads current state, writes nothing, cleans temp:")
    SEED_L = SEED
    reg = {"services": {"nid": {"discovery_seeds": [
        {"url": SEED_L, "max_depth": 1, "max_pages": 10,
         "allowed_domains": ["gov.example"], "purpose": "test",
         "include_patterns": [], "exclude_patterns": []}]}}}
    tmp = Path(tempfile.mkdtemp())
    reg_path, inv_path, queue_path = tmp / "reg.json", tmp / "inv.json", tmp / "queue.json"
    reg_path.write_text(json.dumps(reg), encoding="utf-8")
    preexisting = {"nid": {SEED_L: {
        "purpose": "test", "last_verified_crawl": fixed_now(),
        "resources": {f"{SEED_L}/a": {"type": "html", "verification_status": "verified",
                                      "first_seen": fixed_now(), "last_seen": fixed_now()},
                      f"{SEED_L}/b": {"type": "html", "verification_status": "verified",
                                      "first_seen": fixed_now(), "last_seen": fixed_now()}},
        "missing_counts": {}}}}
    inv_path.write_text(json.dumps(preexisting), encoding="utf-8")
    queue_path.write_text("[]", encoding="utf-8")
    before = {p: p.read_bytes() for p in (inv_path, queue_path)}

    renderer = FakeRenderer({SEED_L: html("HUB"),
                             f"{SEED_L}/a": html("A " * 30),
                             f"{SEED_L}/b": html("B " * 30),
                             f"{SEED_L}/c": html("C " * 30)})
    renderer.links = {SEED_L: [f"{SEED_L}/a", f"{SEED_L}/b", f"{SEED_L}/c"]}
    from freshness.run_freshness_check import run_discovery
    from tests.maintenance.fixtures import patch_crawler_robots
    with patch_crawler_robots({"gov.example"}):
        summary = run_discovery(["nid"], dry_run=True, transport=FakeTransport(),
                                renderer=renderer, registry_path=reg_path,
                                inventory_path=inv_path, review_queue_path=queue_path)
    check("B6a dry run predicts only new_source C (not A/B/C as all-new)",
          summary["new_sources"] == 1, str(summary))
    check("B6b monitoring files byte-identical after dry run",
          all(p.read_bytes() == before[p] for p in (inv_path, queue_path)))
    leftovers = [p for p in glob.glob(os.path.join(tempfile.gettempdir(), "discovery_dry_*"))]
    check("B7a dry-run temp evidence dirs are disposed", leftovers == [], str(leftovers[:2]))


# ---------------------------------------------------------------------------
# B10 — migration conflict zero-write
# ---------------------------------------------------------------------------

def test_b10_migration_conflict_zero_write():
    print("V2.1 B10 — migration conflict performs zero V2 writes:")
    tmp = Path(tempfile.mkdtemp())
    v1_store = tmp / "freshness_store.json"
    ok_hash = hashlib.sha256(b"OK").hexdigest()
    v1_store.write_text(json.dumps({
        "r_ok1": {"source_url": "https://x.gov.bd/ok", "content_hash": ok_hash,
                  "last_checked": fixed_now()},
        "r_ok2": {"source_url": "https://x.gov.bd/ok", "content_hash": ok_hash,
                  "last_checked": fixed_now()},
        "r_a": {"source_url": "https://x.gov.bd/a", "content_hash": "ha",
                "last_checked": fixed_now()},
        "r_b": {"source_url": "https://x.gov.bd/a", "content_hash": "hb",
                "last_checked": fixed_now()},
    }), encoding="utf-8")
    snaps = tmp / "last_known_text"
    snaps.mkdir()
    for rid, body in [("r_ok1", "OK"), ("r_ok2", "OK"), ("r_a", "A"), ("r_b", "B")]:
        (snaps / f"{rid}.txt").write_text(body, encoding="utf-8")
    v2_store = tmp / "source_store.json"
    v2_store.write_text("{}", encoding="utf-8")
    from freshness.migrate_v1 import migrate
    import contextlib
    import io
    buf = io.StringIO()
    halted = False
    try:
        with contextlib.redirect_stdout(buf):
            migrate(v1_store=v1_store, v1_snapshots=snaps, v2_store=v2_store,
                    snapshots_dir=tmp / "source_snapshots", backup_root=tmp / "backup")
    except SystemExit:
        halted = True
    check("B10a conflict halts migration", halted)
    check("B10b zero V2 snapshot writes on conflict",
          not (tmp / "source_snapshots").exists()
          or list((tmp / "source_snapshots").iterdir()) == [])
    check("B10c source store unchanged on conflict",
          json.loads(v2_store.read_text(encoding="utf-8")) == {})


# ---------------------------------------------------------------------------
# B9 — migration metrics reconcile with stored entries
# ---------------------------------------------------------------------------

def test_b9_migration_metrics_reconcile():
    print("V2.1 B9 — migration metrics reconcile with stored entries:")
    tmp = Path(tempfile.mkdtemp())
    v1_store = tmp / "freshness_store.json"
    ok_hash = hashlib.sha256(b"OK").hexdigest()
    v1_store.write_text(json.dumps({
        "r_ok1": {"source_url": "https://x.gov.bd/ok", "content_hash": ok_hash,
                  "last_checked": fixed_now()},
        "r_ok2": {"source_url": "https://x.gov.bd/ok", "content_hash": ok_hash,
                  "last_checked": fixed_now()},
        "r_bad": {"source_url": "gazette:not-a-url", "status": "skipped",
                  "last_checked": fixed_now()},
        "r_block": {"source_url": "https://x.gov.bd/blocked", "status": "needs_manual_screenshot",
                    "last_checked": fixed_now()},
    }), encoding="utf-8")
    snaps = tmp / "last_known_text"
    snaps.mkdir()
    for rid, body in [("r_ok1", "OK"), ("r_ok2", "OK")]:
        (snaps / f"{rid}.txt").write_text(body, encoding="utf-8")
    from freshness.migrate_v1 import migrate
    report = migrate(report_only=True, v1_store=v1_store, v1_snapshots=snaps)
    check("B9a audit metrics distinguish stored vs no-URL records",
          report["baseline_sources_stored"] == 1
          and report["status_only_sources_stored"] == 1
          and report["records_without_usable_url"] == 1,
          json.dumps({k: report[k] for k in ("baseline_sources_stored",
                                             "status_only_sources_stored",
                                             "records_without_usable_url")}))
    # committed run reconciles with the actual store
    v2_store = tmp / "source_store.json"
    migrate(v1_store=v1_store, v1_snapshots=snaps, v2_store=v2_store,
            snapshots_dir=tmp / "source_snapshots", backup_root=tmp / "backup")
    stored = json.loads(v2_store.read_text(encoding="utf-8"))
    baselines = len([e for e in stored.values() if e.get("trusted_hash")])
    status_only = len([e for e in stored.values() if not e.get("trusted_hash")])
    check("B9b stored counts equal report counts",
          baselines == report["baseline_sources_stored"]
          and status_only == report["status_only_sources_stored"]
          and baselines + status_only == len(stored))


# ---------------------------------------------------------------------------
# 13A — targeted run keeps complete impacted-record mapping
# ---------------------------------------------------------------------------

def test_13a_complete_impacted_mapping():
    print("V2.1 13A — targeted source check yields complete impacted-record list:")
    tmp = Path(tempfile.mkdtemp())
    shared = PAGE_A
    records = [rec(f"r{i}", shared, f"H{i}") for i in range(10)]
    corpus = fixture_corpus(tmp, records)
    renderer = FakeRenderer({shared: html("BASE " * 30)})
    mon = make_monitor(tmp, FakeTransport(), renderer, {"nid": corpus})
    mon.run()
    renderer.script[shared] = html("CHANGED " * 30)
    mon_sel = make_monitor(tmp, FakeTransport(), renderer, {"nid": corpus},
                           record_ids={"r3"})
    mon_sel.run()
    from freshness.review_actions import load_events
    events = [e for e in load_events(tmp / "pending_source_events.json")
              if e["normalized_url"] == shared and e["status"] == "pending"]
    got = sorted(im["record_id"] for im in events[0]["impacted_records"])
    check("13A event lists ALL records sharing the source (not just selected)",
          got == sorted(f"r{i}" for i in range(10)), str(got))


if __name__ == "__main__":
    test_b2_robots_integration()
    test_b1_update_production_layout()
    test_13b_stale_source_detection()
    test_b11_malformed_task_isolation()
    test_13c_candidate_snapshot_immutability()
    test_b12_redirect_allowlist()
    test_b5_capture_only_trust_boundary()
    test_b3_b8_lifecycle_and_event_ids()
    test_b6_b7_dry_run_current_state()
    test_b10_migration_conflict_zero_write()
    test_b9_migration_metrics_reconcile()
    test_13a_complete_impacted_mapping()
    print("=" * 78)
    if CHECKS["fail"]:
        print(f"Result: {CHECKS['fail']} CHECK(S) FAILED ({CHECKS['pass']} passed)")
        sys.exit(1)
    print(f"Result: ALL CHECKS PASS ({CHECKS['pass']} checks)")
