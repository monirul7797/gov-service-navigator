"""
build_visa_raw.py — Part B §B2–B4: seed-URL fetch + raw staging for the visa pilot.

Discovers same-domain pages from a curated seed list (bounded depth ≤ 2),
fetches each with freshness/fetch.py (the SAME fetcher as Part A — no
rewrites), and stages text into data/raw/visa/ following the existing
convention: SECTION: / SOURCE: / LANGUAGE: header, blank-line separators,
no per-entry metadata footer (metadata belongs in visa.jsonl downstream).

Blocked-domain handling is identical to Part A: no evasion. Pages that fail
to fetch (wizard/login-only pages, TLS errors, HTTP 4xx/5xx) are recorded in
the manifest and simply not staged.

§B2 NOTE (PII page): the spec says a prior session parked visa/MRV material
with one PII page to re-exclude. That pile does not exist on this machine
(repo, data/, Codex folder, Desktop/Downloads, and prior session artifacts
were all searched on 2026-09-03). Nothing was found to exclude, so nothing
was silently dropped — but if the pile is ever restored, re-run the PII
audit BEFORE staging any of it.
# TODO(Monirul): confirm whether the prior visa pile survives elsewhere
# (another machine/backup); if so, locate the MRV page with real PII and
# re-exclude it per spec §0 note 3 before it touches data/raw/visa/.

Run:
    python build_visa_raw.py            # discover + fetch + stage
    python build_visa_raw.py --dry-run  # discovery only, no writes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "freshness"))
import fetch as freshness_fetch   # Part A fetcher — reused, not rewritten

REPO_ROOT = freshness_fetch.REPO_ROOT
OUT_DIR = os.path.join(REPO_ROOT, "data", "raw", "visa")
MANIFEST_PATH = os.path.join(OUT_DIR, "visa_manifest.json")

# Curated seeds (spec §B3): the official Bangladesh Online (e) Visa portal.
# NOTE: https://www.visa.gov.bd (www host) resets connections; apex works.
SEEDS = [
    "https://visa.gov.bd/",
]

# Same-domain nav pages that are application-wizard/portal screens, not
# informational content (spec §0 note 3: logged-in/wizard pages excluded).
WIZARD_URL_MARKERS = ("/ViewStatus", "/Reprint", "/Incomplete", "/ApplyVisa",
                      "/Login", "/Payment", "/personal", "/Applicant")

# Depth-2 link prioritization: which discovered links are worth fetching for
# a process/fee/document/FAQ-shaped corpus. Terms/Privacy/Complaints pages are
# legal/site boilerplate, not service content — deliberately not staged.
USEFUL_LINK_RE = re.compile(
    r"faq|fee|fees|visa.?fee|price|apply|application|how.?to|requirement|"
    r"document|notice|circular|instruction|guide|help|mrvc?|support|countries?"
    r"|eligib|process|step|download|form",
    re.I,
)
USELESS_LINK_RE = re.compile(
    r"logout|signin|sign-in|register\.aspx|javascript:|mailto:|tel:"
    r"|TermsCondition|PrivacyPolicy|CompFeedback",
    re.I,
)

MAX_DEPTH = 2
MAX_PAGES = 40          # hard cap on staged/attempted pages per run
FETCH_INTERVAL = 1.0    # same rate limit as Part A


def log(msg: str) -> None:
    print(msg, flush=True)


def discover_links(page, url: str) -> list[tuple[str, str]]:
    """Return same-domain (href, anchor_text) pairs on a page."""
    try:
        page.goto(url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(freshness_fetch.PAGE_SETTLE_MS)
        items = page.evaluate(
            """() => Array.from(document.querySelectorAll('a'))
                   .map(a => ({href: a.href, text: (a.innerText || '').trim()}))"""
        )
    except Exception as exc:  # noqa: BLE001
        log(f"  discover failed for {url}: {str(exc)[:90]}")
        return []
    domain = urllib.parse.urlparse(url).netloc
    out, seen = [], set()
    for item in items:
        u = item.get("href") or ""
        if not u.startswith("http") or USELESS_LINK_RE.search(u):
            continue
        if urllib.parse.urlparse(u).netloc != domain:
            continue
        u = u.split("#")[0]
        if not u or u in seen:
            continue
        seen.add(u)
        out.append((u, item.get("text", "")))
    return out


def classify(url: str, anchor: str) -> str:
    if any(m in url for m in WIZARD_URL_MARKERS):
        return "wizard"
    if USEFUL_LINK_RE.search(url) or USEFUL_LINK_RE.search(anchor):
        return "useful"
    return "other"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="discovery only, write nothing")
    args = ap.parse_args()

    manifest = {
        "service": "visa",
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seeds": SEEDS,
        "excluded_pii": {
            "status": "not_located",
            "note": "Spec B2 says a prior session parked visa material containing "
                    "one real-PII MRV page. That pile was searched for on 2026-09-03 "
                    "(repo, data/, Documents incl. Codex folder, Desktop, Downloads, "
                    "prior agent-session artifacts) and does not exist on this "
                    "machine. Nothing was processed or excluded because nothing was "
                    "found. If the pile is restored, re-run the PII audit before use.",
            "pages": [],
        },
        "wizard_pages_excluded": [],
        "blocked_domains": [],
        "fetch_failures": [],
        "staged_files": [],
    }
    # The DIP site (Department of Immigration & Passports) serves an invalid TLS
    # certificate on both apex and www — treated like orgbdr.gov.bd: not fetched,
    # no cert-bypass (verify gate must stay intact); a human can screenshot it.
    manifest["blocked_domains"].append({
        "domain": "dip.gov.bd",
        "reason": "ssl_cert_invalid (www.dip.gov.bd: ERR_CERT_AUTHORITY_INVALID; apex SSL error)",
        "disposition": "needs_manual_screenshot",
    })

    from playwright.sync_api import sync_playwright
    staged: dict[str, dict] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # ---- BFS discovery, depth ≤ 2 ------------------------------------
        frontier = [(u, "", 1) for u in SEEDS]
        queued: set[str] = set(SEEDS)
        attempt_order: list[tuple[str, str]] = []

        while frontier and len(attempt_order) < MAX_PAGES:
            url, anchor, depth = frontier.pop(0)
            log(f"[discover d{depth}] {url}  ({anchor[:40]})")
            kind = classify(url, anchor)
            if kind == "wizard":
                manifest["wizard_pages_excluded"].append({"url": url, "anchor": anchor[:80]})
                continue
            attempt_order.append((url, anchor))
            if depth >= MAX_DEPTH:
                continue
            for child_url, child_anchor in discover_links(page, url):
                if child_url in queued:
                    continue
                queued.add(child_url)
                child_kind = classify(child_url, child_anchor)
                if child_kind == "useful":
                    frontier.append((child_url, child_anchor, depth + 1))
                elif child_kind == "wizard":
                    manifest["wizard_pages_excluded"].append(
                        {"url": child_url, "anchor": child_anchor[:80]})

        # ---- targeted captures: JS-modal content on the homepage ----------
        # The "Current visa fees are available Here" link (a.clsFees) and the
        # "supported countries" link both expand jQuery dialogs on the SAME
        # page — no URL of their own. Content is captured post-click and
        # staged with SOURCE: the homepage + an interaction note.
        def capture_homepage_dialog(click_selector: str, label: str) -> str | None:
            try:
                page.goto(SEEDS[0], timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(freshness_fetch.PAGE_SETTLE_MS)
                page.click(click_selector, timeout=8000)
                page.wait_for_timeout(2000)
                text = page.evaluate(
                    """() => {
                        const el = document.querySelector('.ui-dialog')
                                 || document.querySelector('.simplemodal-wrap')
                                 || document.querySelector('[id*="dialog" i]');
                        return el ? el.innerText : null;
                    }"""
                )
                if text and len(text.strip()) > 100:
                    return freshness_fetch._normalize(text)
                log(f"  {label}: dialog not found or too short")
            except Exception as exc:  # noqa: BLE001
                log(f"  {label}: capture failed: {str(exc)[:90]}")
            return None

        fee_text = capture_homepage_dialog("a.clsFees", "fee dialog")
        countries_text = capture_homepage_dialog("text=supported countries",
                                                 "supported-countries dialog")

        # MRV blank form PDF — verify it is fetchable (same domain), note in manifest.
        mrv_pdf_url = "https://visa.gov.bd/Img/MRV_FORM.pdf"
        try:
            resp = page.request.get(mrv_pdf_url, timeout=30000)
            manifest["mrv_form_pdf"] = {
                "url": mrv_pdf_url,
                "http_status": resp.status,
                "content_type": resp.headers.get("content-type", ""),
                "bytes": len(resp.body()),
            }
            log(f"  MRV pdf: HTTP {resp.status}, {resp.headers.get('content-type', '')}")
        except Exception as exc:  # noqa: BLE001
            manifest["mrv_form_pdf"] = {"url": mrv_pdf_url, "error": str(exc)[:120]}

        # ---- fetch + stage ------------------------------------------------
        robots_cache = freshness_fetch._RobotsCache()
        first = True
        for url, anchor in attempt_order:
            if len(staged) >= MAX_PAGES:
                break
            if not first:
                time.sleep(FETCH_INTERVAL)
            first = False

            allowed, note = freshness_fetch._robots_check(robots_cache, url, "FreshnessChecker")
            if not allowed:
                manifest["fetch_failures"].append({"url": url, "reason": "robots_disallowed"})
                continue
            try:
                text, status = freshness_fetch.fetch_page_text(page, url)
            except Exception as exc:  # noqa: BLE001
                manifest["fetch_failures"].append(
                    {"url": url, "reason": str(exc)[:140] or type(exc).__name__})
                log(f"  FETCH FAIL {url}: {str(exc)[:80]}")
                continue
            if len(text) < 200:
                manifest["fetch_failures"].append(
                    {"url": url, "reason": f"too_little_text ({len(text)} chars)"})
                continue

            slug = re.sub(r"[^A-Za-z0-9]+", "_", url.split("//", 1)[1]).strip("_")[:60]
            fname = f"{len(staged):02d}_{slug}.txt"
            section = ("faq" if "faq" in url.lower()
                       else "mrv-form" if "mrv" in url.lower()
                       else "portal" if url.rstrip("/") == "https://visa.gov.bd"
                       else "information")
            lang = "bn+en" if re.search(r"[\u0980-\u09FF]", text) else "en"
            header = f"SECTION: {section}\nSOURCE: {url}\nLANGUAGE: {lang}\n\n"
            if not args.dry_run:
                os.makedirs(OUT_DIR, exist_ok=True)
                with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
                    f.write(header + text + "\n")
            staged[url] = {"file": fname, "section": section, "lang": lang,
                           "anchor": anchor[:80], "chars": len(text)}
            manifest["staged_files"].append(staged[url])
            log(f"  staged {fname} ({len(text)} chars, {lang})")

        browser.close()

    # ---- stage the two JS-dialog captures (post-click homepage states) ----
    for label, text, section in [
        ("fee_table", fee_text, "fees"),
        ("supported_countries", countries_text, "eligibility"),
    ]:
        if not text:
            continue
        fname = f"9x_visa_gov_bd_{label}.txt"
        header = (f"SECTION: {section}\nSOURCE: {SEEDS[0]}\nLANGUAGE: en\n"
                  f"NOTE: content revealed by clicking the '{label}' link on the "
                  f"homepage (jQuery dialog — no URL of its own).\n\n")
        if not args.dry_run:
            with open(os.path.join(OUT_DIR, fname), "w", encoding="utf-8") as f:
                f.write(header + text + "\n")
        staged[fname] = {"file": fname, "section": section, "lang": "en",
                         "anchor": f"homepage dialog: {label}", "chars": len(text)}
        manifest["staged_files"].append(staged[fname])
        log(f"  staged {fname} ({len(text)} chars)")

    if not args.dry_run:
        os.makedirs(OUT_DIR, exist_ok=True)
        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        log(f"\nmanifest: {MANIFEST_PATH}")
    log(f"staged {len(staged)} pages; wizard-excluded {len(manifest['wizard_pages_excluded'])}; "
        f"failures {len(manifest['fetch_failures'])}")


if __name__ == "__main__":
    main()
