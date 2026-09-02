"""
acquisition/acquire.py — the layered capture primitive (Phase 3.5 §1/§2/§8/§9).

Fallback hierarchy (each level attempted only if the previous did not yield
a usable capture; verification status is NEVER inflated):

    1. strict normal fetch (requests, TLS verified)
         → HTML with extractable text : method='verified_normal'
         → PDF/binary download        : method='direct_pdf_verified'
    2. headless Chromium, strict context (certificate validation ON)
         → method='verified_browser'  (a successful goto IS cert-validated)
    3. headless Chromium, evidence context (ignore_https_errors=True)
         → method='capture_only_tls_invalid', verification_status='capture_only',
           requires_secondary_verification=True — original TLS error preserved
    4. browser also blocked/failed → disposition 'needs_manual_screenshot'
       (manual screenshots are the LAST fallback, never the first)

Evidence artifacts per successful capture (§8): raw HTML, clean extracted
text, full-page PNG (browser captures), provenance JSON; PDFs preserved
verbatim. Filesystem-safe deterministic names; duplicates (same content
hash) are the caller's concern via the returned content_sha256.

This module never writes to data/vectordb/ or any corpus .jsonl — it
produces evidence only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .extract import normalize_text
from .robots import RobotsCache, robots_check
from .transport import RenderResult, RequestsTransport, StrictResult
from .urls import classify_url, normalize_url

# Method taxonomy (§9 must be able to distinguish all of these)
VERIFIED_NORMAL = "verified_normal"
VERIFIED_BROWSER = "verified_browser"
CAPTURE_ONLY_TLS = "capture_only_tls_invalid"
DIRECT_PDF_VERIFIED = "direct_pdf_verified"
DIRECT_PDF_UNVERIFIED = "direct_pdf_unverified"

DISPOSITION_NEEDS_MANUAL = "needs_manual_screenshot"
DISPOSITION_FAILED = "failed_fetch"

# an HTML capture with less extracted text than this is treated as JS-only
MIN_STATIC_TEXT_CHARS = 200


@dataclass
class Capture:
    url: str
    normalized_url: str
    method: str                              # VERIFIED_* / CAPTURE_ONLY_TLS / …
    disposition: str                         # 'captured' | DISPOSITION_NEEDS_MANUAL | DISPOSITION_FAILED
    verification_status: str                 # 'verified' | 'capture_only' | 'unreachable'
    tls_verified: bool
    requires_secondary_verification: bool = False
    tls_error: str | None = None             # original TLS failure — preserved (§2)
    http_status: int | None = None
    rendered: bool = False
    content_sha256: str = ""
    text: str = ""
    text_path: str = ""
    html_path: str = ""
    png_path: str = ""
    pdf_path: str = ""
    notes: list = field(default_factory=list)

    def to_provenance(self) -> dict:
        return {
            "url": self.url,
            "normalized_url": self.normalized_url,
            "acquisition_method": self.method,
            "http_status": self.http_status,
            "tls_verified": self.tls_verified,
            "tls_error": self.tls_error,
            "rendered": self.rendered,
            "verification_status": self.verification_status,
            "requires_secondary_verification": self.requires_secondary_verification,
            "content_sha256": self.content_sha256,
            "disposition": self.disposition,
            "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "artifacts": {
                "text": self.text_path or None,
                "html": self.html_path or None,
                "screenshot_png": self.png_path or None,
                "pdf": self.pdf_path or None,
            },
            "notes": self.notes,
        }


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _slug(url: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", normalize_url(url).split("//", 1)[-1]).strip("_")
    return slug[:60] or "page"


def _write(out_dir: str, name: str, data: bytes | str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
        f.write(data)
    return path


def extract_text_from_html(html: str) -> str:
    """Deterministic static-HTML text extraction mirroring the DOM strip rules
    (script/style/noscript/nav/footer/header/aside/iframe removed). Used only
    on the strict-fetch path; rendered captures use the DOM extractor."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:      # bs4 is in requirements_phase2.txt; fail loud if truly absent
        raise
    soup = BeautifulSoup(html, "html.parser")
    for sel in ("script", "style", "noscript", "nav", "footer", "header", "aside", "iframe"):
        for el in soup.find_all(sel):
            el.decompose()
    body = soup.body or soup
    return normalize_text(body.get_text("\n"))


