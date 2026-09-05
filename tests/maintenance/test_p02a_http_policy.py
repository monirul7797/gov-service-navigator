"""
tests/maintenance/test_p02a_http_policy.py — P02A packet tests.

Covers the two fixed contracts:
  A. NavigationPolicy — immutable configuration, pure URL policy (no DNS),
     exact/subdomain host matching, wizard/login exclusion incl. the
     percent-decoded path, invalid input returns False.
  B/C. RequestsTransport policy-enforced bounded manual redirects with a
     scripted fake session: exact request traces, deterministic redirect
     diagnostics, response closure, legacy compatibility.

The session boundary is mocked, not the production redirect loop or the
policy decision. Zero network attempts by construction.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from tests.maintenance.fixtures import scripted_http_session

import requests

A = "https://gov.example/a"
B = "https://gov.example/b"
C = "https://gov.example/c"
D = "https://gov.example/d"
E = "https://gov.example/e"
F = "https://gov.example/f"
EXTERNAL = "https://external.example/x"
EXTERNAL_PDF = "https://external.example/doc.pdf"
LOGIN = "https://gov.example/login"
PDF = "https://gov.example/doc.pdf"


def _resp(status, *, url, headers=None, body=b"page", history=None):
    return {"status": status, "headers": headers or {}, "body": body,
            "final_url": url, "history": history or []}


def make_transport(routes, *, policy=None):
    """Real RequestsTransport + scripted fake session at the session boundary."""
    from acquisition.transport import RequestsTransport
    session = scripted_http_session(routes)
    transport = RequestsTransport(navigation_policy=policy, session=session)
    return transport, session


def policy_hosts(hosts="gov.example", max_redirects=5):
    from acquisition.navigation_policy import NavigationPolicy
    return NavigationPolicy(frozenset(hosts) if isinstance(hosts, (set, frozenset, list))
                            else frozenset({hosts}), max_redirects=max_redirects)


class TestNavigationPolicyConstruction(unittest.TestCase):
    """Contract A: configuration validation."""

    def test_accepts_hostname_config_with_default_limit(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"gov.example"}))
        self.assertEqual(policy.max_redirects, 5)
        self.assertEqual(policy.allowed_hosts, frozenset({"gov.example"}))

    def test_empty_host_set_denies_everything(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset())
        self.assertFalse(policy.allows("https://gov.example/a"))
        self.assertFalse(policy.allows("https://x.gov.example/a"))

    def test_invalid_max_redirects_rejected(self):
        from acquisition.navigation_policy import NavigationPolicy
        for bad in (-1, True, False, 2.5, "5", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    NavigationPolicy(frozenset({"gov.example"}), max_redirects=bad)
        # zero and positive ints are valid
        NavigationPolicy(frozenset({"gov.example"}), max_redirects=0)
        NavigationPolicy(frozenset({"gov.example"}), max_redirects=3)

    def test_invalid_host_configuration_rejected(self):
        from acquisition.navigation_policy import NavigationPolicy
        for bad in ("https://gov.example",          # URL string
                    "gov.example:443",              # port
                    "user:pass@gov.example",        # credentials
                    "gov.example/path",             # path
                    "gov.example?q=1",              # query
                    "*",                            # bare wildcard
                    "gov.*",                        # non-leading wildcard
                    "gov.example:*",                # wildcard port shape
                    "",                             # empty
                    "gov example",                  # whitespace
                    "gov_exa mple",                 # space inside
                    None, 42,                       # non-string
                    "..",                           # dot junk
                    ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    NavigationPolicy(frozenset({bad}))

    def test_valid_configurations_accepted(self):
        from acquisition.navigation_policy import NavigationPolicy
        for hosts in ({"gov.example"}, {"*.gov.example"}, {"GOV.Example"},
                      {"192.0.2.10"}, {"localhost"}, {"[2001:db8::1]"},
                      {"*.gov.example", "gov.example"}):
            with self.subTest(hosts=hosts):
                NavigationPolicy(frozenset(hosts))


class TestNavigationPolicyAllows(unittest.TestCase):
    """Contract A: pure matching rules; invalid input returns False."""

    @classmethod
    def setUpClass(cls):
        from acquisition.navigation_policy import NavigationPolicy
        cls.policy = NavigationPolicy(frozenset({"gov.example", "*.sub.gov.example"}))
        cls.sub_policy = NavigationPolicy(frozenset({"*.gov.example"}))

    def test_exact_host_allowed(self):
        self.assertTrue(self.policy.allows("https://gov.example/a"))

    def test_unconfigured_subdomain_denied(self):
        self.assertFalse(self.policy.allows("https://www.gov.example/a"))

    def test_explicit_subdomain_wildcard(self):
        self.assertTrue(self.sub_policy.allows("https://www.gov.example/a"))
        self.assertTrue(self.sub_policy.allows("https://a.b.gov.example/a"))
        self.assertFalse(self.sub_policy.allows("https://gov.example/a"),
                         "wildcard must not match the apex")

    def test_suffix_impostor_denied(self):
        self.assertFalse(self.policy.allows("https://gov.example.evil/a"))

    def test_mixed_case_host_and_scheme(self):
        self.assertTrue(self.policy.allows("HTTPS://GOV.Example/a"))

    def test_http_and_https_allowed(self):
        self.assertTrue(self.policy.allows("http://gov.example/a"))

    def test_unsupported_scheme_denied(self):
        self.assertFalse(self.policy.allows("ftp://gov.example/a"))
        self.assertFalse(self.policy.allows("file:///etc/passwd"))

    def test_credential_url_denied(self):
        self.assertFalse(self.policy.allows("https://user:pass@gov.example/a"))

    def test_invalid_ports_denied_valid_ports_match_host(self):
        self.assertTrue(self.policy.allows("https://gov.example:443/a"))
        self.assertTrue(self.policy.allows("https://gov.example:8443/a"))
        self.assertFalse(self.policy.allows("https://gov.example:0/a"))
        self.assertFalse(self.policy.allows("https://gov.example:99999/a"))
        self.assertFalse(self.policy.allows("https://gov.example:abc/a"))

    def test_raw_whitespace_backslash_control_chars_denied(self):
        self.assertFalse(self.policy.allows("https://gov.example /a"))
        self.assertFalse(self.policy.allows("https://gov.example\backslash"))
        self.assertFalse(self.policy.allows("https://gov.example/a\n\t"))

    def test_malformed_brackets_denied(self):
        self.assertFalse(self.policy.allows("https://[invalid/x"))
        self.assertFalse(self.policy.allows("https://gov.example/[bad]/a"))

    def test_wizard_login_paths_denied(self):
        for path in ("/login", "/ViewStatus.aspx", "/Reprint.aspx",
                     "/Incomplete.aspx", "/ApplyVisa"):
            with self.subTest(path=path):
                self.assertFalse(self.policy.allows(f"https://gov.example{path}"))

    def test_percent_encoded_login_path_denied(self):
        self.assertFalse(self.policy.allows("https://gov.example/log%69n"))
        self.assertFalse(self.policy.allows("https://gov.example/%6Cogin"))

    def test_informational_pdf_and_boilerplate_eligible(self):
        self.assertTrue(self.policy.allows("https://gov.example/files/doc.pdf"))
        self.assertTrue(self.policy.allows("https://gov.example/TermsCondition.aspx"))

    def test_invalid_input_returns_false_without_raising(self):
        for bad in (None, 123, "", "not a url at all", "gov.example/a",
                    "https://", "https:///a"):
            with self.subTest(bad=bad):
                self.assertFalse(self.policy.allows(bad))

    def test_no_dns_or_network_needed(self):
        # a hostname that would require DNS resolution still policy-matches
        # purely syntactically; nothing here contacts the network (the offline
        # harness proves this globally)
        self.assertTrue(self.policy.allows("https://gov.example/deep/path?q=1#frag"))


class TestPolicyEnforcedRedirects(unittest.TestCase):
    """Contract B/C: bounded manual redirects with exact request traces."""

    def test_forbidden_initial_url_no_calls(self):
        transport, session = make_transport({}, policy=policy_hosts())
        result = transport.fetch(EXTERNAL)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "destination_disallowed")
        self.assertEqual(session.urls, [])
        self.assertEqual(result.redirect_chain, [])

    def test_allowed_redirect_a_to_b(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: _resp(200, url=B),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertTrue(result.ok)
        self.assertEqual(session.urls, [A, B])
        self.assertEqual(result.redirect_chain, [A, B])
        self.assertEqual(result.final_url, B)
        self.assertEqual(result.status, 200)
        self.assertEqual(result.body, b"page")

    def test_relative_location_resolved(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": "/b"}),
            B: _resp(200, url=B),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertTrue(result.ok)
        self.assertEqual(session.urls, [A, B])

    def test_mixed_case_location_header(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"lOcAtIoN": B}),
            B: _resp(200, url=B),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertTrue(result.ok)
        self.assertEqual(session.urls, [A, B])

    def test_all_five_supported_redirect_statuses(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                transport, session = make_transport({
                    A: _resp(status, url=A, headers={"Location": B}),
                    B: _resp(200, url=B),
                }, policy=policy_hosts())
                result = transport.fetch(A)
                self.assertTrue(result.ok, status)
                self.assertEqual(session.urls, [A, B], status)

    def test_external_redirect_denied_not_contacted(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": EXTERNAL}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "destination_disallowed")
        self.assertEqual(session.urls, [A], "external destination was contacted")
        self.assertEqual(result.redirect_chain, [A])
        self.assertEqual(result.final_url, A)

    def test_external_pdf_redirect_denied(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": EXTERNAL_PDF}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "destination_disallowed")
        self.assertEqual(session.urls, [A])

    def test_same_host_login_redirect_denied(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": LOGIN}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "destination_disallowed")
        self.assertEqual(session.urls, [A])

    def test_missing_location(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "redirect_missing_location")
        self.assertEqual(session.urls, [A])

    def test_blank_location(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": "   "}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "redirect_missing_location")

    def test_malformed_location_destination(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": "https://[bad"}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "destination_disallowed")
        self.assertEqual(session.urls, [A])

    def test_self_loop(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": A}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "redirect_loop")
        self.assertEqual(session.urls, [A])

    def test_two_hop_loop(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: _resp(302, url=B, headers={"Location": A}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "redirect_loop")
        self.assertEqual(session.urls, [A, B])

    def test_fragment_only_self_loop(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": A + "#section"}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "redirect_loop")
        self.assertEqual(session.urls, [A])

    def test_default_port_equivalent_loop(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": "https://gov.example:443/a"}),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "redirect_loop")
        self.assertEqual(session.urls, [A])

    def test_five_redirects_then_success_six_calls(self):
        routes = {A: _resp(302, url=A, headers={"Location": B})}
        for src, dst in ((B, C), (C, D), (D, E), (E, F)):
            routes[src] = _resp(302, url=src, headers={"Location": dst})
        routes[F] = _resp(200, url=F)
        transport, session = make_transport(routes, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertTrue(result.ok)
        self.assertEqual(session.urls, [A, B, C, D, E, F])
        self.assertEqual(len(session.urls), 6)

    def test_sixth_redirect_hits_limit_no_seventh_call(self):
        routes = {A: _resp(302, url=A, headers={"Location": B})}
        for src, dst in ((B, C), (C, D), (D, E), (E, F)):
            routes[src] = _resp(302, url=src, headers={"Location": dst})
        # the sixth response redirects to a NEW destination — the hop limit,
        # not a loop, is what must stop the seventh request
        routes[F] = _resp(302, url=F, headers={"Location": "https://gov.example/g"})
        transport, session = make_transport(routes, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "redirect_limit")
        self.assertEqual(session.urls, [A, B, C, D, E, F])
        self.assertEqual(len(session.urls), 6)

    def test_zero_limit_redirect_immediately(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: _resp(200, url=B),
        }, policy=policy_hosts(max_redirects=0))
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "redirect_limit")
        self.assertEqual(session.urls, [A])

    def test_custom_smaller_limit_boundary(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: _resp(302, url=B, headers={"Location": C}),
            C: _resp(200, url=C),
        }, policy=policy_hosts(max_redirects=2))
        result = transport.fetch(A)
        self.assertTrue(result.ok)
        self.assertEqual(session.urls, [A, B, C])
        transport2, session2 = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: _resp(302, url=B, headers={"Location": C}),
            C: _resp(302, url=C, headers={"Location": D}),
        }, policy=policy_hosts(max_redirects=2))
        result2 = transport2.fetch(A)
        self.assertEqual(result2.error_kind, "redirect_limit")
        self.assertEqual(session2.urls, [A, B, C])

    def test_http_404_preserves_classification(self):
        transport, session = make_transport({
            A: _resp(404, url=A),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "http_status")
        self.assertEqual(result.status, 404)
        self.assertEqual(result.final_url, A)

    def test_http_500_after_allowed_redirect(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: _resp(500, url=B),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "http_status")
        self.assertEqual(result.status, 500)
        self.assertEqual(session.urls, [A, B])

    def test_unsupported_3xx_status(self):
        transport, session = make_transport({
            A: _resp(304, url=A),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "http_status")
        self.assertEqual(session.urls, [A])

    def test_tls_exception_classification_initial(self):
        from requests.exceptions import SSLError
        transport, session = make_transport(
            {A: {"exception": SSLError("certificate verify failed")}},
            policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "tls")
        self.assertEqual(session.urls, [A])

    def test_tls_exception_after_allowed_redirect(self):
        from requests.exceptions import SSLError
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: {"exception": SSLError("handshake failed")},
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "tls")
        self.assertEqual(session.urls, [A, B])

    def test_connection_exception_classification(self):
        from requests.exceptions import ConnectionError as RequestsConnectionError
        transport, session = make_transport(
            {A: {"exception": RequestsConnectionError("refused")}},
            policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "connection")


class TestResponseClosureAndCallKwargs(unittest.TestCase):
    """Contract C: every received response closes exactly once; policy-mode
    calls disable automatic redirects and enable certificate verification."""

    def test_closures_on_success(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: _resp(200, url=B),
        }, policy=policy_hosts())
        transport.fetch(A)
        self.assertEqual(session.close_counts, {A: 1, B: 1})

    def test_closures_on_rejections_and_errors(self):
        cases = {
            "disallowed_redirect": {A: _resp(302, url=A, headers={"Location": EXTERNAL})},
            "missing_location": {A: _resp(302, url=A, headers={})},
            "loop": {A: _resp(302, url=A, headers={"Location": A})},
            "http_404": {A: _resp(404, url=A)},
        }
        for label, routes in cases.items():
            with self.subTest(label=label):
                transport, session = make_transport(routes, policy=policy_hosts())
                transport.fetch(A)
                self.assertEqual(session.close_counts.get(A), 1, label)

    def test_policy_calls_disable_auto_redirects_and_verify_tls(self):
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: _resp(200, url=B),
        }, policy=policy_hosts())
        transport.fetch(A)
        for call_kwargs in session.call_kwargs:
            self.assertFalse(call_kwargs.get("allow_redirects"))
            self.assertTrue(call_kwargs.get("verify"))


class TestLegacyCompatibility(unittest.TestCase):
    """Contract B: policy-less legacy behavior is retained and visibly
    separate; the new chain field does not break simple fakes."""

    def test_legacy_automatic_redirects_unchanged(self):
        # legacy mode issues ONE automatic-redirect call; the scripted fake
        # models requests having followed it (response history + final_url)
        session = scripted_http_session({
            A: _resp(200, url=B, history=[A]),
        })
        from acquisition.transport import RequestsTransport
        transport = RequestsTransport(session=session)   # no policy
        result = transport.fetch(A)
        self.assertTrue(result.ok)
        self.assertEqual(result.final_url, B)
        self.assertEqual(session.urls, [A])
        self.assertTrue(session.call_kwargs[0].get("allow_redirects"))
        # the chain is populated from the response history
        self.assertEqual(result.redirect_chain, [A, B])

    def test_legacy_error_classification_unchanged(self):
        session = scripted_http_session({A: _resp(404, url=A)})
        from acquisition.transport import RequestsTransport
        transport = RequestsTransport(session=session)
        result = transport.fetch(A)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "http_status")
        self.assertEqual(result.status, 404)

    def test_redirect_chain_instances_independent(self):
        from acquisition.transport import StrictResult
        r1, r2 = StrictResult(ok=True), StrictResult(ok=True)
        r1.redirect_chain.append(A)
        self.assertEqual(r2.redirect_chain, [])
        self.assertIsNot(r1.redirect_chain, r2.redirect_chain)


if __name__ == "__main__":
    unittest.main()
