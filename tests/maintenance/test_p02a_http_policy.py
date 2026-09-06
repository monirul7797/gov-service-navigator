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


# ---------------------------------------------------------------------------
# P02A-C1 regressions (fail-closed classification, host canonicalization,
# immutable configuration, response cleanup and exception boundaries)
# ---------------------------------------------------------------------------

import hashlib  # noqa: E402


class TestC1InvalidClassificationFailsClosed(unittest.TestCase):
    """C1: `invalid` classification must not bypass wizard/login exclusion.
    Only ok/pdf/boilerplate dispositions may pass the classification stage,
    on both the original and the percent-decoded representation."""

    FORBIDDEN = [
        "https://gov.example/login?x=1,2",       # comma -> invalid original
        "https://gov.example/log%69n?x=1,2",     # comma + encoded login
        "https://gov.example/x%20y/log%69n",     # decoded space invalidates
        "https://gov.example/page?x=1;2",        # semicolon -> invalid
    ]

    def test_allows_rejects_all_forbidden_variants(self):
        policy = policy_hosts()
        for url in self.FORBIDDEN:
            with self.subTest(url=url):
                self.assertFalse(policy.allows(url),
                                 f"invalid/encoded classification bypassed denial: {url}")

    def test_normal_query_positive_control(self):
        policy = policy_hosts()
        self.assertTrue(policy.allows("https://gov.example/page?q=1"))
        self.assertTrue(policy.allows("https://gov.example/page?x=1&y=2"))

    def test_forbidden_initial_urls_never_contacted(self):
        for url in self.FORBIDDEN:
            with self.subTest(url=url):
                transport, session = make_transport({}, policy=policy_hosts())
                result = transport.fetch(url)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_kind, "destination_disallowed")
                self.assertEqual(session.urls, [], f"{url} was contacted")
                self.assertEqual(result.redirect_chain, [])

    def test_forbidden_redirect_destinations_never_contacted(self):
        for url in self.FORBIDDEN:
            with self.subTest(url=url):
                transport, session = make_transport(
                    {A: _resp(302, url=A, headers={"Location": url})},
                    policy=policy_hosts())
                result = transport.fetch(A)
                self.assertFalse(result.ok)
                self.assertEqual(result.error_kind, "destination_disallowed")
                self.assertEqual(session.urls, [A],
                                 f"redirect target {url} was contacted")
                self.assertEqual(result.redirect_chain, [A])

    def test_allowed_query_positive_control_through_transport(self):
        target = "https://gov.example/page?q=1"
        transport, session = make_transport({
            A: _resp(302, url=A, headers={"Location": target}),
            target: _resp(200, url=target),
        }, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertTrue(result.ok)
        self.assertEqual(session.urls, [A, target])
        self.assertEqual(result.redirect_chain, [A, target])


class TestC2HostCanonicalization(unittest.TestCase):
    """C2: ipaddress-based IP literals, consistent canonicalization, label
    validation on URL hosts, scoped IPv6 rejected, wildcards are DNS-only."""

    def test_invalid_ipv6_shorthand_rejected(self):
        from acquisition.navigation_policy import NavigationPolicy
        with self.assertRaises(ValueError):
            NavigationPolicy(frozenset({"[:::]"}))

    def test_valid_ipv6_config_and_exact_match(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"[2001:db8::1]"}))
        self.assertTrue(policy.allows("https://[2001:db8::1]/"))

    def test_equivalent_expanded_ipv6_matches(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"[2001:db8::1]"}))
        self.assertTrue(policy.allows(
            "https://[2001:0db8:0000:0000:0000:0000:0000:0001]/"))

    def test_bracket_free_url_hostname_matches_ipv6(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"2001:db8::1"}))
        self.assertTrue(policy.allows("https://[2001:db8::1]/"))

    def test_junk_after_closing_bracket_rejected(self):
        from acquisition.navigation_policy import NavigationPolicy
        with self.assertRaises(ValueError):
            NavigationPolicy(frozenset({"[::1]junk"}))

    def test_unmatched_bracket_rejected(self):
        from acquisition.navigation_policy import NavigationPolicy
        with self.assertRaises(ValueError):
            NavigationPolicy(frozenset({"[::1"}))

    def test_scoped_ipv6_config_rejected(self):
        from acquisition.navigation_policy import NavigationPolicy
        with self.assertRaises(ValueError):
            NavigationPolicy(frozenset({"fe80::1%eth0"}))

    def test_scoped_ipv6_url_denied(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"gov.example"}))
        self.assertFalse(policy.allows("https://[fe80::1%25eth0]/a"))

    def test_wildcard_matches_normal_subdomain(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"*.gov.example"}))
        self.assertTrue(policy.allows("https://www.gov.example/a"))

    def test_wildcard_rejects_malformed_empty_label_host(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"*.gov.example"}))
        self.assertFalse(policy.allows("https://bad..gov.example/a"))

    def test_wildcard_over_ip_literal_rejected(self):
        from acquisition.navigation_policy import NavigationPolicy
        with self.assertRaises(ValueError):
            NavigationPolicy(frozenset({"*.192.0.2.10"}))

    def test_ip_literal_exact_match(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"192.0.2.10"}))
        self.assertTrue(policy.allows("https://192.0.2.10/a"))
        wildcard_policy = NavigationPolicy(frozenset({"*.2.10"}))
        self.assertFalse(wildcard_policy.allows("https://192.0.2.10/a"))

    def test_config_whitespace_rejected_not_stripped(self):
        from acquisition.navigation_policy import NavigationPolicy
        with self.assertRaises(ValueError):
            NavigationPolicy(frozenset({" gov.example"}))

    def test_explicitly_empty_port_denied(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"gov.example"}))
        self.assertFalse(policy.allows("https://gov.example:/a"))

    def test_trailing_dot_normalized_on_both_sides(self):
        from acquisition.navigation_policy import NavigationPolicy
        policy = NavigationPolicy(frozenset({"gov.example."}))
        self.assertTrue(policy.allows("https://gov.example./a"))
        policy2 = NavigationPolicy(frozenset({"gov.example"}))
        self.assertTrue(policy2.allows("https://gov.example./a"))

    def test_ipv6_url_reaches_scripted_session_and_succeeds(self):
        routes = {"https://[2001:db8::1]/a": _resp(200, url="https://[2001:db8::1]/a")}
        transport, session = make_transport(
            routes, policy=policy_hosts(frozenset({"2001:db8::1"})))
        result = transport.fetch("https://[2001:db8::1]/a")
        self.assertTrue(result.ok)
        self.assertEqual(session.urls, ["https://[2001:db8::1]/a"])


