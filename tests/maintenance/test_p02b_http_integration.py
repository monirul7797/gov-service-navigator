"""
tests/maintenance/test_p02b_http_integration.py — P02B integration tests.

Binding, acquisition, and known-monitor integration with the real
RequestsTransport, real acquire_url, and real KnownSourceMonitor.run.
All state lives under temporary test roots. Zero network by construction
(scripted session + injected renderer/robots).
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_freshness import (FakeRenderer, FakeTransport, fixed_now, rec,
                            state_bytes)

from acquisition.transport import StrictResult
from tests.maintenance.fixtures import make_pdf, robots_for_hosts, scripted_http_session

A = "https://gov.example/a"
B = "https://gov.example/b"
PDF = "https://gov.example/doc.pdf"
EXTERNAL = "https://external.example/x"
HOSTS = frozenset({"gov.example"})


def _resp(status, *, url, headers=None, body=b"page", final_url=None):
    d = {"status": status, "headers": headers or {}, "body": body,
         "final_url": final_url if final_url is not None else url, "history": []}
    return d


def html_result(text, status=200, final_url=""):
    from acquisition.transport import RenderResult
    return RenderResult(ok=status < 400, status=status, text=text,
                        html=f"<html>{text}</html>", screenshot_png=b"",
                        final_url=final_url)


class TestTransportBinding(unittest.TestCase):
    """P02B §1: binding returns a distinct object sharing the session."""

    def test_distinct_object_shares_session(self):
        from acquisition.transport import RequestsTransport
        session = scripted_http_session({})
        original = RequestsTransport(session=session)
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"gov.example"}))
        bound = original.with_navigation_policy(policy)
        self.assertIsNot(original, bound)
        self.assertIs(bound.session, original.session)
        self.assertEqual(bound.timeout, original.timeout)
        self.assertIsNot(bound.navigation_policy, original.navigation_policy)

    def test_no_policy_returns_same_policy(self):
        session = scripted_http_session({})
        from acquisition.transport import RequestsTransport
        from acquisition.navigation_policy import NavigationPolicy
        original = RequestsTransport(session=session)
        policy = NavigationPolicy(frozenset({"gov.example"}))
        bound = original.with_navigation_policy(policy)
        self.assertIs(bound.navigation_policy, policy)

    def test_intersection_of_two_policies(self):
        from acquisition.transport import RequestsTransport
        from acquisition.navigation_policy import NavigationPolicy
        session = scripted_http_session({})
        p1 = NavigationPolicy(frozenset({"gov.example", "other.example"}))
        original = RequestsTransport(navigation_policy=p1, session=session)
        p2 = NavigationPolicy(frozenset({"gov.example"}), max_redirects=2)
        bound = original.with_navigation_policy(p2)
        self.assertTrue(bound.navigation_policy.allows("https://gov.example/a"))
        self.assertFalse(bound.navigation_policy.allows("https://other.example/a"))
        self.assertEqual(bound.navigation_policy.max_redirects, 2)

    def test_original_unchanged_after_binding(self):
        from acquisition.transport import RequestsTransport
        from acquisition.navigation_policy import NavigationPolicy
        session = scripted_http_session({})
        p1 = NavigationPolicy(frozenset({"gov.example", "other.example"}), max_redirects=5)
        original = RequestsTransport(navigation_policy=p1, session=session)
        p2 = NavigationPolicy(frozenset({"gov.example"}), max_redirects=1)
        original.with_navigation_policy(p2)
        self.assertEqual(original.navigation_policy.max_redirects, 5)
        self.assertTrue(original.navigation_policy.allows("https://other.example/a"))

    def test_two_bound_copies_cannot_widen_each_other(self):
        from acquisition.transport import RequestsTransport
        from acquisition.navigation_policy import NavigationPolicy
        session = scripted_http_session({})
        original = RequestsTransport(session=session)
        p1 = NavigationPolicy(frozenset({"a.example"}))
        p2 = NavigationPolicy(frozenset({"b.example"}))
        b1 = original.with_navigation_policy(p1)
        b2 = original.with_navigation_policy(p2)
        self.assertFalse(b1.navigation_policy.allows("https://b.example/a"))
        self.assertFalse(b2.navigation_policy.allows("https://a.example/a"))


class TestAcquirePolicyBinding(unittest.TestCase):
    """P02B §2: acquire_url derives policy, binds transport, terminal errors."""

    def _acquire(self, url, *, routes, renderer_script=None, allowed_domains=None,
                 out_dir=None, also_screenshot=False):
        from acquisition.acquire import acquire_url
        from acquisition.robots import RobotsCache
        from acquisition.transport import RequestsTransport
        from tests.maintenance.fixtures import robots_for_hosts
        session = scripted_http_session(routes)
        from acquisition.transport import RequestsTransport as RT
        transport = RT(session=session)
        renderer = FakeRenderer(renderer_script or {})
        out = out_dir or tempfile.mkdtemp(prefix="p02b_")
        robots = robots_for_hosts({"gov.example", "external.example"})
        return acquire_url(
            url, transport=transport, renderer=renderer, robots=robots,
            out_dir=out, user_agent="FreshnessChecker",
            also_screenshot=also_screenshot, allowed_domains=allowed_domains,
        ), session, renderer, out

    def test_allowed_redirect_to_external_terminal(self):
        routes = {
            A: _resp(302, url=A, headers={"Location": EXTERNAL}),
        }
        cap, session, renderer, out_dir = self._acquire(
            A, routes=routes, allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "redirected_out_of_allowlist")
        self.assertFalse(cap.verification_status == "verified")
        self.assertEqual(session.urls, [A], "external destination was contacted")
        self.assertEqual(len(renderer.goto_log), 0, "browser was invoked after HTTP denial")
        artifacts = list(Path(out_dir).iterdir()) if Path(out_dir).exists() else []
        self.assertEqual(artifacts, [], "artifacts written for denied capture")

    def test_pdf_redirect_to_external_terminal(self):
        body = make_pdf("PDF-EXTERNAL")
        routes = {
            PDF: _resp(302, url=PDF, headers={"Location": EXTERNAL}),
        }
        cap, session, renderer, out_dir = self._acquire(
            PDF, routes=routes, allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "redirected_out_of_allowlist")
        self.assertEqual(session.urls, [PDF])
        self.assertEqual(len(renderer.goto_log), 0)

    def test_login_redirect_terminal(self):
        routes = {
            A: _resp(302, url=A, headers={"Location": "https://gov.example/login"}),
        }
        cap, session, _, _ = self._acquire(A, routes=routes, allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "redirected_out_of_allowlist")
        self.assertEqual(session.urls, [A])

    def test_loop_terminal(self):
        routes = {
            A: _resp(302, url=A, headers={"Location": A}),
        }
        cap, session, _, _ = self._acquire(A, routes=routes, allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "failed_fetch")
        self.assertEqual(session.urls, [A])

    def test_missing_location_terminal(self):
        routes = {
            A: _resp(302, url=A, headers={}),
        }
        cap, session, _, _ = self._acquire(A, routes=routes, allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "failed_fetch")
        self.assertEqual(session.urls, [A])

    def test_limit_terminal(self):
        routes = {}
        urls = [A, B, "https://gov.example/c", "https://gov.example/d",
                "https://gov.example/e", "https://gov.example/f",
                "https://gov.example/g"]
        for i in range(len(urls) - 1):
            routes[urls[i]] = _resp(302, url=urls[i], headers={"Location": urls[i + 1]})
        cap, session, _, _ = self._acquire(A, routes=routes, allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "failed_fetch")
        self.assertEqual(len(session.urls), 6)

    def test_screenshot_denied_no_artifact(self):
        routes = {
            A: _resp(302, url=A, headers={"Location": EXTERNAL}),
        }
        out_dir = tempfile.mkdtemp(prefix="p02b_shot_")
        cap, session, _, _ = self._acquire(
            A, routes=routes, allowed_domains=HOSTS, out_dir=out_dir,
            also_screenshot=True)
        self.assertEqual(cap.disposition, "redirected_out_of_allowlist")
        artifacts = list(Path(out_dir).iterdir()) if Path(out_dir).exists() else []
        self.assertEqual(artifacts, [])

    def test_exact_host_denies_subdomain(self):
        routes = {
            "https://www.gov.example/a": _resp(200, url="https://www.gov.example/a"),
        }
        cap, session, renderer, _ = self._acquire(
            "https://www.gov.example/a", routes=routes, allowed_domains=HOSTS)
        self.assertNotEqual(cap.disposition, "captured")
        self.assertEqual(session.urls, [], "subdomain contacted with exact-host policy")

    def test_wildcard_allows_subdomain(self):
        routes = {
            "https://www.gov.example/a": _resp(200, url="https://www.gov.example/a"),
        }
        cap, session, renderer, _ = self._acquire(
            "https://www.gov.example/a", routes=routes,
            allowed_domains={"*.gov.example"})
        self.assertEqual(cap.disposition, "captured")

    def test_empty_allowlist_denies(self):
        routes = {
            A: _resp(200, url=A),
        }
        cap, session, _, _ = self._acquire(A, routes=routes, allowed_domains=set())
        self.assertEqual(cap.disposition, "redirected_out_of_allowlist")
        self.assertEqual(session.urls, [])

    def test_no_allowlist_derives_hostname(self):
        routes = {
            A: _resp(200, url=A),
        }
        cap, session, _, _ = self._acquire(A, routes=routes, allowed_domains=None)
        self.assertEqual(cap.disposition, "captured")

    def test_malformed_original_url_rejected(self):
        cap, session, _, _ = self._acquire(
            "https://user:pass@gov.example/a", routes={},
            allowed_domains=HOSTS)
        self.assertNotEqual(cap.disposition, "captured")

    def test_valid_same_host_html_succeeds(self):
        routes = {
            A: _resp(200, url=A, body=("<html><body>" + "OK " * 100 + "</body></html>").encode()),
        }
        cap, session, _, _ = self._acquire(A, routes=routes, allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "captured")
        self.assertEqual(cap.method, "verified_normal")

    def test_valid_same_host_pdf_succeeds(self):
        body = make_pdf("P02B-PDF")
        routes = {
            PDF: _resp(200, url=PDF, body=body, headers={"content-type": "application/pdf"}),
        }
        cap, session, _, _ = self._acquire(
            PDF, routes=routes, allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "captured")
        self.assertEqual(cap.method, "direct_pdf_verified")
        self.assertEqual(cap.content_sha256, hashlib.sha256(body).hexdigest())

    def test_transport_policy_narrower_than_caller_still_denies(self):
        routes = {
            A: _resp(200, url=A),
            EXTERNAL: _resp(200, url=EXTERNAL),
        }
        session = scripted_http_session(routes)
        from acquisition.transport import RequestsTransport
        from acquisition.navigation_policy import NavigationPolicy
        narrow = NavigationPolicy(frozenset({"gov.example"}))
        transport = RequestsTransport(navigation_policy=narrow, session=session)
        renderer = FakeRenderer({})
        from acquisition.robots import RobotsCache
        from tests.maintenance.fixtures import robots_for_hosts
        robots = robots_for_hosts({"gov.example", "external.example"})
        from acquisition.acquire import acquire_url
        cap = acquire_url(
            A, transport=transport, renderer=renderer, robots=robots,
            out_dir=tempfile.mkdtemp(prefix="p02b_"), user_agent="FreshnessChecker",
            allowed_domains=frozenset({"gov.example", "external.example"}),
        )
        self.assertEqual(cap.disposition, "redirected_out_of_allowlist")

    def test_fake_ok_true_external_final_url_no_artifacts(self):
        """Injected fake returns ok=True with an external final URL — the
        post-capture check must reject it; no artifacts, no browser fallback."""
        routes = {
            A: _resp(200, url=A, body=("<html><body>" + "SHORT " * 100 + "</body></html>").encode(),
                     final_url=EXTERNAL),
        }
        session = scripted_http_session(routes)
        from acquisition.transport import RequestsTransport
        transport = RequestsTransport(session=session)
        renderer = FakeRenderer({A: html_result("SHORT " * 100, final_url=EXTERNAL)})
        renderer.goto_log = []
        from acquisition.robots import RobotsCache
        from tests.maintenance.fixtures import robots_for_hosts
        from acquisition.acquire import acquire_url
        cap = acquire_url(
            A, transport=transport, renderer=renderer,
            robots=robots_for_hosts({"gov.example"}),
            out_dir=tempfile.mkdtemp(prefix="p02b_fake_"),
            user_agent="FreshnessChecker", allowed_domains=HOSTS)
        self.assertNotEqual(cap.disposition, "captured")
        self.assertEqual(len(renderer.goto_log), 0, "browser fallback after fake ok=True")

    def test_fake_empty_final_url_compatibility(self):
        routes = {
            A: _resp(200, url=A, body=("<html><body>" + "OK " * 100 + "</body></html>").encode(),
                     final_url=""),
        }
        session = scripted_http_session(routes)
        from acquisition.transport import RequestsTransport
        transport = RequestsTransport(session=session)
        renderer = FakeRenderer({})
        from acquisition.robots import RobotsCache
        from tests.maintenance.fixtures import robots_for_hosts
        from acquisition.acquire import acquire_url
        cap = acquire_url(
            A, transport=transport, renderer=renderer,
            robots=robots_for_hosts({"gov.example"}),
            out_dir=tempfile.mkdtemp(prefix="p02b_compat_"),
            user_agent="FreshnessChecker", allowed_domains=HOSTS)
        self.assertEqual(cap.disposition, "captured")


class TestMonitorPolicyBinding(unittest.TestCase):
    """P02B §3: known monitor binds per-source HTTP adapters."""

    def _make_monitor(self, tmp, routes, renderer_script=None, *, record_ids=None):
        from test_freshness import make_monitor
        from tests.maintenance.fixtures import robots_for_hosts
        from acquisition.transport import RequestsTransport
        session = scripted_http_session(routes)
        from acquisition.transport import RequestsTransport as RT
        transport = RT(session=session)
        renderer = FakeRenderer(renderer_script or {})
        corpus = tmp / "nid.jsonl"
        with open(corpus, "w", encoding="utf-8") as f:
            f.write(json.dumps(rec("r1", PDF)) + "\n")
            f.write(json.dumps(rec("r2", A)) + "\n")
        mon = make_monitor(tmp, transport, renderer, {"nid": corpus},
                           robots=robots_for_hosts({"gov.example"}),
                           record_ids=record_ids)
        return mon, session, renderer, corpus

    def test_pdf_redirect_to_external_terminal_no_evidence(self):
        tmp = Path(tempfile.mkdtemp(prefix="p02b_mon_"))
        old_pdf = make_pdf("OLD-PDF")
        routes = {
            PDF: _resp(302, url=PDF, headers={"Location": EXTERNAL}),
        }
        mon, session, renderer, corpus = self._make_monitor(
            tmp, routes, renderer_script={A: html_result("HTML " * 40)})
        summary = mon.run()
        store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
        entry = store.get(PDF)
        self.assertIsNotNone(entry)
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertIsNone(entry.get("candidate_hash"))
        self.assertEqual(len(renderer.goto_log), 0,
                         "renderer invoked for a PDF source")
        events = json.loads((tmp / "pending_source_events.json").read_text(encoding="utf-8"))
        self.assertEqual([e for e in events if e.get("status") == "pending"], [])

    def test_existing_state_preserved_after_http_denial(self):
        tmp = Path(tempfile.mkdtemp(prefix="p02b_mon_"))
        old_pdf = make_pdf("BASE-PDF")
        # first: establish baseline with a valid PDF
        mon, session, renderer, _ = self._make_monitor(
            tmp, {PDF: _resp(200, url=PDF, body=old_pdf,
                             headers={"content-type": "application/pdf"})},
            renderer_script={A: html_result("HTML " * 40)})
        mon.run()
        store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
        entry_before = store[PDF]
        frozen_hash = entry_before["trusted_hash"]
        frozen_path = entry_before["trusted_snapshot"]
        frozen_bytes = Path(frozen_path).read_bytes()
        frozen_events = (tmp / "pending_source_events.json").read_text(encoding="utf-8")

        # then: redirect to external
        mon2, session2, renderer2, _ = self._make_monitor(
            tmp, {PDF: _resp(302, url=PDF, headers={"Location": EXTERNAL})},
            renderer_script={A: html_result("HTML " * 40)})
        mon2.run()
        store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
        entry = store[PDF]
        self.assertEqual(entry["trusted_hash"], frozen_hash)
        self.assertEqual(Path(entry["trusted_snapshot"]).read_bytes(), frozen_bytes)
        events_now = (tmp / "pending_source_events.json").read_text(encoding="utf-8")
        self.assertEqual(events_now, frozen_events)

    def test_two_sources_different_hosts_one_fails_one_succeeds(self):
        tmp = Path(tempfile.mkdtemp(prefix="p02b_mon2_"))
        html_body = "<html><body>" + "OK " * 100 + "</body></html>"
        routes = {
            A: _resp(200, url=A, body=html_result("HTML " * 40).text.encode()),
            PDF: _resp(302, url=PDF, headers={"Location": EXTERNAL}),
        }
        mon, session, renderer, corpus = self._make_monitor(
            tmp, routes, renderer_script={A: html_result("HTML " * 40)})
        summary = mon.run()
        store = json.loads((tmp / "source_store.json").read_text(encoding="utf-8"))
        # the valid HTML source is still processed
        self.assertIsNotNone(store.get(A, {}).get("trusted_hash"))
        # the PDF source with external redirect is denied
        self.assertIsNone(store.get(PDF, {}).get("trusted_hash"))
        # the original transport is unchanged
        self.assertIsNone(mon.transport.navigation_policy)

    def test_dry_run_preserves_all_persisted_bytes(self):
        tmp = Path(tempfile.mkdtemp(prefix="p02b_dry_"))
        mon, session, renderer, _ = self._make_monitor(
            tmp, {PDF: _resp(200, url=PDF, body=make_pdf("BASE"),
                             headers={"content-type": "application/pdf"})},
            renderer_script={A: html_result("HTML " * 40)})
        mon.run()
        before = state_bytes(tmp)
        dry, _, _, _ = self._make_monitor(
            tmp, {PDF: _resp(302, url=PDF, headers={"Location": EXTERNAL})},
            renderer_script={A: html_result("HTML " * 40)}, dry_run=True)
        dry.run()
        self.assertEqual(before, state_bytes(tmp))


def fixture_corpus(tmp, records, name="nid") -> Path:
    p = tmp / f"{name}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


if __name__ == "__main__":
    unittest.main()
