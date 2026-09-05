"""
tests/maintenance/test_p00_baseline.py — P00 packet tests.

Covers (G0 P00 §2/§5):
  * the offline guard fails closed on a deliberate network probe
  * the carried transport contract: real RequestsTransport.fetch preserves
    the observed final destination; dataclass defaults; Capture provenance
  * a real KnownSourceMonitor.run consuming those result types in temporary
    fixture directories, with fully injected robots/session/renderer fakes
  * the carried acquire/crawler allowed-domain argument rejects an external
    final URL (R1 reconciliation scope; NOT pre-request blocking — P02/P03)

These tests must run with zero attempted network under tools/run_offline_checks.py.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "tools" / "run_offline_checks.py"

PAGE_A = "https://gov.example/source"


def fixture_corpus(tmp: Path, records, name="nid") -> Path:
    p = tmp / f"{name}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


def rec(record_id, url, heading="H"):
    return {"id": record_id, "source_url": url, "heading": heading,
            "text": f"corpus text {record_id}", "category": "info",
            "lang": "en", "official": True}


class TestNetworkProbeFailsClosed(unittest.TestCase):
    """P00 §2: the guard cannot silently convert attempts into successful
    offline tests — the deliberate probe must exit nonzero before any network
    access and print an attempted-network diagnostic."""

    def test_network_probe_fails_closed(self):
        runner = RUNNER
        self.assertTrue(runner.exists(), f"runner missing: {runner}")
        result = subprocess.run(
            [sys.executable, "-B", str(runner), "--probe-network"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            timeout=120)
        self.assertNotEqual(result.returncode, 0,
                            f"probe exited 0 — guard did not fail closed:\n{result.stdout}")
        combined = result.stdout + result.stderr
        self.assertIn("NETWORK_ATTEMPT", combined)
        self.assertIn("gov.example", combined)


class TestTransportContract(unittest.TestCase):
    """P00 §5: the carried final_url/provenance contract, through the real
    RequestsTransport.fetch producer with the session injected at its
    external boundary."""

    def test_http_result_preserves_observed_destination(self):
        from acquisition.transport import RequestsTransport
        t = RequestsTransport()
        t.session = Mock()
        t.session.get.return_value = SimpleNamespace(
            status_code=200, headers={"content-type": "text/html"},
            content=b"page", url="https://gov.example/final")
        result = t.fetch("https://gov.example/start")
        self.assertTrue(result.ok)
        self.assertEqual(result.final_url, "https://gov.example/final")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body, b"page")

    def test_strict_result_defaults_are_conservative(self):
        from acquisition.transport import StrictResult
        r = StrictResult(ok=False)
        self.assertEqual(r.final_url, "")
        self.assertIsNone(r.status)
        self.assertEqual(r.body, b"")

    def test_render_result_defaults_are_conservative(self):
        from acquisition.transport import RenderResult
        r = RenderResult(ok=False)
        self.assertEqual(r.final_url, "")
        self.assertEqual(r.text, "")
        self.assertEqual(r.screenshot_png, b"")

    def test_capture_to_provenance_records_destination_fields(self):
        from acquisition.acquire import Capture
        cap = Capture(url="https://gov.example/start",
                      normalized_url="https://gov.example/start",
                      method="verified_browser", disposition="captured",
                      verification_status="verified", tls_verified=True,
                      requested_url="https://gov.example/start",
                      final_url="https://gov.example/final",
                      redirect_occurred=True)
        prov = cap.to_provenance()
        self.assertEqual(prov["requested_url"], "https://gov.example/start")
        self.assertEqual(prov["final_url"], "https://gov.example/final")
        self.assertTrue(prov["redirect_occurred"])
        self.assertEqual(prov["acquisition_method"], "verified_browser")


class TestKnownSourceMonitorConsumer(unittest.TestCase):
    """P00 §5: a real KnownSourceMonitor.run consumes the carried result types
    end-to-end in temporary fixture directories (injected transport/renderer/
    robots — no network)."""

    def _monitor(self, tmp, script, robots, record_ids=None):
        from test_freshness import FakeRenderer, FakeTransport, make_monitor
        corpus = fixture_corpus(tmp, [rec("r1", PAGE_A)])
        return make_monitor(tmp, FakeTransport(), FakeRenderer(script),
                            {"nid": corpus}, robots=robots, record_ids=record_ids), corpus

    def test_baseline_then_change_produces_candidate_and_event(self):
        tmp = Path(tempfile.mkdtemp())
        from tests.maintenance.fixtures import robots_for_hosts
        robots = robots_for_hosts({"gov.example"})
        mon, corpus = self._monitor(tmp, {PAGE_A: html_result("BASE " * 30)}, robots)
        summary = mon.run()
        store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
        self.assertIn(PAGE_A, store)
        self.assertEqual(store[PAGE_A]["trusted_acquisition_method"], "verified_browser")
        self.assertEqual(summary["new_baselines"], 1)
        # change: trusted preserved, candidate + ONE source event
        mon2, _ = self._monitor(tmp, {PAGE_A: html_result("CHANGED " * 30)}, robots)
        summary2 = mon2.run()
        store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
        self.assertNotEqual(store[PAGE_A]["candidate_hash"], None)
        events = json.loads((tmp / "pending_source_events.json").read_text(encoding="utf-8"))
        pending = [e for e in events if e["status"] == "pending"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(sorted(i["record_id"] for i in pending[0]["impacted_records"]),
                         ["r1"])

    def test_monitor_is_hermetic_with_injected_robots(self):
        # with pre-seeded robots the whole run performs zero network attempts
        # even though production robots_check would otherwise fetch robots.txt
        tmp = Path(tempfile.mkdtemp())
        from tests.maintenance.fixtures import robots_for_hosts
        mon, _ = self._monitor(tmp, {PAGE_A: html_result("HERMETIC " * 30)},
                               robots_for_hosts({"gov.example"}))
        mon.run()
        # no snapshot/persisted state escapes the supplied tmp root
        for entry in json.loads((tmp / "source_store.json").read_text(encoding="utf-8")).values():
            if entry.get("trusted_snapshot"):
                self.assertTrue(str(entry["trusted_snapshot"]).startswith(str(tmp)),
                                entry["trusted_snapshot"])


def html_result(text, status=200):
    from acquisition.transport import RenderResult
    return RenderResult(ok=status < 400, status=status, text=text,
                        html=f"<html>{text}</html>", screenshot_png=b"",
                        final_url="https://gov.example/source")


class TestAcquireAllowlistGate(unittest.TestCase):
    """P00 §5: the carried allowed-domain argument reaches capture and rejects
    an external final URL (existing local behavior; NOT pre-request
    redirect blocking — that is P02/P03 scope)."""

    def test_external_final_url_is_rejected_not_trusted(self):
        from acquisition.acquire import acquire_url
        from acquisition.robots import RobotsCache
        from acquisition.transport import StrictResult
        from unittest.mock import Mock
        from tests.maintenance.fixtures import robots_for_hosts

        transport = RequestsTransportStub(final_url="https://unrelated.example.net/doc.pdf",
                                          content_type="application/pdf", body=b"%PDF")
        renderer = None
        cap = acquire_url("https://gov.example/doc.pdf", transport=transport,
                          renderer=renderer, robots=robots_for_hosts({"gov.example"}),
                          out_dir=tempfile.mkdtemp(), user_agent="FreshnessChecker",
                          allowed_domains={"gov.example"})
        self.assertEqual(cap.disposition, "redirected_out_of_allowlist")
        self.assertEqual(cap.pdf_path, "")
        self.assertEqual(cap.final_url, "https://unrelated.example.net/doc.pdf")
        self.assertTrue(cap.redirect_occurred)


class RequestsTransportStub:
    """Minimal strict-transport stub returning a scripted final destination;
    the production acquire_url path runs unmodified around it."""

    def __init__(self, final_url, content_type, body):
        self.final_url = final_url
        self.content_type = content_type
        self.body = body

    def fetch(self, url):
        from acquisition.transport import StrictResult
        return StrictResult(ok=True, status=200, content_type=self.content_type,
                            body=self.body, final_url=self.final_url)


class TestCrawlerAllowlistRegression(unittest.TestCase):
    """Crawler threads its allowlist into acquire; an out-of-allowlist
    destination is never captured (carried behavior)."""

    def test_crawler_rejects_external_destination(self):
        from acquisition.crawler import CrawlConfig, Crawler
        from acquisition.transport import RenderResult
        from tests.maintenance.fixtures import patch_crawler_robots

        class ScriptedRenderer:
            """Minimal (url, ignore_tls)-scripted renderer for the crawler."""

            def __init__(self, script):
                self.script = script

            def goto(self, url, *, ignore_tls=False):
                return self.script.get((url, ignore_tls))                     or self.script.get(url)                     or RenderResult(ok=False, error_kind="blocked",
                                    error_detail="net::ERR_UNSCRIPTED")

            def discover_links(self, url):
                return []

            def ensure_on(self, url):
                pass

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

        A = "https://gov.example/"
        renderer = ScriptedRenderer({(A, False): RenderResult(
            ok=True, status=200, text="HUB " * 30, html="<html></html>",
            screenshot_png=b"", final_url="https://unrelated.example.net/")})
        from acquisition.transport import StrictResult

        class OfflineTransport:
            """Strict transport that never succeeds, so the crawl proceeds to
            the browser path where the scripted redirect lives."""

            def fetch(self, url):
                return StrictResult(ok=False, error_kind="connection",
                                    error_detail="stub: offline", final_url=url)

        with patch_crawler_robots({"gov.example"}):
            crawler = Crawler(CrawlConfig(seeds=[A], allowlist={"gov.example"},
                                          max_depth=2, interval_seconds=0),
                              transport=OfflineTransport(), renderer=renderer,
                              out_dir=tempfile.mkdtemp())
            manifest = crawler.run()
        captured = [n for n in manifest["nodes"]
                    if str(n.get("disposition", "")).startswith("captured")]
        self.assertEqual(captured, [], "out-of-allowlist destination must not be captured")


def html_result_text(text, status=200):
    from acquisition.transport import RenderResult
    return RenderResult(ok=status < 400, status=status, text=text,
                        html=f"<html>{text}</html>", screenshot_png=b"",
                        final_url="")


if __name__ == "__main__":
    unittest.main()
