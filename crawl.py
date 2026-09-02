"""
crawl.py — CLI entry point for the Phase 3.5 acquisition crawler.

Reusable for any service: pass seeds + an optional domain allowlist; the
crawler handles bounded same-domain discovery, URL-policy exclusions,
evidence artifacts, safe dynamic-state capture, and a provenance manifest.

Examples:
    python crawl.py --service visa --seed https://visa.gov.bd/
    python crawl.py --service dip_gov_bd --seed https://dip.gov.bd/ --depth 1

Output: data/evidence/<service>/  (artifacts + manifest.json)
"""
from __future__ import annotations

import argparse
import os

from acquisition.crawler import CrawlConfig, Crawler, save_manifest
from acquisition.transport import PlaywrightRenderer, RequestsTransport

REPO_ROOT = r"C:\Users\Monirul\Documents\Structured Data\NID\gov-service-navigator\gov-service-navigator"


def main() -> None:
    ap = argparse.ArgumentParser(description="Provenance-aware bounded gov-site crawler")
    ap.add_argument("--service", required=True, help="output name under data/evidence/")
    ap.add_argument("--seed", required=True, action="append", help="seed URL (repeatable)")
    ap.add_argument("--allow", action="append", default=None,
                    help="domain allowlist entry (default: the seeds' hosts)")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--no-screenshots", action="store_true")
    args = ap.parse_args()

    allowlist = {a.lower() for a in args.allow} if args.allow else None
    cfg = CrawlConfig(seeds=args.seed, allowlist=allowlist, max_depth=args.depth,
                      max_pages=args.max_pages, also_screenshot=not args.no_screenshots)
    out_dir = os.path.join(REPO_ROOT, "data", "evidence", args.service)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        renderer = PlaywrightRenderer(browser).open()
        try:
            crawler = Crawler(cfg, transport=RequestsTransport(),
                              renderer=renderer, out_dir=out_dir)
            manifest = crawler.run()
        finally:
            renderer.close()
            browser.close()

    manifest_path = os.path.join(out_dir, "manifest.json")
    save_manifest(manifest, manifest_path)
    print(f"summary: {manifest['summary']['dispositions']}")
    print(f"manifest: {manifest_path}")


if __name__ == "__main__":
    main()
