"""
freshness/known_monitor.py — source-level known-source drift monitoring
(Freshness V2 §2/§4/§5/§6/§7/§8/§13/§14).

Flow per run:
  1. build the normalized-URL → records mapping from the current corpora
  2. deduplicate: each unique source is acquired EXACTLY ONCE, result fanned
     out to all impacted records (§4); rate limiting is per acquisition
  3. acquire through the shared acquisition layer hierarchy (§5):
       certificate-validated rendered browser text (canonical for HTML)
       → strict verified static HTTP text only when the browser cannot render
       → capture-only invalid-TLS evidence (NEVER trusted)
       verified original bytes for PDFs
  4. run the capture-quality quarantine gate (§7) before anything is trusted
  5. apply state transitions with trusted/candidate separation (§8):
       first observation   → baseline, no event
       unchanged           → refresh metadata, clear stale failure state (§13)
       changed             → candidate stored, ONE source-level event;
                             repeated identical candidates never duplicate
       method transition   → same content via a different verified method is
                             NOT content drift (§5)
       failure/suspect     → trusted baseline preserved, status recorded
  6. dry run: identical computation, ZERO persistent writes (§14)

Dependency-injected transport/renderer make the whole machine hermetic.
"""
from __future__ import annotations

from datetime import datetime, timezone

from acquisition.acquire import extract_text_from_html
from acquisition.transport import RequestsTransport  # noqa: F401 (default factory)
from acquisition.urls import normalize_url

from . import source_store as S
from .mapping import build_source_mapping
from .paths import atomic_read_json, atomic_write_json, sha256_bytes, sha256_text
from .quality import classify_capture

