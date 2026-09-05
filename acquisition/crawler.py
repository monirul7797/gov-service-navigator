"""
acquisition/crawler.py — reusable bounded, seed-driven crawler with
provenance and safe dynamic-state capture (Phase 3.5 §4/§5/§6/§7/§9).

Not visa-specific: configure seeds + domain allowlist + depth and it works
for any service (visa, NID, ePassport, BDRIS, eReturn, future ones).

Guarantees:
  * bounded — max depth (default 2), max pages (default 40), max interactions
    per page, ~1 request/second
  * same-domain only by default, configurable allowlist
  * URL-level exclusions BEFORE navigation: wizard/login/PII patterns,
    boilerplate, explicit PII URL list (recorded, never silently dropped)
  * every skipped URL records WHY (§9)
  * crawl graph: each node records its parent (which page linked to it) and
    discovery depth
  * safe dynamic-state capture with before/after comparison and per-state
    provenance (trigger text, parent page, content hash)
  * no auto-clicking of submit/apply/login/pay/etc. (safeclick policy);
    ambiguous interactions are skipped with TODO(Monirul)
  * duplicate captures (same content_sha256) recorded as 'duplicate'
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

from .acquire import (CAPTURE_ONLY_TLS, DIRECT_PDF_UNVERIFIED, DIRECT_PDF_VERIFIED,
                      DISPOSITION_FAILED, DISPOSITION_NEEDS_MANUAL, VERIFIED_BROWSER,
                      VERIFIED_NORMAL, acquire_url)
from .robots import RobotsCache
from .safeclick import AMBIGUOUS, SAFE, UNSAFE, classify_element
from .transport import DEFAULT_UA
from .urls import allowed_domain, classify_url, normalize_url


def _write_artifact(out_dir: str, name: str, data) -> str:
    """Write one evidence artifact (bytes or str); returns the path."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name)
    with open(path, "wb" if isinstance(data, bytes) else "w",
              **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
        f.write(data)
    return path


@dataclass
class CrawlConfig:
    seeds: list
    allowlist: set = None                     # default: hosts of the seeds
    max_depth: int = 2
    max_pages: int = 40
    max_interactions_per_page: int = 8
    interval_seconds: float = 1.0
    user_agent: str = DEFAULT_UA
    skip_boilerplate: bool = True
    excluded_pii_urls: set = None             # exact normalized URLs to never process
    min_state_delta_chars: int = 50           # meaningful-new-content threshold
    also_screenshot: bool = True              # PNG evidence even for verified_normal pages

    def __post_init__(self):
        if self.allowlist is None:
            hosts = set()
            for s in self.seeds:
                host = normalize_url(s).split("//")[-1].split("/")[0]
                if host:
                    hosts.add(host)
            self.allowlist = hosts
        if self.excluded_pii_urls is None:
            self.excluded_pii_urls = set()


@dataclass
class _Node:
    url: str
    parent: str = ""
    depth: int = 0


class Crawler:
    def __init__(self, config: CrawlConfig, *, transport, renderer, out_dir: str):
        self.cfg = config
        self.transport = transport
        self.renderer = renderer
        self.out_dir = out_dir
        self.robots = RobotsCache()
        self.nodes: list[dict] = []           # crawl graph / manifest nodes
        self.content_hashes: set = set()      # duplicate detection
        self.page_count = 0

    # -- manifest helpers -----------------------------------------------------

    def _record(self, node: dict) -> None:
        node["captured_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.nodes.append(node)

    def _sleep(self):
        time.sleep(self.cfg.interval_seconds)

    # -- main entry -------------------------------------------------------------

    def run(self) -> dict:
        seen: set = set()
        queue: deque = deque()
        for seed in self.cfg.seeds:
            n = normalize_url(seed)
            if n:
                queue.append(_Node(n, parent="", depth=0))
                seen.add(n)

        while queue and self.page_count < self.cfg.max_pages:
            node = queue.popleft()
            disposition, reason = classify_url(node.url, skip_boilerplate=self.cfg.skip_boilerplate)

            if disposition in {"invalid", "wizard", "boilerplate"}:
                self._record({"url": node.url, "parent": node.parent, "depth": node.depth,
                              "disposition": f"excluded_{disposition}" if disposition != "invalid" else "invalid_url",
                              "reason": reason})
                continue
            if node.url in self.cfg.excluded_pii_urls:
                self._record({"url": node.url, "parent": node.parent, "depth": node.depth,
                              "disposition": "excluded_pii",
                              "reason": "explicit PII exclusion list (spec §0 note 3)"})
                continue
            if not allowed_domain(node.url, self.cfg.allowlist):
                self._record({"url": node.url, "parent": node.parent, "depth": node.depth,
                              "disposition": "excluded_out_of_allowlist",
                              "reason": "outside the crawl's domain allowlist"})
                continue

            self._sleep()
            capture = acquire_url(node.url, transport=self.transport, renderer=self.renderer,
                                  robots=self.robots, out_dir=self.out_dir,
                                  user_agent=self.cfg.user_agent,
                                  also_screenshot=self.cfg.also_screenshot,
                                  allowed_domains=set(self.cfg.allowlist))
            method_to_disposition = {
                VERIFIED_NORMAL: "captured_verified_normal",
                VERIFIED_BROWSER: "captured_verified_browser",
                CAPTURE_ONLY_TLS: "capture_only_tls_invalid",
                DIRECT_PDF_VERIFIED: "direct_pdf_verified",
                DIRECT_PDF_UNVERIFIED: "direct_pdf_unverified",
                "redirected_out_of_allowlist": "redirected_out_of_allowlist",
                DISPOSITION_NEEDS_MANUAL: "needs_manual_screenshot",
                DISPOSITION_FAILED: "failed_fetch",
            }
            entry = {"url": node.url, "parent": node.parent, "depth": node.depth,
                     "disposition": method_to_disposition.get(capture.method, capture.disposition),
                     "provenance": capture.to_provenance()}

            if capture.disposition == "captured" and capture.content_sha256:
                if capture.content_sha256 in self.content_hashes:
                    entry["disposition"] = "duplicate"
                    entry["reason"] = "identical content already captured (same sha256)"
                else:
                    self.content_hashes.add(capture.content_sha256)
                    self.page_count += 1
            self._record(entry)

            # ---- link discovery (depth ≤ max_depth) -------------------------
            if entry["disposition"].startswith(("captured", "direct_pdf")) is False:
                continue
            if node.depth >= self.cfg.max_depth:
                continue
            for child_url in self._discover_links(node.url):
                child_norm = normalize_url(child_url)
                if not child_norm or child_norm in seen:
                    continue
                seen.add(child_norm)
                queue.append(_Node(child_norm, parent=node.url, depth=node.depth + 1))

            # ---- safe dynamic-state capture (informational UI only) ---------
            if entry["disposition"] in {"captured_verified_normal", "captured_verified_browser"}:
                self._capture_dynamic_states(node.url)

        return self.manifest()

    # -- link discovery (§5) ------------------------------------------------------

    def _discover_links(self, url: str) -> list:
        """Same-domain links from anchors/buttons-that-navigate/documents."""
        links = self.renderer.discover_links(url)
        out = []
        for href in links:
            disposition, _ = classify_url(href, skip_boilerplate=self.cfg.skip_boilerplate)
            if disposition in {"ok", "pdf"}:
                out.append(href)
            else:
                # record why a discovered link was not followed (§4/§9)
                self._record({"url": href, "parent": url, "depth": "?",
                              "disposition": f"excluded_{disposition}" if disposition != "invalid" else "invalid_url",
                              "reason": "link discovery: URL-level policy"})
        return out

    # -- dynamic states (§6/§7) -----------------------------------------------------

    def _capture_dynamic_states(self, parent_url: str) -> None:
        # the renderer must be sitting on the parent page before we enumerate
        # interactions (verified_normal captures never navigated the browser)
        if hasattr(self.renderer, "ensure_on"):
            self.renderer.ensure_on(parent_url)
        candidates = self.renderer.list_interactables()
        activated = 0
        for item in candidates:
            if activated >= self.cfg.max_interactions_per_page:
                break
            verdict, reason = classify_element(item.tag, item.text, item.attributes,
                                               inside_form=item.inside_form)
            if verdict == UNSAFE:
                self._record({"url": parent_url, "disposition": "interaction_rejected",
                              "reason": f"safe-click policy: {reason}",
                              "trigger_text": item.text[:80]})
                continue
            if verdict == AMBIGUOUS:
                self._record({"url": parent_url, "disposition": "interaction_skipped_ambiguous",
                              "reason": f"TODO(Monirul): review interaction — {reason}",
                              "trigger_text": item.text[:80]})
                continue

            before = self.renderer.body_text()
            if not self.renderer.activate(item):
                self._record({"url": parent_url, "disposition": "interaction_failed",
                              "reason": "locator/click failed", "trigger_text": item.text[:80]})
                continue
            self.renderer.settle()
            after = self.renderer.body_text()
            activated += 1

            delta = self._new_content(before, after)
            if len(delta) < self.cfg.min_state_delta_chars:
                self._record({"url": parent_url, "disposition": "dynamic_state_no_new_content",
                              "reason": f"delta {len(delta)} chars < threshold",
                              "trigger_text": item.text[:80]})
                # reset to the base state before trying the next interaction
                self.renderer.goto(parent_url)
                continue

            state_raw = self.renderer.snapshot_state()
            digest = ""
            import hashlib
            if state_raw["text"]:
                digest = hashlib.sha256(state_raw["text"].encode("utf-8")).hexdigest()
            if digest and digest in self.content_hashes:
                self._record({"url": parent_url, "disposition": "duplicate",
                              "reason": "dynamic state identical to an existing capture",
                              "trigger_text": item.text[:80]})
            else:
                if digest:
                    self.content_hashes.add(digest)
                slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{parent_url}_{item.text or 'state'}").strip("_")[:60]
                text_path = _write_artifact(self.out_dir, f"state_{slug}.txt", state_raw["text"])
                html_path = _write_artifact(self.out_dir, f"state_{slug}.html", state_raw["html"])
                png_path = (_write_artifact(self.out_dir, f"state_{slug}.png", state_raw["png"])
                            if state_raw["png"] else "")
                self._record({
                    "url": parent_url,
                    "parent_page": parent_url,
                    "disposition": "js_dynamic_state",
                    "trigger_text": item.text[:80],
                    "trigger_locator": f"{item.tag}:has-text({item.text[:40]!r})",
                    "state_name": item.text[:40] or "state",
                    "state": {
                        "source_url": parent_url,
                        "state_name": item.text[:40] or "state",
                        "trigger_text": item.text[:80],
                        "parent_page": parent_url,
                        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "content_sha256": digest,
                        "chars": len(state_raw["text"]),
                        "artifacts": {"text": text_path, "html": html_path,
                                      "screenshot_png": png_path or None},
                        "js_required": True,
                        "interaction_required": True,
                        # the state was reached from a strictly-navigated page
                        # (certificate-validated), so its content is verified
                        "verification_status": "verified",
                    },
                })
            # reset to base state so the next interaction starts clean
            self._sleep()
            self.renderer.goto(parent_url)

    def _new_content(self, before: str, after: str) -> str:
        """Rough informational delta: what the new state adds over the base."""
        if len(after) <= len(before):
            return ""
        tail = after[len(before):] if after.startswith(before[:200]) else after
        return tail


    # -- manifest (§9) --------------------------------------------------------------

    def manifest(self) -> dict:
        counts: dict = {}
        for n in self.nodes:
            counts[n["disposition"]] = counts.get(n["disposition"], 0) + 1
        return {
            "crawl_config": {
                "seeds": self.cfg.seeds,
                "allowlist": sorted(self.cfg.allowlist),
                "max_depth": self.cfg.max_depth,
                "max_pages": self.cfg.max_pages,
                "user_agent": self.cfg.user_agent,
                "interval_seconds": self.cfg.interval_seconds,
            },
            "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "summary": {"nodes": len(self.nodes), "pages_captured": self.page_count,
                        "dispositions": counts},
            "nodes": self.nodes,
        }


def save_manifest(manifest: dict, path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
