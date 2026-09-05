"""
acquisition/transport.py — network/render ports for the acquisition layer.

Two transports, injected into acquire/crawler so tests can run hermetically:

  RequestsTransport — "strict normal fetch" (Phase 3.5 §1/§2): Python
    requests with TLS verification ON (verify=True). Never bypasses
    certificates. Classifies failures: tls / connection / http_status.

  PlaywrightRenderer — headless Chromium rendering. TWO separate browser
    contexts:
      * strict context  — certificate validation ON. A successful goto here
        IS a certificate-validated fetch (method 'verified_browser').
      * evidence context — ignore_https_errors=True, used ONLY for the
        explicit invalid-TLS evidence fallback (method
        'capture_only_tls_invalid'). Renders are marked capture_only and
        NEVER promoted to verified (Phase 3.5 §2).

No proxy rotation, no UA spoofing (the UA is an honest project identifier),
no rate-limit tricks — anti-evasion rules from spec §0 note 6 and Phase 3.5
§3 are structural here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import requests

DEFAULT_UA = "FreshnessChecker"   # same honest identity Part A robots checks have always used

PAGE_SETTLE_MS = 1500
GOTO_TIMEOUT_MS = 30000


# --- strict HTTP transport ---------------------------------------------------

@dataclass
class StrictResult:
    ok: bool
    status: int | None = None
    content_type: str = ""
    body: bytes = b""
    error_kind: str | None = None      # None | 'tls' | 'connection' | 'http_status'
    error_detail: str = ""
    final_url: str = ""                # post-redirect URL (B12 allowlist revalidation)
    redirect_chain: list = field(default_factory=list)  # P02A: URLs actually submitted
    # 'tls' means the certificate failed verification — the original error is
    # preserved in error_detail and must surface in the manifest (§2).


class RequestsTransport:
    """Strict HTTP transport.

    navigation_policy=None retains the LEGACY behavior (a single
    `session.get(..., allow_redirects=True, verify=True)` call with the
    existing result/error semantics). Legacy callers therefore do NOT gain
    pre-request redirect protection by construction — that is the
    policy-enforced branch below, used only when a NavigationPolicy is
    supplied (tracker paths from P02B onward).
    """

    def __init__(self, user_agent: str = DEFAULT_UA, timeout: float = 20.0,
                 navigation_policy=None, session=None):
        # an injected session replaces requests.Session entirely; no second
        # session is created behind it
        self.session = session if session is not None else requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.timeout = timeout
        self.navigation_policy = navigation_policy

    def fetch(self, url: str) -> StrictResult:
        if self.navigation_policy is not None:
            return self._fetch_with_policy(url)
        return self._fetch_legacy(url)

    def _fetch_legacy(self, url: str) -> StrictResult:
        """Legacy branch (navigation_policy is None): automatic redirects,
        existing result and error semantics. redirect_chain is populated from
        the response history when the injected response object provides one;
        simple fakes without a history attribute yield an empty chain."""
        try:
            resp = self.session.get(url, timeout=self.timeout, verify=True, allow_redirects=True)
        except requests.exceptions.SSLError as exc:
            return StrictResult(ok=False, error_kind="tls", error_detail=str(exc)[:200])
        except requests.exceptions.ConnectionError as exc:
            detail = str(exc)
            kind = "tls" if "SSL" in detail or "certificate" in detail.lower() else "connection"
            return StrictResult(ok=False, error_kind=kind, error_detail=detail[:200])
        except requests.exceptions.RequestException as exc:
            return StrictResult(ok=False, error_kind="connection", error_detail=str(exc)[:200])
        if resp.status_code >= 400:
            return StrictResult(ok=False, status=resp.status_code,
                                error_kind="http_status",
                                error_detail=f"http_{resp.status_code}",
                                final_url=str(resp.url))
        chain: list = []
        try:
            history = list(resp.history or []) if getattr(resp, "history", None) else []
            chain = [str(h.url) for h in history] + [str(resp.url)]
        except Exception:      # simple injected fakes may lack usable history
            chain = []
        return StrictResult(ok=True, status=resp.status_code,
                            content_type=resp.headers.get("content-type", ""),
                            body=resp.content, final_url=str(resp.url),
                            redirect_chain=chain)


    def _fetch_with_policy(self, url: str) -> StrictResult:
        """Policy-enforced branch: bounded manual redirects. Every request uses
        allow_redirects=False and verify=True; destinations are validated BEFORE
        contact; evaluation order per redirect is Location presence -> resolved
        destination policy -> loop detection -> hop limit -> next request. Every
        received response is closed exactly once; the injected session is never
        closed here."""
        policy = self.navigation_policy
        chain: list = []
        tried: dict = {}
        last_response_url = ""
        hops = 0

        # initial destination must pass policy before the first get
        if not policy.allows(url):
            return StrictResult(
                ok=False, error_kind="destination_disallowed",
                error_detail=f"initial destination denied by navigation policy: {url}",
                redirect_chain=chain, final_url="")

        request_url = _strip_fragment(url)
        while True:
            chain.append(request_url)
            tried[_redirect_identity(request_url)] = request_url
            try:
                resp = self.session.get(request_url, timeout=self.timeout,
                                        verify=True, allow_redirects=False)
            except Exception as exc:
                kind, detail = _requests_transport_error(exc)
                return StrictResult(
                    ok=False, status=None, error_kind=kind, error_detail=detail,
                    redirect_chain=chain, final_url=last_response_url)

            hops += 1
            last_response_url = str(resp.url)
            status = resp.status_code

            if status not in _REDIRECT_STATUSES:
                body = resp.content            # read terminal bytes before closing
                content_type = _header_value(resp.headers, "content-type")
                _close_quietly(resp)
                if 300 <= status < 400:
                    return StrictResult(
                        ok=False, status=status,
                        error_kind="http_status",
                        error_detail=f"unsupported redirect status {status}",
                        final_url=last_response_url, redirect_chain=chain)
                if status >= 400:
                    return StrictResult(
                        ok=False, status=status, content_type=content_type,
                        error_kind="http_status",
                        error_detail=f"http_{status}",
                        final_url=last_response_url, redirect_chain=chain)
                return StrictResult(
                    ok=True, status=status, content_type=content_type,
                    body=body, final_url=last_response_url, redirect_chain=chain)

            # redirect response: Location presence -> destination policy ->
            # loop detection -> hop limit -> next request
            location = _header_value(resp.headers, "location").strip()
            if not location:
                _close_quietly(resp)
                return StrictResult(
                    ok=False, status=status, error_kind="redirect_missing_location",
                    error_detail=f"redirect response without Location at {last_response_url}",
                    final_url=last_response_url, redirect_chain=chain)

            from urllib.parse import urljoin
            try:
                destination = _strip_fragment(urljoin(last_response_url, location))
            except Exception as exc:
                _close_quietly(resp)
                return StrictResult(
                    ok=False, status=status, error_kind="destination_disallowed",
                    error_detail=f"malformed Location destination {location!r}: {exc}",
                    final_url=last_response_url, redirect_chain=chain)

            if not policy.allows(destination):
                _close_quietly(resp)
                return StrictResult(
                    ok=False, status=status, error_kind="destination_disallowed",
                    error_detail=f"redirect destination denied by navigation policy: {destination}",
                    final_url=last_response_url, redirect_chain=chain)

            destination_identity = _redirect_identity(destination)
            if destination_identity in tried:
                _close_quietly(resp)
                return StrictResult(
                    ok=False, status=status, error_kind="redirect_loop",
                    error_detail=(f"redirect loop: {destination} repeats a previously "
                                  f"requested URL"),
                    final_url=last_response_url, redirect_chain=chain)

            if hops > policy.max_redirects:
                _close_quietly(resp)
                return StrictResult(
                    ok=False, status=status, error_kind="redirect_limit",
                    error_detail=(f"redirect limit reached: {hops} requests made, "
                                  f"following another redirect would exceed "
                                  f"max_redirects={policy.max_redirects}"),
                    final_url=last_response_url, redirect_chain=chain)

            _close_quietly(resp)
            request_url = destination

# --- browser rendering port ---------------------------------------------------

@dataclass
class RenderResult:
    ok: bool
    status: int | None = None
    text: str = ""                  # extracted main text (normalized)
    html: str = ""                  # raw rendered HTML
    screenshot_png: bytes = b""     # full-page screenshot
    error_kind: str | None = None   # None | 'tls' | 'blocked' | 'navigation'
    error_detail: str = ""
    final_url: str = ""             # post-navigation URL (B12 allowlist revalidation)


@dataclass
class Interactable:
    idx: int
    tag: str
    text: str
    attributes: str
    inside_form: bool


class PlaywrightRenderer:
    """Wraps a sync Playwright page with strict + evidence contexts."""

    def __init__(self, browser, user_agent: str = DEFAULT_UA):
        self.browser = browser
        self.user_agent = user_agent
        self._strict_ctx = None
        self._evidence_ctx = None
        self._page = None

    def open(self):
        self._strict_ctx = self.browser.new_context(user_agent=self.user_agent)
        # ignore_https_errors ONLY on the evidence context (Phase 3.5 §2):
        # the strict context always validates certificates.
        self._evidence_ctx = self.browser.new_context(user_agent=self.user_agent,
                                                      ignore_https_errors=True)
        self._page = self._strict_ctx.new_page()
        return self

    def close(self):
        for ctx in (self._strict_ctx, self._evidence_ctx):
            try:
                if ctx:
                    ctx.close()
            except Exception:
                pass

    # -- page-level primitives ------------------------------------------------

    def goto(self, url: str, *, ignore_tls: bool = False) -> RenderResult:
        """Navigate the (strict or evidence) context and extract main text."""
        page = self._page_for(ignore_tls)
        try:
            resp = page.goto(url, timeout=GOTO_TIMEOUT_MS, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            detail = str(exc)
            kind = "tls" if ("ERR_CERT" in detail or "SSL" in detail) else "navigation"
            if "net::ERR" in detail:
                kind = "blocked"
            return RenderResult(ok=False, error_kind=kind, error_detail=detail[:200])
        page.wait_for_timeout(PAGE_SETTLE_MS)
        from .extract import STRIP_AND_EXTRACT_JS, normalize_text
        try:
            raw = page.evaluate(STRIP_AND_EXTRACT_JS)
        except Exception as exc:  # noqa: BLE001
            return RenderResult(ok=False, error_kind="navigation", error_detail=str(exc)[:200])
        try:
            shot = page.screenshot(full_page=True, type="png")
        except Exception:
            shot = b""
        status = resp.status if resp is not None else None
        ok = status is None or status < 400
        return RenderResult(
            ok=ok,
            status=status,
            text=normalize_text(raw or ""),
            html=page.content(),
            screenshot_png=shot,
            error_kind=None if ok else "http_status",
            error_detail="" if ok else f"http_{status}",
            final_url=page.url,
        )

    def _page_for(self, ignore_tls: bool):
        if ignore_tls:
            # switch to the evidence context page (capture-only path)
            if self._evidence_ctx and (self._page is None or self._page.context is not self._evidence_ctx):
                self._page.close()
                self._page = self._evidence_ctx.new_page()
        else:
            if self._strict_ctx and (self._page is None or self._page.context is not self._strict_ctx):
                self._page.close()
                self._page = self._strict_ctx.new_page()
        return self._page

    # -- dynamic-state primitives (Phase 3.5 §6) -------------------------------

    def current_url(self) -> str:
        return self._page.url if self._page else ""

    def ensure_on(self, url: str):
        """Navigate only when not already on the target (saves reloads)."""
        if self._page is not None and self.current_url().rstrip("/") == url.rstrip("/"):
            return
        self.goto(url)

    def body_text(self) -> str:
        from .extract import STRIP_AND_EXTRACT_JS, normalize_text
        try:
            return normalize_text(self._page.evaluate(STRIP_AND_EXTRACT_JS) or "")
        except Exception:
            return ""

    def list_interactables(self) -> list[Interactable]:
        """Candidate interactive elements for safe dynamic-state capture:
        anchors with no real href target (href='#'), buttons, and elements
        with onclick handlers. Real navigation links are handled by link
        discovery, not this path."""
        raw = self._page.evaluate(
            """() => {
                const out = [];
                for (const el of document.querySelectorAll(
                        'a[href="#"], a[href$="#"], button, [onclick], [role="button"]')) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 && rect.height === 0) continue;   // hidden
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || el.value || '').trim().slice(0, 120),
                        attributes: (el.outerHTML || '').slice(0, 300),
                        inside_form: !!el.closest('form'),
                    });
                }
                return out.slice(0, 40);
            }"""
        )
        return [Interactable(idx=i, **item) for i, item in enumerate(raw)]

    def activate(self, item: Interactable) -> bool:
        """Click via a stable locator (preferred: text, then class/id token)."""
        page = self._page
        try:
            if item.text:
                loc = page.locator(f"{item.tag}:has-text(\"{item.text[:60]}\")").first
                loc.click(timeout=5000)
                return True
            m = re.search(r'id="([^"]+)"', item.attributes) or re.search(r'class="([^"]+)"', item.attributes)
            if m:
                token = m.group(1).split()[0]
                sel = f"#{token}" if (m.re.pattern.startswith("id")) else f".{token}"
                page.locator(sel).first.click(timeout=5000)
                return True
        except Exception:
            return False
        return False

    def settle(self, ms: int = 2000):
        self._page.wait_for_timeout(ms)

    # -- link discovery (§5) and state snapshots (§6) ---------------------------

    def discover_links(self, url: str) -> list:
        """All candidate hrefs on a page (anchors + onclick navigations).
        The crawler applies domain/allowlist/URL policy — this is raw data."""
        try:
            self.goto(url)
            if not self._page:
                return []
            return self._page.evaluate(
                """() => {
                    const out = new Set();
                    for (const a of document.querySelectorAll('a[href]')) {
                        if (a.href && a.href.startsWith('http')) out.add(a.href.split('#')[0]);
                    }
                    for (const el of document.querySelectorAll('[onclick]')) {
                        const m = (el.getAttribute('onclick') || '').match(/location(?:\\.href)?\\s*=\\s*'([^']+)'/);
                        if (m) out.add(new URL(m[1], location.href).href);
                    }
                    return Array.from(out);
                }"""
            )
        except Exception:
            return []

    def snapshot_state(self) -> dict:
        """Capture the CURRENT rendered state (text/html/png) — used for
        dynamic states after a safe interaction. No navigation."""
        from .extract import normalize_text
        try:
            shot = self._page.screenshot(full_page=True, type="png")
        except Exception:
            shot = b""
        try:
            html = self._page.content()
        except Exception:
            html = ""
        return {
            "text": normalize_text(self.body_text() or ""),
            "html": html,
            "png": shot,
        }


# ---------------------------------------------------------------------------
# P02A policy-enforced branch (visibly separate from the legacy path above)
# ---------------------------------------------------------------------------

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_DEFAULT_PORTS = {"http": 80, "https": 443}


def _strip_fragment(url: str) -> str:
    """Remove the fragment for the actual HTTP request and loop identity;
    path case and query parameter order are preserved."""
    from urllib.parse import urlsplit, urlunsplit
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _redirect_identity(url: str) -> tuple:
    """Loop identity: equates scheme/host case, default ports and an empty
    path with '/'. Query is preserved (different query = different request)."""
    from urllib.parse import urlsplit
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port is None:
        port = _DEFAULT_PORTS.get(scheme)
    path = parts.path or "/"
    return (scheme, host, port, path, parts.query)


def _header_value(headers, name: str) -> str:
    """Case-insensitive header lookup over a mapping-like object."""
    try:
        items = headers.items()
    except Exception:
        return ""
    wanted = name.lower()
    for key, value in items:
        if str(key).lower() == wanted:
            return str(value)
    return ""


def _close_quietly(response) -> None:
    try:
        response.close()
    except Exception:
        pass


def _requests_transport_error(exc: Exception) -> tuple[str, str]:
    """Existing transport error classification, shared with the legacy path."""
    import requests.exceptions as req_exc
    if isinstance(exc, req_exc.SSLError):
        return "tls", str(exc)[:200]
    if isinstance(exc, req_exc.ConnectionError):
        detail = str(exc)
        kind = "tls" if "SSL" in detail or "certificate" in detail.lower() else "connection"
        return kind, detail[:200]
    if isinstance(exc, req_exc.RequestException):
        return "connection", str(exc)[:200]
    return "connection", f"{type(exc).__name__}: {exc}"[:200]