DEFAULT_UA = "FreshnessChecker"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KnownSourceMonitor:
    def __init__(self, *, corpus_paths: dict, transport, renderer,
                 store_path, snapshot_dir, events_path,
                 rate_limit_seconds: float = 1.0, dry_run: bool = False,
                 user_agent: str = DEFAULT_UA, now_fn=None,
                 record_ids: set[str] | None = None):
        self.corpus_paths = corpus_paths
        self.transport = transport
        self.renderer = renderer
        self.store_path = store_path
        self.snapshot_dir = snapshot_dir
        self.events_path = events_path
        self.rate_limit_seconds = rate_limit_seconds
        self.dry_run = dry_run
        self.user_agent = user_agent
        self._now = now_fn or _now
        self.record_ids = record_ids
        self._first_fetch = True

    # -- persistence helpers (dry-run aware) ---------------------------------

    def _save_store(self, store):
        if not self.dry_run:
            S.save_store(store, self.store_path)

    def _save_events(self, events):
        if not self.dry_run:
            atomic_write_json(self.events_path, events)

    def _store_or_memory(self, store):
        """In dry-run, mutations happen on an in-memory copy only."""
        return store

    def _entry_snapshot_write(self, entry, text, kind):
        """Write a snapshot file unless in dry-run; return the path-or-note."""
        if self.dry_run:
            return f"(dry-run: {kind} snapshot not persisted)"
        from .source_store import snapshot_path_for
        from .paths import atomic_write_text
        p = snapshot_path_for(entry["normalized_url"], kind)
        atomic_write_text(p, text)
        return str(p)

    # -- acquisition ----------------------------------------------------------

    def _acquire(self, url: str, is_pdf: bool, entry):
        """Acquire one source through the shared hierarchy.
        Returns (quality, method, verification, text_or_bytes, reason)."""
        import time
        if not self._first_fetch:
            time.sleep(self.rate_limit_seconds)
        self._first_fetch = False

        if is_pdf:
            strict = self.transport.fetch(url)
            if strict.ok:
                return ("ok", S.METHOD_PDF, "verified", strict.body,
                        f"pdf:{strict.content_type}")
            if strict.error_kind == "tls":
                return ("failed", None, "unreachable", None,
                        f"tls: {strict.error_detail[:120]}")
            status = S.STATUS_BLOCKED if (strict.status in (401, 403, 429, 503)) else S.STATUS_FAILED
            return ("failed", None, "unreachable", None,
                    f"{status}: {strict.error_detail[:120]}")

        render = self.renderer.goto(url, ignore_tls=False)
        if render.ok:
            return ("ok", S.METHOD_BROWSER, "verified", render.text, "")
        if render.error_kind == "tls":
            # evidence-context render: diagnostics only, NEVER trusted (§5/§7)
            ev = self.renderer.goto(url, ignore_tls=True)
            reason = f"tls: {render.error_detail[:120]}"
            if ev.ok:
                return ("capture_only", None, "capture_only", ev.text, reason)
            return ("failed", None, "unreachable", None, reason + " (evidence render also failed)")
        # browser could not render (blocked / http error / navigation) —
        # try the strict static fallback before declaring failure (§5 item 2)
        strict = self.transport.fetch(url)
        if strict.ok and not url.lower().endswith(".pdf"):
            ctype = (strict.content_type or "").lower()
            if "html" in ctype or ctype == "":
                text = extract_text_from_html(strict.body.decode("utf-8", errors="replace"))
                return ("ok", S.METHOD_STATIC, "verified", text,
                        f"browser failed ({render.error_kind}), static fallback used")
        blocked = render.status in (401, 403, 429, 503) or render.error_kind == "blocked"
        status = S.STATUS_BLOCKED if blocked else S.STATUS_FAILED
        return ("failed", None, "unreachable", None,
                f"{status}: {render.error_kind} {render.error_detail[:100]}")

    # -- main run ---------------------------------------------------------------

    def run(self) -> dict:
        import time

        mapping = build_source_mapping(self.corpus_paths, record_ids=self.record_ids)
        store = S.load_store(self.store_path)
        events = atomic_read_json(self.events_path, default=[]) or []

        summary = {
            "records_mapped": sum(len(v["records"]) for v in mapping.values()),
            "unique_sources": len([u for u in mapping if u]),
            "sources_fetched": 0,
            "new_baselines": 0, "unchanged": 0, "changed_pending": 0,
            "suspect_captures": 0, "blocked_or_robots": 0, "capture_only": 0,
            "failed_or_unreachable": 0, "method_transitions": 0,
            "no_source_url_records": len(mapping.get("", {}).get("records", [])) if "" in mapping else 0,
        }

        for norm_url, entry_info in sorted(mapping.items()):
            if not norm_url:
                continue  # malformed/non-http: accounted in summary, never fetched
            entry = S.get_entry(store, norm_url, entry_info["canonical_url"])
            if entry.get("first_seen") is None:
                entry["first_seen"] = self._now()
            entry["last_checked"] = self._now()

            # §6: no permanent skip list — historical block info stays metadata.
            # Explicit robots restrictions remain authoritative and are checked
            # per run by the acquisition layer inside the real implementation.
            # (The injected renderer/transport stand in for it in tests.)

            quality, method, verification, payload, reason = self._acquire(
                norm_url, entry_info["is_pdf"], entry)
            summary["sources_fetched"] += 1

            if quality == "capture_only":
                S.mark_failure(entry, S.STATUS_CAPTURE_ONLY, reason)
                summary["capture_only"] += 1
                self._save_store(self._store_or_memory(store))
                continue

            if quality == "failed":
                blocked = reason.startswith(S.STATUS_BLOCKED)
                S.mark_failure(entry, S.STATUS_BLOCKED if blocked else S.STATUS_FAILED, reason)
                if blocked:
                    summary["blocked_or_robots"] += 1
                else:
                    summary["failed_or_unreachable"] += 1
                self._save_store(self._store_or_memory(store))
                continue

            # PDF bytes vs HTML text
            if method == S.METHOD_PDF:
                new_hash = sha256_bytes(payload)
                new_len = len(payload)
                quality_code, q_reason = ("ok", "")
                baseline_text = None
            else:
                new_hash = sha256_text(payload)
                new_len = len(payload)
                baseline_text = None
                if entry.get("trusted_snapshot") and not self.dry_run:
                    from .paths import atomic_read_text
                    baseline_text = atomic_read_text(entry["trusted_snapshot"])
                quality_code, q_reason = classify_capture(payload, baseline_text=baseline_text)

            if quality_code != "ok":
                # §7: quarantine — preserve trusted baseline, keep diagnostics
                S.mark_failure(entry, S.STATUS_SUSPECT,
                               f"{quality_code}: {q_reason}")
                summary["suspect_captures"] += 1
                self._save_store(self._store_or_memory(store))
                continue

            entry["content_type"] = reason if method == S.METHOD_PDF else entry.get("content_type")
            entry["content_length"] = new_len

            if entry.get("trusted_hash") is None:
                # first observation = baseline, not a change (§ preserved rule)
                if not self.dry_run:
                    S.save_trusted_baseline(entry, payload, method,
                                            content_type=(reason if method == S.METHOD_PDF
                                                          else "text/html"),
                                            content_hash=new_hash,
                                            snapshot_dir=self.snapshot_dir)
                else:
                    entry["trusted_hash"] = new_hash   # in-memory only
                    entry["trusted_acquisition_method"] = method
                summary["new_baselines"] += 1
                self._save_store(self._store_or_memory(store))
                continue

            if new_hash == entry["trusted_hash"]:
                # unchanged (possibly a recovery after failure) — §13
                S.refresh_unchanged(entry, method, verification,
                                    canonical_url=entry_info["canonical_url"])
                summary["unchanged"] += 1
                self._save_store(self._store_or_memory(store))
                continue

            # content differs from trusted — candidate/trusted separation (§8)
            if entry.get("candidate_hash") == new_hash:
                # same pending candidate: update last-seen metadata only
                entry["candidate_acquisition_method"] = method
                self._save_store(self._store_or_memory(store))
                continue
            if entry.get("candidate_invalid") and entry.get("candidate_hash") == new_hash:
                # already ruled invalid — do not re-alert (§9)
                continue

            method_transition = (entry.get("trusted_acquisition_method") != method
                                 and entry.get("trusted_acquisition_method") is not None)
            if not self.dry_run:
                snap_path = str(S.save_candidate(entry, payload, method,
                                                 snapshot_dir=self.snapshot_dir))
            else:
                S.save_candidate  # no-op in dry run; keep hashes in memory
                entry["candidate_hash"] = new_hash
                entry["candidate_acquisition_method"] = method
                snap_path = "(dry-run: candidate snapshot not persisted)"
            entry["candidate_length"] = new_len
            event_type = "method_transition" if method_transition else "content_change"
            if method_transition:
                summary["method_transitions"] += 1
            else:
                summary["changed_pending"] += 1
            events = [e for e in events
                      if not (e["normalized_url"] == norm_url and e["status"] == "pending")]
            events.append({
                "event_id": f"ev_{sha256_text(norm_url)[:8]}_{new_hash[:8]}",
                "type": event_type,
                "normalized_url": norm_url,
                "canonical_url": entry_info["canonical_url"],
                "detected_at": self._now(),
                "last_seen": self._now(),
                "trusted_hash": entry["trusted_hash"],
                "candidate_hash": new_hash,
                "candidate_snapshot": snap_path,
                "trusted_snapshot": entry["trusted_snapshot"],
                "acquisition_method": method,
                "trusted_method": entry.get("trusted_acquisition_method"),
                "quality": "ok",
                "status": "pending",
                "impacted_records": entry_info["records"],
            })
            self._save_store(self._store_or_memory(store))
            self._save_events(events)

        if not self.dry_run:
            S.save_store(store, self.store_path)
            atomic_write_json(self.events_path, events)
        self.summary = summary
        return summary