class TestC3PolicyImmutability(unittest.TestCase):
    """C3: the policy configuration is immutable under ordinary assignment
    and input-set aliasing cannot change decisions."""

    def _policy(self):
        from acquisition.navigation_policy import NavigationPolicy
        source = {"gov.example"}
        return NavigationPolicy(frozenset(source)), source

    def test_max_redirects_assignment_rejected(self):
        policy, _ = self._policy()
        with self.assertRaises(AttributeError):
            policy.max_redirects = 100

    def test_hosts_assignment_rejected(self):
        policy, _ = self._policy()
        with self.assertRaises(AttributeError):
            policy.allowed_hosts = frozenset({"external.example"})
        with self.assertRaises(AttributeError):
            policy._hosts = frozenset({"external.example"})

    def test_max_redirects_deletion_rejected(self):
        policy, _ = self._policy()
        with self.assertRaises(AttributeError):
            del policy.max_redirects

    def test_input_set_aliasing_cannot_change_policy(self):
        policy, source = self._policy()
        source.add("external.example")
        source.discard("gov.example")
        self.assertTrue(policy.allows("https://gov.example/a"))
        self.assertFalse(policy.allows("https://external.example/"))

    def test_bound_transport_still_denies_after_attempted_mutation(self):
        policy, _ = self._policy()
        transport, session = make_transport({}, policy=policy)
        with self.assertRaises(AttributeError):
            policy._hosts = frozenset({"external.example"})
        with self.assertRaises(AttributeError):
            policy.max_redirects = 100
        result = transport.fetch(EXTERNAL)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "destination_disallowed")
        self.assertEqual(session.urls, [])


