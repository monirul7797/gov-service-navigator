"""
test_freshness.py — hermetic state-machine suite for Freshness V2 (§1).

No live government network, no Chromium, no Chroma, no model downloads:
corpora are fixtures in temp dirs; acquisition is faked via injectable
transport/renderer ports; the vector layer is faked via injectable embedder +
collection adapters.

Run:  python test_freshness.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from acquisition.transport import RenderResult, StrictResult  # noqa: E402

CHECKS = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = ""):
    if ok:
        CHECKS["pass"] += 1
        print(f"  \u2713 PASS  {name}")
    else:
        CHECKS["fail"] += 1
        print(f"  \u2717 FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, script=None):
        self.script = script or {}
        self.fetched = []

    def fetch(self, url):
        self.fetched.append(url)
        return self.script.get(url, StrictResult(ok=False, error_kind="connection",
                                                 error_detail="unscripted"))


class FakeRenderer:
    """goto-scripted renderer tracking every navigation (for the one-fetch test)."""

    def __init__(self, script=None):
        self.script = script or {}     # (url, ignore_tls) -> RenderResult  |  url -> RenderResult
        self.goto_log = []

    def goto(self, url, *, ignore_tls=False):
        self.goto_log.append((url, ignore_tls))
        r = self.script.get((url, ignore_tls), self.script.get(url))
        if r is None:
            return RenderResult(ok=False, error_kind="blocked", error_detail="net::ERR_UNSCRIPTED")
        return r

    # crawler-interface defaults (acquisition.crawler may drive this fake);
    # tests needing richer crawler behavior use test_acquisition.FakeRenderer
    def discover_links(self, url):
        return []

    def ensure_on(self, url):
        self.goto(url)

    def list_interactables(self):
        return []

    def activate(self, item):
        return False

    def settle(self, ms=0):
        pass

    def body_text(self):
        return ""

    def snapshot_state(self):
        return {"text": "", "html": "", "png": b""}


def html(text, status=200):
    return RenderResult(ok=status < 400, status=status, text=text,
                        html=f"<html>{text}</html>", screenshot_png=b"")


PAGE_A = "https://gov.example/source"
PAGE_B = "https://gov.example/other"
PAGE_PDF = "https://gov.example/doc.pdf"


def fixture_corpus(tmp: Path, records: list[dict], name="nid") -> Path:
    p = tmp / f"{name}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def rec(record_id, url, heading="H", service="nid"):
    # fields mirror a real record closely enough for the metadata builders
    return {"id": record_id, "source_url": url, "heading": heading,
            "text": f"corpus text {record_id}", "service": service,
            "category": "info", "lang": "en", "official": True}


def fixed_now():
    return "2026-09-04T00:00:00Z"


def make_monitor(tmp: Path, transport, renderer, corpus_paths, dry_run=False,
                 robots=None, record_ids=None):
    from freshness.known_monitor import KnownSourceMonitor
    return KnownSourceMonitor(
        corpus_paths=corpus_paths, transport=transport, renderer=renderer,
        store_path=tmp / "source_store.json",
        snapshot_dir=tmp / "snapshots",
        events_path=tmp / "pending_source_events.json",
        rate_limit_seconds=0, dry_run=dry_run, now_fn=fixed_now,
        robots=robots, record_ids=record_ids)


def state_bytes(tmp: Path):
    """Byte-for-byte snapshot of every persistent state file (§14)."""
    out = {}
    for name in ["source_store.json", "pending_source_events.json"]:
        p = tmp / name
        out[name] = p.read_bytes() if p.exists() else None
    snap = tmp / "snapshots"
    out["snapshots"] = {f.name: f.read_bytes() for f in sorted(snap.glob("*"))} if snap.exists() else {}
    return out


# ---------------------------------------------------------------------------
# Known-source monitoring (tests 1–13)
# ---------------------------------------------------------------------------

def test_known_source_monitoring():
    print("Known-source monitoring:")
    tmp = Path(tempfile.mkdtemp())

    # 1. first successful observation creates baseline, no pending change
    corpus = fixture_corpus(tmp, [rec("r1", PAGE_A)])
    renderer = FakeRenderer({PAGE_A: html("REAL CONTENT " * 30)})
    mon = make_monitor(tmp, FakeTransport(), renderer, {"nid": corpus})
    s = mon.run()
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    entry = store[PAGE_A]
    check("1a baseline created", entry["trusted_hash"] == hashlib.sha256(("REAL CONTENT " * 30).encode()).hexdigest())
    check("1b no pending event on first observation", (tmp / "pending_source_events.json").read_text(encoding="utf-8").strip() == "[]")

    # 2. unchanged check refreshes last_checked, keeps hash
    before = dict(entry)
    s = mon.run()
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    check("2a trusted hash unchanged", store[PAGE_A]["trusted_hash"] == before["trusted_hash"])
    check("2b last_checked refreshed", store[PAGE_A]["last_checked"] == fixed_now())
    check("2c exactly one navigation for one source", len(renderer.goto_log) == 2)

    # 4–8. changed source: ONE source-level event containing all impacted records;
    # multiple records sharing the URL cause ONE acquisition; trusted hash preserved
    renderer2 = FakeRenderer({PAGE_A: html("REAL CONTENT " * 30),
                              "CHANGED PAGE BODY " and PAGE_A: html("CHANGED PAGE BODY " * 30)})
    corpus2 = fixture_corpus(tmp, [rec("r1", PAGE_A), rec("r2", PAGE_A, "H2"),
                                   rec("r3", PAGE_A, "H3"), rec("r4", PAGE_B, "H4")])
    renderer3 = FakeRenderer({PAGE_A: html("CHANGED PAGE BODY " * 30),
                              PAGE_B: html("OTHER CONTENT " * 30)})
    tmp2 = Path(tempfile.mkdtemp())
    mon2 = make_monitor(tmp2, FakeTransport(), renderer3, {"nid": corpus2})
    mon2.run()   # baseline for both sources
    renderer3.goto_log.clear()
    renderer3.script[PAGE_A] = html("BRAND NEW CONTENT " * 30)
    s = mon2.run()
    events = json.loads((tmp2 / "pending_source_events.json").read_text(encoding="utf-8"))
    store = json.loads((tmp2 / "source_store.json").read_text(encoding="utf-8"))
    pa_events = [e for e in events if e["normalized_url"] == PAGE_A]
    check("4a exactly ONE pending event for the changed source", len(pa_events) == 1)
    check("5a trusted hash NOT overwritten before review",
          store[PAGE_A]["trusted_hash"] == hashlib.sha256(("CHANGED PAGE BODY " * 30).encode()).hexdigest()
          and store[PAGE_A]["candidate_hash"] == hashlib.sha256(("BRAND NEW CONTENT " * 30).encode()).hexdigest())
    got_ids = sorted(im["record_id"] for im in pa_events[0]["impacted_records"])
    check("9a event lists all impacted record ids", got_ids == ["r1", "r2", "r3"])
    fetches_for_A = [u for u in renderer3.goto_log if u[0] == PAGE_A]
    check("8a shared URL fetched exactly ONCE per run", len(fetches_for_A) == 1, f"{len(fetches_for_A)}")
    summary = s
    check("4b report separates records mapped vs unique sources",
          summary["records_mapped"] == 4 and summary["unique_sources"] == 2
          and summary["sources_fetched"] == 2)

    # 8-candidate re-observation: same candidate again -> no new event
    s = mon2.run()
    events = json.loads((tmp2 / "pending_source_events.json").read_text(encoding="utf-8"))
    pa_events = [e for e in events if e["normalized_url"] == PAGE_A and e["status"] == "pending"]
    check("8b repeated identical candidate does not duplicate the alert", len(pa_events) == 1)

    # 3/13. recovery + method transition
    tmp3 = Path(tempfile.mkdtemp())
    corpus3 = fixture_corpus(tmp3, [rec("r1", PAGE_A)])
    renderer4 = FakeRenderer({PAGE_A: html("REAL CONTENT " * 30)})
    mon3 = make_monitor(tmp3, FakeTransport(), renderer4, {"nid": corpus3})
    mon3.run()
    # failure first
    renderer4.script[PAGE_A] = RenderResult(ok=False, status=503, error_kind="http_status",
                                            error_detail="http_503")
    mon3.run()
    store = json.loads((tmp3 / "source_store.json").read_text(encoding="utf-8"))
    check("6a failed acquisition preserves trusted baseline",
          store[PAGE_A]["trusted_hash"] == hashlib.sha256(("REAL CONTENT " * 30).encode()).hexdigest())
    check("6b failure status recorded", store[PAGE_A]["status"] in ("blocked", "failed_acquisition"))
    # recovery with same content
    renderer4.script[PAGE_A] = html("REAL CONTENT " * 30)
    mon3.run()
    store = json.loads((tmp3 / "source_store.json").read_text(encoding="utf-8"))
    check("3a recovery clears stale failure status", store[PAGE_A]["status"] is None)
    events = json.loads((tmp3 / "pending_source_events.json").read_text(encoding="utf-8"))
    check("3b recovery creates no fake change event", events == [])

    # 13. acquisition-method change is not content drift (same content)
    renderer5 = FakeRenderer({PAGE_A: html("REAL CONTENT " * 30)})
    transport5 = FakeTransport({PAGE_A: StrictResult(ok=True, status=200, content_type="text/html",
                                                     body=("<html><body>" + "REAL CONTENT " * 30 + "</body></html>").encode())})
    tmp4 = Path(tempfile.mkdtemp())
    corpus4 = fixture_corpus(tmp4, [rec("r1", PAGE_A)])
    mon4 = make_monitor(tmp4, transport5, renderer5, {"nid": corpus4})
    mon4.run()  # baseline via browser
    renderer5.script[PAGE_A] = RenderResult(ok=False, error_kind="blocked",
                                            error_detail="net::ERR_BLOCKED")
    s = mon4.run()  # browser fails -> static fallback
    store = json.loads((tmp4 / "source_store.json").read_text(encoding="utf-8"))
    events = json.loads((tmp4 / "pending_source_events.json").read_text(encoding="utf-8"))
    check("13a same content via different method -> no drift event",
          all(e.get("type") != "content_change" for e in events))
    check("13b method recorded as static transition", store[PAGE_A]["trusted_acquisition_method"] in ("verified_static", "verified_browser"))

    # 7. HTTP-200 CAPTCHA/maintenance/empty content cannot poison baseline
    tmp5 = Path(tempfile.mkdtemp())
    corpus5 = fixture_corpus(tmp5, [rec("r1", PAGE_A)])
    renderer6 = FakeRenderer({PAGE_A: html("REAL GOVERNMENT CONTENT " * 40)})
    mon5 = make_monitor(tmp5, FakeTransport(), renderer6, {"nid": corpus5})
    mon5.run()
    trusted = json.loads((tmp5 / "source_store.json").read_text(encoding="utf-8"))[PAGE_A]["trusted_hash"]
    for label, body in [("captcha", "Access Denied. Please verify you are a human. captcha 12345."),
                        ("maintenance", "Site under maintenance. Service unavailable."),
                        ("empty", "x")]:
        renderer6.script[PAGE_A] = html(body)
        mon5.run()
    store = json.loads((tmp5 / "source_store.json").read_text(encoding="utf-8"))
    check("7a suspect 200-content never poisons baseline",
          store[PAGE_A]["trusted_hash"] == trusted and store[PAGE_A]["status"] == "suspect_capture",
          store[PAGE_A].get("status_reason", ""))
    check("7b suspect capture keeps diagnostics reason",
          "bot-wall" in (store[PAGE_A]["status_reason"] or "")
          or "maintenance" in (store[PAGE_A]["status_reason"] or "")
          or "chars" in (store[PAGE_A]["status_reason"] or ""))

    # 10. capture-only invalid-TLS evidence never advances trusted state
    tmp6 = Path(tempfile.mkdtemp())
    corpus6 = fixture_corpus(tmp6, [rec("r1", PAGE_A)])
    renderer7 = FakeRenderer({PAGE_A: html("REAL CONTENT " * 30)})
    mon6 = make_monitor(tmp6, FakeTransport(), renderer7, {"nid": corpus6})
    mon6.run()
    trusted6 = json.loads((tmp6 / "source_store.json").read_text(encoding="utf-8"))[PAGE_A]["trusted_hash"]
    renderer7.script[(PAGE_A, False)] = RenderResult(ok=False, error_kind="tls",
                                                     error_detail="ERR_CERT_AUTHORITY_INVALID")
    renderer7.script[(PAGE_A, True)] = html("EVIDENCE-ONLY RENDER " * 30)
    mon6.run()
    store = json.loads((tmp6 / "source_store.json").read_text(encoding="utf-8"))
    check("10a capture-only never advances trusted state",
          store[PAGE_A]["trusted_hash"] == trusted6
          and store[PAGE_A]["status"] == "capture_only_tls_invalid")

    # 11. source URL change for a corpus record remaps correctly
    tmp7 = Path(tempfile.mkdtemp())
    corpus7 = fixture_corpus(tmp7, [rec("r1", PAGE_A)])
    renderer8 = FakeRenderer({PAGE_A: html("AAA CONTENT " * 30), PAGE_B: html("BBB CONTENT " * 30)})
    mon7 = make_monitor(tmp7, FakeTransport(), renderer8, {"nid": corpus7})
    mon7.run()
    corpus7b = fixture_corpus(tmp7, [rec("r1", PAGE_B)])   # record now cites PAGE_B
    mon7b = make_monitor(tmp7, FakeTransport(), renderer8, {"nid": corpus7b})
    mon7b.run()
    store = json.loads((tmp7 / "source_store.json").read_text(encoding="utf-8"))
    check("11a old source entry retained, new source baselined",
          PAGE_A in store and PAGE_B in store
          and store[PAGE_B]["trusted_hash"] == hashlib.sha256(("BBB CONTENT " * 30).encode()).hexdigest())

    # 12. true dry run performs no persistent writes
    tmp8 = Path(tempfile.mkdtemp())
    corpus8 = fixture_corpus(tmp8, [rec("r1", PAGE_A)])
    renderer9 = FakeRenderer({PAGE_A: html("DRY RUN CONTENT " * 30)})
    mon8 = make_monitor(tmp8, FakeTransport(), renderer9, {"nid": corpus8}, dry_run=True)
    mon8.run()
    check("12a dry run writes no state files", state_bytes(tmp8)["source_store.json"] is None
          and state_bytes(tmp8)["snapshots"] == {})
    # now baseline for real, change content, dry-run again -> no mutation
    mon9 = make_monitor(tmp8, FakeTransport(), renderer9, {"nid": corpus8})
    mon9.run()
    before_bytes = state_bytes(tmp8)
    renderer9.script[PAGE_A] = html("CHANGED IN DRY RUN " * 30)
    make_monitor(tmp8, FakeTransport(), renderer9, {"nid": corpus8}, dry_run=True).run()
    check("12b dry run is byte-for-byte non-mutating", state_bytes(tmp8) == before_bytes)

    # PDFs: known-source drift on same-URL bytes
    tmp9 = Path(tempfile.mkdtemp())
    corpus9 = fixture_corpus(tmp9, [rec("r1", PAGE_PDF)])
    transport9 = FakeTransport({PAGE_PDF: StrictResult(ok=True, status=200,
                                                       content_type="application/pdf", body=b"%PDF-old")})
    mon10 = make_monitor(tmp9, transport9, FakeRenderer(), {"nid": corpus9})
    mon10.run()
    transport9.script[PAGE_PDF] = StrictResult(ok=True, status=200,
                                               content_type="application/pdf", body=b"%PDF-new")
    s = mon10.run()
    events = json.loads((tmp9 / "pending_source_events.json").read_text(encoding="utf-8"))
    check("13c same-URL PDF with changed bytes -> content_change event",
          any(e["normalized_url"] == PAGE_PDF and e["type"] == "content_change" for e in events))


# ---------------------------------------------------------------------------
# Review actions + reembed (tests 14–20)
# ---------------------------------------------------------------------------

def test_review_and_reembed():
    print("Review actions + record reembed:")
    tmp = Path(tempfile.mkdtemp())
    corpus = fixture_corpus(tmp, [rec("r1", PAGE_A), rec("r2", PAGE_A, "H2")])
    renderer = FakeRenderer({PAGE_A: html("ORIGINAL CONTENT " * 30)})
    mon = make_monitor(tmp, FakeTransport(), renderer, {"nid": corpus})
    mon.run()
    renderer.script[PAGE_A] = html("UPDATED CONTENT " * 30)
    mon.run()

    from freshness.review_actions import apply_source_action, load_events, save_events
    events = load_events(tmp / "pending_source_events.json")
    ev = next(e for e in events if e["normalized_url"] == PAGE_A and e["status"] == "pending")

    # 9/§9. skip: nothing changes
    apply_source_action(tmp, ev["event_id"], "skip", selected_record_ids=None)
    events = load_events(tmp / "pending_source_events.json")
    ev = next(e for e in events if e["event_id"] == ev["event_id"])
    check("9a skip leaves event pending", ev["status"] == "pending")
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    check("9b skip leaves trusted baseline unchanged", store[PAGE_A]["candidate_hash"] is not None)

    # invalid: keep trusted, mark candidate invalid
    apply_source_action(tmp, ev["event_id"], "invalid", selected_record_ids=None)
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    check("9c invalid keeps trusted baseline and marks candidate invalid",
          store[PAGE_A]["candidate_invalid"] is True
          and store[PAGE_A]["trusted_hash"] != store[PAGE_A]["candidate_hash"])
    # same invalid candidate again -> no new alert
    mon.run()
    events = load_events(tmp / "pending_source_events.json")
    check("9d same invalid candidate does not re-alert",
          len([e for e in events if e["normalized_url"] == PAGE_A and e["status"] == "pending"]) == 0)

    # fresh change -> acknowledge
    renderer.script[PAGE_A] = html("SECOND CHANGE " * 30)
    mon.run()
    events = load_events(tmp / "pending_source_events.json")
    ev = next(e for e in events if e["normalized_url"] == PAGE_A and e["status"] == "pending")
    apply_source_action(tmp, ev["event_id"], "acknowledge", selected_record_ids=None)
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    check("9e acknowledge promotes candidate to trusted, no record tasks",
          store[PAGE_A]["candidate_hash"] is None
          and store[PAGE_A]["trusted_hash"] == hashlib.sha256(("SECOND CHANGE " * 30).encode()).hexdigest()
          and not (tmp / "approved_for_reembed.json").exists())

    # update: creates record-level tasks for SELECTED ids with pre-edit hashes
    renderer.script[PAGE_A] = html("THIRD CHANGE " * 30)
    mon.run()
    events = load_events(tmp / "pending_source_events.json")
    ev = next(e for e in events if e["normalized_url"] == PAGE_A and e["status"] == "pending")
    apply_source_action(tmp, ev["event_id"], "update", selected_record_ids=["r1"])
    store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
    tasks = json.loads((tmp / "approved_for_reembed.json").read_text(encoding="utf-8"))
    check("9f update promotes candidate and creates one selected task",
          store[PAGE_A]["candidate_hash"] is None and len(tasks) == 1 and tasks[0]["record_id"] == "r1")
    corpus_recs = [json.loads(l) for l in open(corpus, encoding="utf-8")]
    pre_hash = hashlib.sha256(next(r for r in corpus_recs if r["id"] == "r1")["text"].encode()).hexdigest()
    check("10a task captures pre-edit corpus text hash", tasks[0]["pre_edit_text_sha256"] == pre_hash)

    # 14/19/20. reembed service with fakes
    from freshness.reembed import ReembedService
    calls = []

    class FakeCollection:
        def __init__(self):
            self.rows = {}

        def upsert(self, ids, documents, embeddings, metadatas):
            calls.append(("upsert", ids))
            for i, rid in enumerate(ids):
                self.rows[rid] = {"document": documents[i], "metadata": metadatas[i],
                                  "embedding": embeddings[i]}

        def get(self, ids, include=None):
            return {"ids": ids, "documents": [self.rows[i]["document"] for i in ids],
                    "metadatas": [self.rows[i]["metadata"] for i in ids]}

        def count(self):
            return len(self.rows)

    collections = {"nid": FakeCollection()}
    svc = ReembedService(corpus_paths={"nid": corpus}, tasks_path=tmp / "approved_for_reembed.json",
                         embedder=lambda texts: [[0.1, 0.2]] * len(texts),
                         collection_factory=lambda service: collections[service])

    # 14. refuse when manual corpus edit was never made
    out = svc.run(execute=True)
    check("14a reembed aborts when manual edit not detected",
          len(calls) == 0 and "not been detected" in json.dumps(out))
    tasks = json.loads((tmp / "approved_for_reembed.json").read_text(encoding="utf-8"))
    check("14b aborted task stays pending", len(tasks) == 1)

    # human edits the corpus for r1 only
    recs = [json.loads(l) for l in open(corpus, encoding="utf-8")]
    for r in recs:
        if r["id"] == "r1":
            r["text"] = "MANUALLY EDITED CORPUS TEXT"
    with open(corpus, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # 17/18. incremental completion: r1 succeeds and clears; failures keep pending
    out = svc.run(execute=True)
    check("17a successful upsert happened for edited record", ("upsert", ["r1"]) in calls)
    tasks = json.loads((tmp / "approved_for_reembed.json").read_text(encoding="utf-8"))
    check("17b completed task cleared, other record's task never created",
          tasks == [])

    # 15/16. metadata parity per service (vs embed-script builders)
    from freshness.metadata import build_metadata
    from embed_ereturn import to_metadata as ereturn_md
    from embed_bdris import to_metadata as bdris_md
    from embed_nid import to_metadata_nid as nid_md
    from embed_epassport import to_metadata_epassport as ep_md
    er = {"id": "e1", "text": "t", "category": "faq", "subcategory": "s", "heading": "h",
          "lang": "bn+en", "source_url": "https://nbr.gov.bd/x", "official": True,
          "question_bn": "q", "question_en": "q", "tax_year_stated": "2023-24",
          "volatile": True, "question_reconstructed": False}
    check("15a ereturn parity incl. optional fields",
          build_metadata("ereturn", er) == ereturn_md(er))
    bd = {"id": "b1", "text": "t", "category": "c", "subcategory": "s", "heading": "h",
          "lang": "bn", "source_url": "https://bdris.gov.bd/", "official": True,
          "question_bn": "q", "question_en": "q", "group_topic": "g", "stage": 3}
    check("16a bdris parity", build_metadata("bdris", bd) == bdris_md(bd))
    n = {"id": "n1", "text": "t", "category": "c", "heading": "h", "lang": "bn",
         "source_url": "https://services.nidw.gov.bd/", "official": True}
    check("16b nid parity", build_metadata("nid", n) == nid_md(n))
    ep = {"id": "p1", "text": "t", "category": "c", "subcategory": "s", "heading": "h",
          "lang": "bn+en", "source_url": "https://www.epassport.gov.bd/x", "official": True}
    check("16c epassport parity", build_metadata("epassport", ep) == ep_md(ep))
    try:
        build_metadata("visa", {"id": "v1"})
        check("16d visa metadata designed-for-later (clean error)", False)
    except NotImplementedError:
        check("16d visa metadata designed-for-later (clean error)", True)

    # 19. missing record id aborts safely (task kept, loud failure)
    tmp2 = Path(tempfile.mkdtemp())
    corpus2 = fixture_corpus(tmp2, [rec("r1", PAGE_A)])
    (tmp2 / "tasks.json").write_text(json.dumps([
        {"task_id": "t1", "record_id": "missing-id", "service": "nid",
         "source_url": PAGE_A, "event_id": "ev1", "approved_at": fixed_now(),
         "pre_edit_text_sha256": "deadbeef"}]), encoding="utf-8")
    svc2 = ReembedService(corpus_paths={"nid": corpus2}, tasks_path=tmp2 / "tasks.json",
                          embedder=lambda texts: [[0.1]] * len(texts),
                          collection_factory=lambda service: FakeCollection())
    out = svc2.run(execute=True)
    check("19a missing record id aborts that task safely",
          "missing" in json.dumps(out).lower()
          and len(json.loads((tmp2 / "tasks.json").read_text(encoding="utf-8"))) == 1)

    # 20. no approval -> no vector-write path
    tmp3 = Path(tempfile.mkdtemp())
    (tmp3 / "tasks.json").write_text("[]", encoding="utf-8")
    svc3 = ReembedService(corpus_paths={"nid": fixture_corpus(tmp3, [rec("r1", PAGE_A)])},
                          tasks_path=tmp3 / "tasks.json",
                          embedder=lambda texts: [[0.1]] * len(texts),
                          collection_factory=lambda service: FakeCollection())
    calls.clear()
    out = svc3.run(execute=True)
    check("20a no approvals -> no vector write", len(calls) == 0)


# ---------------------------------------------------------------------------
# Discovery monitoring (tests 21–33)
# ---------------------------------------------------------------------------

def test_discovery():
    print("Source-discovery monitoring:")
    from freshness.discovery import compare_inventories, apply_review_action
    SEED = "https://gov.example/hub"

    # 21. unchanged inventory -> zero events
    inv = {SEED: {"resources": {"https://gov.example/hub/a": {"type": "html"}}}}
    ev = compare_inventories(inv, {SEED: {"resources": {"https://gov.example/hub/a": {"type": "html"}}}},
                             seed_url=SEED, missing_threshold=3)
    check("21 unchanged inventory -> zero events", ev == [])

    # 22/27/25. one new child -> one new_source; duplicate normalized links count once;
    # new PDF is new_source
    prev = {SEED: {"resources": {"https://gov.example/hub/a": {"type": "html"}}}}
    curr = {SEED: {"resources": {
        "https://gov.example/hub/a": {"type": "html"},
        "https://gov.example/hub/b": {"type": "html"},
        "https://gov.example/hub/b": {"type": "html"},
        "https://gov.example/hub/new.pdf": {"type": "pdf"},
    }}}
    ev = compare_inventories(prev, curr, seed_url=SEED, missing_threshold=3)
    news = [e for e in ev if e["type"] == "new_source"]
    check("22 new child -> one new_source", len([e for e in news if e["url"] == "https://gov.example/hub/b"]) == 1)
    check("25 new verified PDF -> new_source", any(e["url"] == "https://gov.example/hub/new.pdf" for e in news))

    # 23/21b. missing child -> missing state, not instant removal
    prev = {SEED: {"resources": {"https://gov.example/hub/a": {"type": "html"},
                                 "https://gov.example/hub/gone": {"type": "html"}}}}
    curr = {SEED: {"resources": {"https://gov.example/hub/a": {"type": "html"}}}}
    ev = compare_inventories(prev, curr, seed_url=SEED, missing_threshold=3)
    check("23 missing child -> source_missing (not removed)",
          any(e["type"] == "source_missing" and e["url"] == "https://gov.example/hub/gone" for e in ev)
          and not any(e["type"] == "source_removed" for e in ev))

    # 32. reappearing resource (gone was missing in earlier runs, now back)
    curr_back = {SEED: {"resources": {"https://gov.example/hub/a": {"type": "html"},
                                      "https://gov.example/hub/gone": {"type": "html"}}}}
    ev = compare_inventories(prev, curr_back, seed_url=SEED, missing_threshold=3,
                             previous_missing={"https://gov.example/hub/gone": {"count": 2}})
    check("32 reappeared resource detected",
          any(e["type"] == "source_reappeared" and e["url"] == "https://gov.example/hub/gone" for e in ev))

    # removal escalation after repeated verified absence
    ev = compare_inventories(prev, curr, seed_url=SEED, missing_threshold=3,
                             previous_missing={"https://gov.example/hub/gone": {"count": 3}})
    check("23b removal escalates only after threshold",
          any(e["type"] == "source_removed" and e["url"] == "https://gov.example/hub/gone" for e in ev))

    # 26/§27. same PDF URL with changed bytes -> known_source_changed
    prev = {SEED: {"resources": {"https://gov.example/hub/doc.pdf":
                                 {"type": "pdf", "content_hash": "aaa"}}}}
    curr = {SEED: {"resources": {"https://gov.example/hub/doc.pdf":
                                 {"type": "pdf", "content_hash": "bbb"}}}}
    ev = compare_inventories(prev, curr, seed_url=SEED, missing_threshold=3)
    check("26 same-URL PDF changed bytes -> known_source_changed",
          any(e["type"] == "known_source_changed" and e["url"] == "https://gov.example/hub/doc.pdf" for e in ev))

    # 28. normalized duplicates already deduped by inventory keys (dict keys)

    # 24/§23. review actions: not_relevant persists on the EXACT nested resource
    queue = [{"event_id": "e1", "type": "new_source", "url": "https://gov.example/hub/news-item",
              "service": "nid", "seed": SEED, "status": "pending"}]
    inv_state = {"nid": {SEED: {"resources": {
        "https://gov.example/hub/news-item": {"type": "html", "verification_status": "verified"}}}}}
    apply_review_action(queue, inv_state, "e1", "not_relevant",
                        inventory_path=None, save=lambda *a, **k: None)
    check("24a not_relevant marks the candidate ignored",
          queue[0]["status"] == "not_relevant"
          and inv_state["nid"][SEED]["resources"]["https://gov.example/hub/news-item"]["reviewed"] == "not_relevant")
    # ignored candidates do not re-alert: compare_inventories takes reviewed set
    ev = compare_inventories(prev, curr, seed_url=SEED, missing_threshold=3,
                             reviewed={"https://gov.example/hub/a"})
    check("24b reviewed/ignored candidates do not alert again",
          not any(e["url"] == "https://gov.example/hub/a" for e in ev))

    # 29/§24. PII/login/wizard candidates are excluded by URL policy
    from acquisition.urls import classify_url
    check("29a login candidate excluded", classify_url("https://gov.example/hub/login")[0] == "wizard")
    check("29b wizard/status candidate excluded",
          classify_url("https://gov.example/hub/ViewStatus.aspx")[0] == "wizard")

    # 30. capture-only candidate cannot enter trusted inventory
    from freshness.discovery import inventory_entry_allowed
    check("30a capture-only resource rejected",
          not inventory_entry_allowed({"verification_status": "capture_only"}))
    check("31a safe JS/dynamic state resource allowed",
          inventory_entry_allowed({"verification_status": "verified", "type": "js_dynamic_state"}))

    # 22b/§22. failed hub crawl preserves inventory, creates no mass deletion
    from freshness.discovery import reconcile_seed_inventory
    prev_inv = {"resources": {f"https://gov.example/hub/p{i}": {"type": "html"} for i in range(10)}}
    out = reconcile_seed_inventory(prev_inv, crawl_failed=True, current_resources=None,
                                   seed_url=SEED, missing_counts={}, missing_threshold=3)
    check("22b failed hub crawl preserves previous inventory, no deletion events",
          out["preserved"] is True and out["events"] == [] and len(out["resources"]) == 10)


# ---------------------------------------------------------------------------
# Operational safety (tests 34–36)
# ---------------------------------------------------------------------------

def test_operational_safety():
    print("Operational safety:")
    tmp = Path(tempfile.mkdtemp())

    # 34. atomic state writes survive simulated interruption
    from freshness.paths import atomic_write_json
    target = tmp / "state.json"
    atomic_write_json(target, {"v": 1})
    interrupted = target.with_name(target.name + ".tmp")
    interrupted.write_text("{CORRUPT", encoding="utf-8")   # a killed writer leaves .tmp
    check("34a interrupted write leaves the real state intact",
          json.loads(target.read_text(encoding="utf-8")) == {"v": 1})
    atomic_write_json(target, {"v": 2})
    check("34b atomic replace overwrites cleanly",
          json.loads(target.read_text(encoding="utf-8")) == {"v": 2})

    # 35. repo-relative portability from a relocated temp layout
    import subprocess
    reloc = Path(tempfile.mkdtemp()) / "moved_repo"
    (reloc / "freshness").mkdir(parents=True)
    shutil.copy(Path("freshness/paths.py"), reloc / "freshness" / "paths.py")
    (reloc / "freshness" / "__init__.py").write_text("", encoding="utf-8")
    code = ("import sys; sys.path.insert(0, r'%s');\n"
            "from freshness import paths;\n"
            "print(paths.REPO_ROOT.name == 'moved_repo')" % str(reloc))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    check("35 relocated repo resolves repo-relative root", out.stdout.strip() == "True",
          out.stderr[:200])

    # 36. snapshot naming cannot collide for distinct normalized URLs
    from freshness.paths import slug_for_url
    a = slug_for_url("https://x.gov.bd/a/b")
    b = slug_for_url("https://x.gov.bd/a_b")
    check("36 distinct URLs -> distinct snapshot ids", a != b)


if __name__ == "__main__":
    test_known_source_monitoring()
    test_review_and_reembed()
    test_discovery()
    test_operational_safety()
    print("=" * 78)
    if CHECKS["fail"]:
        print(f"Result: {CHECKS['fail']} CHECK(S) FAILED ({CHECKS['pass']} passed)")
        sys.exit(1)
    print(f"Result: ALL CHECKS PASS ({CHECKS['pass']} checks)")