def acquire_url(url: str, *, transport: RequestsTransport, renderer,
                robots: RobotsCache, out_dir: str, user_agent: str,
                save_screenshot: bool = True, also_screenshot: bool = False) -> Capture:
    """Capture one URL through the fallback hierarchy. Writes evidence
    artifacts under out_dir and returns a provenance-complete Capture.

    also_screenshot: for verified_normal captures (no browser involved), take
    a post-capture browser screenshot purely as human-review evidence — the
    method/verification status stays verified_normal/verified (the PNG is
    evidence, never a promotion of trust)."""
    normalized = normalize_url(url)
    cap = Capture(url=url, normalized_url=normalized, method="",
                  disposition=DISPOSITION_FAILED, verification_status="unreachable",
                  tls_verified=False)

    disposition, reason = classify_url(normalized)
    if disposition in {"invalid", "wizard"}:
        cap.disposition = disposition
        cap.notes.append(reason)
        return cap

    allowed, robots_note = robots_check(robots, normalized, user_agent)
    if not allowed:
        cap.disposition = DISPOSITION_NEEDS_MANUAL
        cap.notes.append("robots_disallowed (explicit directive honored, never evaded)")
        return cap
    if robots_note:
        cap.notes.append(robots_note)

    # ---- 1) strict normal fetch (TLS verified) ---------------------------
    strict: StrictResult = transport.fetch(normalized)
    if strict.ok:
        cap.http_status = strict.status
        cap.tls_verified = True
        ctype = strict.content_type.lower()
        if "pdf" in ctype or disposition == "pdf":
            digest = _sha256(strict.body)
            cap.method = DIRECT_PDF_VERIFIED
            cap.disposition = "captured"
            cap.verification_status = "verified"
            cap.content_sha256 = digest
            cap.pdf_path = _write(out_dir, f"{_slug(url)}.pdf", strict.body)
            return cap
        if "html" in ctype or ctype == "":
            text = extract_text_from_html(strict.body.decode("utf-8", errors="replace"))
            if len(text) >= MIN_STATIC_TEXT_CHARS:
                cap.method = VERIFIED_NORMAL
                cap.disposition = "captured"
                cap.verification_status = "verified"
                cap.rendered = False
                cap.text = text
                cap.content_sha256 = _sha256(text.encode("utf-8"))
                cap.html_path = _write(out_dir, f"{_slug(url)}.html", strict.body)
                cap.text_path = _write(out_dir, f"{_slug(url)}.txt", text)
                if also_screenshot:
                    try:
                        shot_render = renderer.goto(normalized, ignore_tls=False)
                        if shot_render.ok and shot_render.screenshot_png:
                            cap.png_path = _write(out_dir, f"{_slug(url)}.png",
                                                  shot_render.screenshot_png)
                            cap.notes.append("post-capture verification screenshot "
                                             "taken (method unchanged: verified_normal)")
                    except Exception:  # noqa: BLE001 — screenshot is best-effort evidence
                        pass
                return cap
            # else: JS-only content — fall through to the browser layer
        # other content types: treat via browser render for completeness

    # ---- 2) headless Chromium, strict context (cert validation ON) -------
    render: RenderResult = renderer.goto(normalized, ignore_tls=False)
    if render.ok:
        cap.http_status = render.status
        cap.tls_verified = True               # Chromium validated the certificate
        cap.method = VERIFIED_BROWSER
        cap.disposition = "captured"
        cap.verification_status = "verified"
        cap.rendered = True
        cap.text = render.text
        cap.content_sha256 = _sha256(render.text.encode("utf-8"))
        cap.html_path = _write(out_dir, f"{_slug(url)}.html", render.html)
        cap.text_path = _write(out_dir, f"{_slug(url)}.txt", render.text)
        if save_screenshot and render.screenshot_png:
            cap.png_path = _write(out_dir, f"{_slug(url)}.png", render.screenshot_png)
        return cap

    # ---- 3) invalid-TLS evidence fallback (ignore_https_errors ONLY here) --
    if render.error_kind == "tls" or strict.error_kind == "tls":
        cap.tls_error = render.error_detail or strict.error_detail or "certificate_invalid"
        evidence = renderer.goto(normalized, ignore_tls=True)
        cap.http_status = evidence.status
        cap.rendered = evidence.ok
        if evidence.ok:
            cap.method = CAPTURE_ONLY_TLS
            cap.disposition = "captured"
            cap.verification_status = "capture_only"
            cap.requires_secondary_verification = True
            cap.tls_verified = False
            cap.text = evidence.text
            cap.content_sha256 = _sha256(evidence.text.encode("utf-8"))
            cap.html_path = _write(out_dir, f"{_slug(url)}.html", evidence.html)
            cap.text_path = _write(out_dir, f"{_slug(url)}.txt", evidence.text)
            if save_screenshot and evidence.screenshot_png:
                cap.png_path = _write(out_dir, f"{_slug(url)}.png", evidence.screenshot_png)
            cap.notes.append(
                "capture_only: rendered with certificate errors ignored for "
                "evidence; NOT verified; requires secondary verification")
            return cap
        cap.notes.append(f"evidence render also failed: {evidence.error_detail}")

    # ---- 4) blocked / failed — manual screenshot is the last fallback -----
    # A site actively refusing automated clients (401/403/429/503) is a block:
    # needs_manual_screenshot, never evaded. A plain dead link (404/410) is a
    # failed fetch, not a block.
    if render.error_kind == "blocked" or (render.status in {401, 403, 429, 503}):
        cap.disposition = DISPOSITION_NEEDS_MANUAL
    else:
        cap.disposition = DISPOSITION_FAILED
    cap.http_status = render.status
    cap.notes.append(f"strict: {strict.error_kind or 'ok'} {strict.error_detail[:120]}")
    cap.notes.append(f"browser: {render.error_kind} {render.error_detail[:120]}")
    return cap