class TestC4ResponseCleanupAndExceptionBoundaries(unittest.TestCase):
    """C4: one finally-owned cleanup per received response; only Requests
    exceptions become transport outcomes; AssertionError/RuntimeError
    propagate after cleanup; read failures retain received provenance with
    no partial body."""

    class RaisingContentResponse:
        def __init__(self, status, url, exc):
            self.status_code = status
            self.url = url
            self.headers = {"content-type": "text/html"}
            self._exc = exc
            self.close_count = 0

        @property
        def content(self):
            raise self._exc

        def close(self):
            self.close_count += 1

    def _verbatim_transport(self, objects):
        """Transport whose session returns the given response objects
        verbatim (each in order), recording requested URLs."""
        from acquisition.transport import RequestsTransport
        from tests.maintenance.fixtures import robots_for_hosts
        from unittest.mock import Mock
        session = Mock()
        session.headers = {}
        session.urls = []
        session.call_kwargs = []
        objects = list(objects)

        def _get(url, **kwargs):
            session.urls.append(url)
            session.call_kwargs.append(kwargs)
            obj = objects.pop(0)
            return obj

        session.get.side_effect = _get
        transport = RequestsTransport(navigation_policy=policy_hosts(), session=session)
        return transport, session

    def test_chunked_encoding_at_initial_url(self):
        from requests.exceptions import ChunkedEncodingError
        resp = self.RaisingContentResponse(200, A, ChunkedEncodingError("boom"))
        transport, session = self._verbatim_transport([resp])
        result = transport.fetch(A)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "connection")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.final_url, A)
        self.assertEqual(result.redirect_chain, [A])
        self.assertFalse(result.body)
        self.assertEqual(resp.close_count, 1)

    def test_chunked_encoding_after_allowed_redirect(self):
        from requests.exceptions import ChunkedEncodingError
        redirect_resp = SimpleNamespace(status_code=302, url=A,
                                        headers={"Location": B}, history=[])
        redirect_resp.close_count = 0

        def close_redirect():
            redirect_resp.close_count += 1

        redirect_resp.close = close_redirect
        broken_resp = self.RaisingContentResponse(200, B, ChunkedEncodingError("boom"))
        transport, session = self._verbatim_transport([redirect_resp, broken_resp])
        result = transport.fetch(A)
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "connection")
        self.assertEqual(result.status, 200)
        self.assertEqual(result.final_url, B)
        self.assertEqual(result.redirect_chain, [A, B])
        self.assertFalse(result.body)
        self.assertEqual(redirect_resp.close_count, 1)
        self.assertEqual(broken_resp.close_count, 1)

    def test_runtime_error_propagates_after_cleanup(self):
        resp = self.RaisingContentResponse(200, A, RuntimeError("programmer error"))
        transport, session = self._verbatim_transport([resp])
        with self.assertRaises(RuntimeError):
            transport.fetch(A)
        self.assertEqual(resp.close_count, 1)

    def test_unscripted_request_assertion_error_propagates(self):
        session = scripted_http_session({})
        from acquisition.transport import RequestsTransport
        transport = RequestsTransport(navigation_policy=policy_hosts(), session=session)
        with self.assertRaises(AssertionError):
            transport.fetch(A)

    def test_genuine_transport_failures_retain_classification(self):
        from requests.exceptions import ConnectionError as RC
        from requests.exceptions import SSLError
        transport, session = make_transport(
            {A: {"exception": RC("refused")}}, policy=policy_hosts())
        result = transport.fetch(A)
        self.assertEqual(result.error_kind, "connection")
        self.assertEqual(result.final_url, "")
        transport2, session2 = make_transport({
            A: _resp(302, url=A, headers={"Location": B}),
            B: {"exception": SSLError("handshake")},
        }, policy=policy_hosts())
        result2 = transport2.fetch(A)
        self.assertEqual(result2.error_kind, "tls")
        self.assertEqual(session2.urls, [A, B])

    def test_existing_paths_still_close_once(self):
        cases = {
            "success": {A: _resp(200, url=A)},
            "http_reject": {A: _resp(404, url=A)},
            "redirect_denied": {A: _resp(302, url=A, headers={"Location": EXTERNAL})},
            "loop": {A: _resp(302, url=A, headers={"Location": A})},
            "missing_location": {A: _resp(302, url=A, headers={})},
            "hop_limit": {A: _resp(302, url=A, headers={"Location": B}),
                          B: _resp(302, url=B, headers={"Location": C}),
                          C: _resp(302, url=C, headers={"Location": D}),
                          D: _resp(302, url=D, headers={"Location": E}),
                          E: _resp(302, url=E, headers={"Location": F}),
                          F: _resp(302, url=F, headers={"Location": "https://gov.example/g"})},
        }
        for label, routes in cases.items():
            with self.subTest(label=label):
                transport, session = make_transport(routes, policy=policy_hosts())
                transport.fetch(A)
                for url in session.urls:
                    self.assertEqual(session.close_counts.get(url), 1,
                                     f"{label}: {url} close count")


if __name__ == "__main__":
    unittest.main()
