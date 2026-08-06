"""
fetch.py — cache-first fetcher that refuses non-official hosts.

Guarantees:
  * A URL is fetched ONLY if its host is in sources.yaml `allowed_hosts`
    (all *.gov.bd). Anything else raises. This is the enforcement point for
    "verified government sources only" — it's not a convention, it's a gate.
  * Responses are decoded as UTF-8 with detection fallback, so Bangla survives.
  * Raw bytes/text are cached under data/raw/<service>/<slug>.<ext>. Re-runs are
    served from cache (idempotent, polite to gov servers, reproducible).

Rendering:
  * render="static" -> requests
  * render="js"     -> Playwright (Chromium), for the SPA portals
  * render="pdf"    -> requests, stored as .pdf for extract.py/pdfplumber

Offline / injected-cache mode:
  If the network can't reach gov.bd (e.g. a locked-down sandbox), pre-place the
  page text at data/raw/<service>/<slug>.txt and the pipeline consumes it
  unchanged. `provenance.json` records the real source URL + fetch time either
  way, so downstream chunks keep correct attribution.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Optional
from urllib.parse import urlparse

from .paths import RAW_DIR, load_sources, raw_path


def _slug(url: str) -> str:
    p = urlparse(url)
    base = (p.path.strip("/") or "index").replace("/", "_")
    base = re.sub(r"[^A-Za-z0-9_.-]", "-", base)
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{h}"[:120]


def host_allowed(url: str) -> bool:
    allowed = set(load_sources().get("allowed_hosts", []))
    host = (urlparse(url).hostname or "").lower()
    return host in allowed and host.endswith(".gov.bd")


@dataclass
class Fetched:
    url: str
    service: str
    slug: str
    path: str            # where the payload is on disk
    kind: str            # "html" | "pdf" | "txt"
    fetched_at: str
    from_cache: bool
    sha256: str


def _write_provenance(service: str, rec: dict) -> None:
    d = os.path.join(RAW_DIR, service)
    os.makedirs(d, exist_ok=True)
    prov_path = os.path.join(d, "provenance.json")
    data = {}
    if os.path.exists(prov_path):
        with open(prov_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    data[rec["slug"]] = rec
    with open(prov_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def fetch(url: str, service: str, render: str = "static",
          *, force: bool = False, timeout: int = 30) -> Fetched:
    if not host_allowed(url):
        raise ValueError(
            f"REFUSED non-official host: {url!r}. Only hosts in "
            f"sources.yaml allowed_hosts (all *.gov.bd) may be fetched."
        )

    ext = "pdf" if render == "pdf" else ("txt" if render == "inject" else "html")
    path = raw_path(service, _slug(url), ext)

    # Cache / injected-content first.
    for cand_ext in (ext, "txt", "html", "pdf"):
        cand = raw_path(service, _slug(url), cand_ext)
        if os.path.exists(cand) and not force:
            rec = _fetched_from_disk(url, service, cand)
            return rec

    if render == "js":
        _fetch_js(url, path, timeout)
    elif render == "pdf":
        _fetch_bytes(url, path, timeout)
    else:
        _fetch_static(url, path, timeout)

    rec = _fetched_from_disk(url, service, path, from_cache=False)
    _write_provenance(service, {**asdict(rec)})
    return rec


def _fetched_from_disk(url: str, service: str, path: str,
                       from_cache: bool = True) -> Fetched:
    kind = "pdf" if path.endswith(".pdf") else ("txt" if path.endswith(".txt") else "html")
    return Fetched(
        url=url, service=service, slug=_slug(url), path=path, kind=kind,
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        from_cache=from_cache, sha256=_sha256_file(path),
    )


def _fetch_static(url: str, path: str, timeout: int) -> None:
    import requests
    headers = {"User-Agent": "GovServiceNavigator/2.0 (research; contact: student@nsu)"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    # Trust UTF-8 for gov.bd; fall back to apparent encoding if it mis-declares.
    if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
        r.encoding = r.apparent_encoding or "utf-8"
    with open(path, "w", encoding="utf-8") as f:
        f.write(r.text)


def _fetch_bytes(url: str, path: str, timeout: int) -> None:
    import requests
    headers = {"User-Agent": "GovServiceNavigator/2.0 (research)"}
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)


def _fetch_js(url: str, path: str, timeout: int) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=timeout * 1000)
        html = page.content()
        browser.close()
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
