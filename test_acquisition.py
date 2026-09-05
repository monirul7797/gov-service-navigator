"""
test_acquisition.py — hermetic regression suite for the acquisition layer
(Phase 3.5 §14). No network: every test uses fake transports/renderers or
pure policy functions, following the test_multiturn.py convention.

Run:  python test_acquisition.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from acquisition import urls as U
from acquisition import safeclick as SC
from acquisition.acquire import (CAPTURE_ONLY_TLS, DIRECT_PDF_VERIFIED,
                                 DISPOSITION_FAILED, DISPOSITION_NEEDS_MANUAL,
                                 VERIFIED_BROWSER, VERIFIED_NORMAL,
                                 acquire_url)
from acquisition.crawler import CrawlConfig, Crawler
from acquisition.robots import RobotsCache
from acquisition.transport import RenderResult, StrictResult

CHECKS = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = ""):
    if ok:
        CHECKS["pass"] += 1
        print(f"  \u2713 PASS  {name}")
    else:
        CHECKS["fail"] += 1
        print(f"  \u2717 FAIL  {name}  {detail}")


# ---------------------------------------------------------------------------
# Fake transports / renderers (hermetic)
# ---------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, script: dict):
        self.script = script          # url -> StrictResult
        self.fetched: list = []

    def fetch(self, url: str) -> StrictResult:
        self.fetched.append(url)
        return self.script.get(url, StrictResult(ok=False, error_kind="connection",
                                                 error_detail="unscripted"))


class FakeRenderer:
    """Minimal PagePort: pages = {url: RenderResult}, links = {url: [hrefs]},
    interactables = {url: [(tag, text, attrs, inside_form)]}.
    Models one active dynamic state at a time: activate() reveals
    state_delta until the next goto() resets to the base page."""

    def __init__(self, pages: dict, links: dict | None = None,
                 interactables: dict | None = None, state_delta: str = ""):
        self.pages = pages
        self.links = links or {}
        self.interactables = interactables or {}
        self.state_delta = state_delta
        self.rendered: list = []
        self.activated: list = []
        self.current = ""
        self._active = False

    def goto(self, url: str, *, ignore_tls: bool = False) -> RenderResult:
        self.rendered.append((url, ignore_tls))
        self.current = url
        self._active = False            # navigation resets dynamic state
        key = (url, ignore_tls)
        r = self.pages.get(key) or self.pages.get(url)
        if r is None:
            return RenderResult(ok=False, error_kind="blocked",
                                error_detail="net::ERR_BLOCKED_FAKE")
        return r

    def discover_links(self, url: str) -> list:
        return self.links.get(url, [])

    def list_interactables(self):
        from acquisition.transport import Interactable
        return [Interactable(idx=i, tag=t, text=x, attributes=a, inside_form=f)
                for i, (t, x, a, f) in enumerate(self.interactables.get(self.current, []))]

    def activate(self, item) -> bool:
        self.activated.append(item.text)
        self._active = True
        return True

    def ensure_on(self, url: str):
        if self.current != url:
            self.goto(url)

    def settle(self, ms=0):
        pass

    def body_text(self) -> str:
        base = self.pages[self.current].text
        extra = self.state_delta if self._active else ""
        return base + extra

    def snapshot_state(self) -> dict:
        text = self.body_text()
        return {"text": text, "html": f"<html>{text}</html>",
                "png": b"PNG" if self._active else b""}


def robots_for_hosts(hosts) -> RobotsCache:
    """P00 §4: pre-seeded allow parsers for the fixture's ACTUAL hosts —
    the historical '*' sentinel was never consulted by real lookups, which
    made tests using it attempt real robots.txt fetches."""
    import urllib.robotparser
    cache = RobotsCache()
    for host in hosts:
        p = urllib.robotparser.RobotFileParser()
        p.allow_all = True
        cache.parsers[host] = p
    return cache


def robots_for(domains: set) -> RobotsCache:
    import urllib.robotparser
    cache = RobotsCache()
    for d in domains:
        p = urllib.robotparser.RobotFileParser()
        p.allow_all = True
        cache.parsers[d] = p
    return cache


# ---------------------------------------------------------------------------
# 1) URL normalization / dedup
# ---------------------------------------------------------------------------

def test_urls():
    print("URL normalization / dedup:")
    check("lowercases host, drops default port",
          U.normalize_url("HTTPS://VISA.GOV.BD:443/A.aspx") == "https://visa.gov.bd/A.aspx")
    check("drops fragment",
          U.normalize_url("https://visa.gov.bd/Home.aspx#x") == "https://visa.gov.bd/Home.aspx")
    check("empty path becomes /",
          U.normalize_url("https://visa.gov.bd") == "https://visa.gov.bd/")
    check("keeps non-default port",
          U.normalize_url("https://visa.gov.bd:8443/") == "https://visa.gov.bd:8443/")
    check("rejects non-http scheme", U.normalize_url("gazette:S.R.O.79") == "")
    check("rejects comma-joined value",
          U.normalize_url("https://etaxnbr.gov.bd/, https://nbr.gov.bd/") == "")
    check("dedup key: www-fragments-and-case",
          U.normalize_url("https://visa.gov.bd/FAQ.aspx#top")
          == U.normalize_url("https://visa.gov.bd/FAQ.aspx"))
    check("query string preserved verbatim",
          U.normalize_url("https://x.gov.bd/p?id=1&b=2") == "https://x.gov.bd/p?id=1&b=2")


# ---------------------------------------------------------------------------
# 2) URL-level exclusion policy (wizard / boilerplate / pdf / allowlist)
# ---------------------------------------------------------------------------

def test_url_policy():
    print("URL-level exclusion policy:")
    cases = {
        "https://visa.gov.bd/ViewStatus.aspx": "wizard",
        "https://visa.gov.bd/Reprint.aspx": "wizard",
        "https://visa.gov.bd/Incomplete.aspx": "wizard",
        "https://visa.gov.bd/Login.aspx": "wizard",
        "https://x.gov.bd/payment/checkout": "wizard",
        "https://visa.gov.bd/TermsCondition.aspx": "boilerplate",
        "https://visa.gov.bd/PrivacyPolicy.aspx": "boilerplate",
        "https://visa.gov.bd/Img/MRV_FORM.pdf": "pdf",
        "https://visa.gov.bd/FAQ.aspx": "ok",
    }
    for url, want in cases.items():
        got, _ = U.classify_url(url)
        check(f"{url.split('//')[1]} -> {want}", got == want, f"got {got}")
    check("allowlist blocks foreign domain",
          not U.allowed_domain("https://other.gov.bd/", {"visa.gov.bd"}))
    check("allowlist admits subdomain",
          U.allowed_domain("https://www.visa.gov.bd/", {"visa.gov.bd"}))


# ---------------------------------------------------------------------------
# 3) Safe-click policy
# ---------------------------------------------------------------------------

def test_safeclick():
    print("Safe-click policy (§7):")
    unsafe_cases = [
        ("button", "Submit", "type=submit"),
        ("button", "Pay now", ""),
        ("a", "Login", "id=loginBtn"),
        ("button", "Save & Exit", ""),
        ("a", "Next", "class=wizard-next"),
        ("input", "", "type=file"),
        ("button", "Confirm", ""),
        ("button", "Upload document", ""),
        ("a", "View Status", "href=ViewStatus.aspx"),
        ("button", "Continue application", ""),
    ]
    for tag, text, attrs in unsafe_cases:
        verdict, why = SC.classify_element(tag, text, attrs)
        check(f"rejects {text or attrs!r}", verdict == SC.UNSAFE, why)
    check("rejects form button", SC.classify_element("button", "FAQ", "", inside_form=True)[0] == SC.UNSAFE)
    check("anchor inside ASP.NET whole-page form still judged on merits",
          SC.classify_element("a", "Here", 'href="#" class="clsFees"', inside_form=True)[0] == SC.SAFE)
    check("rejects unlabeled button", SC.classify_element("button", "", "")[0] == SC.AMBIGUOUS)
    safe_cases = [
        ("a", "Here", 'href="#" class="clsFees"'),
        ("a", "supported countries", 'href="#"'),
        ("button", "Show fee table", "class=ui-button"),
    ]
    for tag, text, attrs in safe_cases:
        verdict, why = SC.classify_element(tag, text, attrs)
        check(f"allows {text!r}", verdict == SC.SAFE, why)


# ---------------------------------------------------------------------------
# 4) Crawler: depth, allowlist, wizard exclusion, dynamic-state dedup
# ---------------------------------------------------------------------------

def html_render(text: str, status: int = 200) -> RenderResult:
    return RenderResult(ok=status < 400, status=status, text=text,
                        html=f"<html>{text}</html>", screenshot_png=b"PNG")


def test_crawler():
    print("Bounded crawler (depth / allowlist / wizard / dynamic states):")
    A = "https://visa.gov.bd/"                    # seed, depth 0
    B = "https://visa.gov.bd/FAQ.aspx"            # depth 1
    C = "https://visa.gov.bd/notices/list"        # depth 2
    D = "https://visa.gov.bd/deep/page"           # depth 3 — must NOT be fetched
    EXT = "https://other.gov.bd/page"
    WIZ = "https://visa.gov.bd/ViewStatus.aspx"
    PAGES = {A: html_render("HOME PAGE CONTENT " * 30),
             B: html_render("FAQ CONTENT " * 30),
             C: html_render("NOTICES CONTENT " * 30),
             D: html_render("DEEP " * 30),
             EXT: html_render("EXTERNAL " * 30)}
    LINKS = {A: [B, WIZ, EXT], B: [C], C: [D]}
    # one safe expander + one unsafe button on the seed
    INTERACT = {A: [("a", "fee table", 'href="#"', False),
                    ("button", "Submit", "type=submit", False)]}

    transport = FakeTransport({})   # everything renders in the browser layer
    renderer = FakeRenderer(PAGES, LINKS, INTERACT, state_delta="FEE TABLE ROWS " * 20)
    cfg = CrawlConfig(seeds=[A], allowlist={"visa.gov.bd"}, max_depth=2,
                      max_pages=40, interval_seconds=0)
    from unittest.mock import patch
    crawler_robots = robots_for_hosts({"visa.gov.bd"})
    with patch("acquisition.crawler.RobotsCache", return_value=crawler_robots):
        crawler = Crawler(cfg, transport=transport, renderer=renderer,
                          out_dir=tempfile.mkdtemp())
    manifest = crawler.run()

    fetched = [u for u, _ in renderer.rendered]
    check("depth 0 fetched", A in fetched)
    check("depth 1 fetched", B in fetched)
    check("depth 2 fetched", C in fetched)
    check("depth 3 NOT fetched (depth enforcement)", D not in fetched)
    check("out-of-allowlist NOT rendered", EXT not in fetched)
    dispositions = {n["url"]: n["disposition"] for n in manifest["nodes"] if "url" in n}
    check("wizard URL recorded excluded_wizard", dispositions.get(WIZ) == "excluded_wizard")
    check("external URL recorded excluded_out_of_allowlist",
          dispositions.get(EXT) == "excluded_out_of_allowlist")
    check("unsafe interaction rejected by safe-click policy",
          any(n["disposition"] == "interaction_rejected" for n in manifest["nodes"]))
    check("safe dynamic state captured",
          any(n["disposition"] == "js_dynamic_state" for n in manifest["nodes"]))
    check("dynamic state provenance has trigger+hash+parent",
          all(k in s for n in manifest["nodes"] if n["disposition"] == "js_dynamic_state"
              for s in [n["state"]]
              for k in ("trigger_text", "content_sha256", "parent_page", "source_url",
                        "state_name", "captured_at")))
    check("crawl graph records parent links",
          all(any(n["url"] == parent for n in manifest["nodes"])
              for n in manifest["nodes"] if n.get("parent")
              for parent in [n["parent"]]))
    # duplicate dynamic-state dedup: run the same interaction again on page A
    check("pages_captured == 3 (A, B, C)", manifest["summary"]["pages_captured"] == 3)


def test_dynamic_state_dedup():
    print("Dynamic-state dedup (identical content):")
    A = "https://visa.gov.bd/"
    PAGES = {A: html_render("HOME " * 30)}
    LINKS = {A: []}
    INTERACT = {A: [("a", "fee table", 'href="#"', False),
                    ("a", "fee table 2", 'href="#"', False)]}
    # both interactions reveal IDENTICAL state content (no state_delta growth
    # between resets) — second must be recorded as duplicate
    renderer = FakeRenderer(PAGES, LINKS, INTERACT, state_delta="SAME ROWS " * 20)
    from unittest.mock import patch
    crawler_robots = robots_for_hosts({"visa.gov.bd"})
    with patch("acquisition.crawler.RobotsCache", return_value=crawler_robots):
        crawler = Crawler(CrawlConfig(seeds=[A], allowlist={"visa.gov.bd"}, max_depth=2,
                                      max_pages=40, interval_seconds=0),
                          transport=FakeTransport({}), renderer=renderer,
                          out_dir=tempfile.mkdtemp())
    manifest = crawler.run()
    states = [n for n in manifest["nodes"] if n["disposition"] == "js_dynamic_state"]
    dupes = [n for n in manifest["nodes"] if n["disposition"] == "duplicate"]
    check("two identical states -> 1 captured + 1 duplicate",
          len(states) == 1 and len(dupes) == 1,
          f"states={len(states)} dupes={len(dupes)}")


# ---------------------------------------------------------------------------
# 5) Layered acquire: TLS classification, capture-only invariant, fallbacks
# ---------------------------------------------------------------------------

def test_layered_capture():
    print("Layered capture (TLS policy + fallbacks):")
    URL = "https://dip.gov.bd/"
    out = tempfile.mkdtemp()

    # -- strict TLS error -> browser strict TLS error -> evidence render OK --
    transport = FakeTransport({URL: StrictResult(ok=False, error_kind="tls",
                                                 error_detail="SSLError: certificate verify failed")})
    renderer = FakeRenderer({})  # no scripted pages: both gotos unscripted -> blocked
    # (never reached: strict TLS error short-circuits to the evidence path below)
    renderer.pages[(URL, False)] = RenderResult(ok=False, error_kind="tls",
                                                error_detail="net::ERR_CERT_AUTHORITY_INVALID")
    renderer.pages[(URL, True)] = html_render("DIP CONTENT RENDERED " * 20)
    cap = acquire_url(URL, transport=transport, renderer=renderer,
                      robots=robots_for({"dip.gov.bd"}), out_dir=out,
                      user_agent="FreshnessChecker")
    check("method is capture_only_tls_invalid", cap.method == CAPTURE_ONLY_TLS)
    check("verification_status == capture_only", cap.verification_status == "capture_only")
    check("tls_verified is False", cap.tls_verified is False)
    check("requires_secondary_verification is True",
          cap.requires_secondary_verification is True)
    check("original TLS error preserved",
          "CERT" in (cap.tls_error or "") or "certificate" in (cap.tls_error or ""))
    prov = cap.to_provenance()
    check("provenance: capture-only never claims verified",
          not (prov["verification_status"] == "verified"
               or prov["tls_verified"] is True))
    check("provenance has acquisition_method+content_sha256",
          prov["acquisition_method"] == CAPTURE_ONLY_TLS and len(prov["content_sha256"]) == 64)
    check("evidence artifacts written (txt/html/png)",
          all(os.path.exists(p) for p in (cap.text_path, cap.html_path, cap.png_path)))

    # -- browser itself blocked -> needs_manual_screenshot (last fallback) --
    transport2 = FakeTransport({URL: StrictResult(ok=False, error_kind="connection",
                                                  error_detail="refused")})
    renderer2 = FakeRenderer({})   # every goto unscripted -> blocked
    cap2 = acquire_url(URL, transport=transport2, renderer=renderer2,
                       robots=robots_for({"dip.gov.bd"}), out_dir=out,
                       user_agent="FreshnessChecker")
    check("browser blocked -> needs_manual_screenshot",
          cap2.disposition == DISPOSITION_NEEDS_MANUAL, cap2.disposition)

    # -- plain 404 -> failed_fetch (dead link, not a block) --
    transport3 = FakeTransport({URL: StrictResult(ok=False, status=404,
                                                  error_kind="http_status",
                                                  error_detail="http_404")})
    renderer3 = FakeRenderer({URL: RenderResult(ok=False, status=404, error_kind="http_status", error_detail="http_404")})
    cap3 = acquire_url(URL, transport=transport3, renderer=renderer3,
                       robots=robots_for({"dip.gov.bd"}), out_dir=out,
                       user_agent="FreshnessChecker")
    check("404 -> failed_fetch (not a block)", cap3.disposition == DISPOSITION_FAILED)

    # -- happy paths: verified_normal (static HTML) and direct PDF --
    HTML_PAGE = "https://visa.gov.bd/FAQ.aspx"
    PDF = "https://visa.gov.bd/Img/MRV_FORM.pdf"
    transport4 = FakeTransport({
        HTML_PAGE: StrictResult(ok=True, status=200, content_type="text/html",
                                body=b"<html><body>" + b"FAQ ANSWER TEXT " * 40 + b"</body></html>"),
        PDF: StrictResult(ok=True, status=200, content_type="application/pdf", body=b"%PDF-1.4 fake"),
    })
    renderer4 = FakeRenderer({})   # browser never needed
    cap4 = acquire_url(HTML_PAGE, transport=transport4, renderer=renderer4,
                       robots=robots_for({"visa.gov.bd"}), out_dir=out,
                       user_agent="FreshnessChecker")
    check("static HTML -> verified_normal", cap4.method == VERIFIED_NORMAL
          and cap4.verification_status == "verified")
    cap5 = acquire_url(PDF, transport=transport4, renderer=renderer4,
                       robots=robots_for({"visa.gov.bd"}), out_dir=out,
                       user_agent="FreshnessChecker")
    check("PDF -> direct_pdf_verified and preserved",
          cap5.method == DIRECT_PDF_VERIFIED and os.path.exists(cap5.pdf_path)
          and cap5.verification_status == "verified")

    # -- JS-only strict content -> browser fallback verified_browser --
    JS_PAGE = "https://x.gov.bd/app"
    transport5 = FakeTransport({JS_PAGE: StrictResult(ok=True, status=200,
                                                      content_type="text/html",
                                                      body=b"<html><body><div id=root></div></body></html>")})
    renderer5 = FakeRenderer({JS_PAGE: html_render("RENDERED SPA TEXT " * 20)})
    cap6 = acquire_url(JS_PAGE, transport=transport5, renderer=renderer5,
                       robots=robots_for({"x.gov.bd"}), out_dir=out,
                       user_agent="FreshnessChecker")
    check("JS-only page -> verified_browser fallback",
          cap6.method == VERIFIED_BROWSER and cap6.rendered
          and cap6.verification_status == "verified")


# ---------------------------------------------------------------------------
# 6) robots: explicit disallow honored (no evasion)
# ---------------------------------------------------------------------------

def test_robots_disallow():
    print("robots.txt explicit disallow honored:")
    import urllib.robotparser
    cache = RobotsCache()
    p = urllib.robotparser.RobotFileParser()
    p.parse(["User-agent: *", "Disallow: /private/"])
    cache.parsers["dip.gov.bd"] = p
    from acquisition.robots import robots_check
    allowed, _ = robots_check(cache, "https://dip.gov.bd/private/x", "FreshnessChecker")
    check("Disallow directive blocks the fetch path", allowed is False)
    allowed2, note = robots_check(cache, "https://dip.gov.bd/public/page", "FreshnessChecker")
    check("non-disallowed path allowed", allowed2 is True)


if __name__ == "__main__":
    test_urls()
    test_url_policy()
    test_safeclick()
    test_crawler()
    test_dynamic_state_dedup()
    test_layered_capture()
    test_robots_disallow()
    print("=" * 78)
    if CHECKS["fail"]:
        print(f"Result: {CHECKS['fail']} CHECK(S) FAILED ({CHECKS['pass']} passed)")
        sys.exit(1)
    print(f"Result: ALL CHECKS PASS ({CHECKS['pass']} checks)")
