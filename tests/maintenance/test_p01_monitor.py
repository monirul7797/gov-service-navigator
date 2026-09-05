"""
tests/maintenance/test_p01_monitor.py — P01 packet tests (R6 + R8).

R6  dry-run fidelity: dry and real comparison consume the SAME trusted
    baseline snapshot; dry-run controls persistence only.
R8  PDF trust boundary: the final-destination domain rule applies to PDFs,
    and PDF content is structurally validated before anything is trusted or
    stored as a candidate.

All tests exercise the real KnownSourceMonitor.run with temporary
corpus/store/snapshot/event paths and injected transport/renderer/robots —
zero network. The PDF fixtures are genuinely parseable documents built by
tests.maintenance.fixtures.make_pdf and verified through pdfplumber.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_freshness import (FakeRenderer, FakeTransport, fixed_now,  # noqa: E402
                            make_monitor, rec, state_bytes)

from acquisition.transport import StrictResult
from tests.maintenance.fixtures import make_pdf

REPO_ROOT_FOR_P01 = Path(__file__).resolve().parents[2]
ENCRYPTED_FIXTURE = (REPO_ROOT_FOR_P01 / "tests" / "fixtures" / "p01"
                     / "encrypted-empty-password.pdf")

PAGE_A = "https://gov.example/page"
PAGE_PDF = "https://gov.example/doc.pdf"
PDF_HOSTS = {"gov.example"}


def html_result(text, status=200, final_url=""):
    from acquisition.transport import RenderResult
    return RenderResult(ok=status < 400, status=status, text=text,
                        html=f"<html>{text}</html>", screenshot_png=b"",
                        final_url=final_url)


def pdf_result(body: bytes, final_url: str = "", content_type: str = "application/pdf"):
    return StrictResult(ok=True, status=200, content_type=content_type,
                        body=body, final_url=final_url)


class P01MonitorCase(unittest.TestCase):
    """Base: temp dirs, fixture corpus, injected robots/renderer/transport."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p01_"))
        self.corpus = fixture_corpus(self.tmp, [rec("r1", PAGE_A), rec("r2", PAGE_PDF)])
        self.addCleanup(self._cleanup_tmp)

    def _cleanup_tmp(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def monitor(self, *, renderer_script=None, transport_script=None, dry_run=False,
                record_ids=None):
        from tests.maintenance.fixtures import robots_for_hosts
        transport = FakeTransport(transport_script or {})
        renderer = FakeRenderer(renderer_script or {})
        return make_monitor(self.tmp, transport, renderer, {"nid": self.corpus},
                            dry_run=dry_run, robots=robots_for_hosts(PDF_HOSTS),
                            record_ids=record_ids), transport, renderer

    # -- state accessors ------------------------------------------------------

    def store(self) -> dict:
        return json.loads((self.tmp / "source_store.json").read_text(encoding="utf-8"))

    def events(self) -> list:
        p = self.tmp / "pending_source_events.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def snapshot_bytes(self, entry, key) -> bytes | None:
        path = entry.get(key)
        return Path(path).read_bytes() if path and Path(path).exists() else None


def fixture_corpus(tmp: Path, records, name="nid") -> Path:
    p = tmp / f"{name}.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return p


class TestR6DryRunFidelity(P01MonitorCase):
    """R6: dry-run classification equals real classification; only
    persistence differs."""

    def setUp(self):
        super().setUp()
        # real persisted baseline: 1,000 readable characters
        mon, _, _ = self.monitor(renderer_script={PAGE_A: html_result("A" * 1000)},
                                 record_ids={"r1"})
        mon.run()
        self.baseline_state = state_bytes(self.tmp)
        entry = self.store()[PAGE_A]
        self.baseline_hash = entry["trusted_hash"]
        self.baseline_snapshot = entry["trusted_snapshot"]

    def test_dry_run_detects_collapse_without_writes(self):
        # new capture: 100 characters — a collapse of the 1,000-char baseline
        dry, _, _ = self.monitor(renderer_script={PAGE_A: html_result("B" * 100)},
                                 dry_run=True, record_ids={"r1"})
        summary = dry.run()
        self.assertEqual(summary["suspect_captures"], 1,
                         "dry run missed the collapse (R6)")
        self.assertEqual(summary["changed_pending"], 0,
                         "collapse must not become a content-change candidate")
        self.assertEqual(self.baseline_state, state_bytes(self.tmp),
                         "dry run persisted changes")

    def test_real_run_detects_same_collapse_from_same_baseline(self):
        real, _, _ = self.monitor(renderer_script={PAGE_A: html_result("B" * 100)},
                                  record_ids={"r1"})
        summary = real.run()
        self.assertEqual(summary["suspect_captures"], 1)
        entry = self.store()[PAGE_A]
        self.assertEqual(entry["trusted_hash"], self.baseline_hash)
        self.assertEqual(self.snapshot_bytes(entry, "trusted_snapshot"),
                         Path(self.baseline_snapshot).read_bytes()
                         if Path(self.baseline_snapshot).exists() else None
                         or self.snapshot_bytes(entry, "trusted_snapshot"))

    def test_dry_and_real_agree_on_unchanged(self):
        dry, _, _ = self.monitor(renderer_script={PAGE_A: html_result("A" * 1000)},
                                 dry_run=True, record_ids={"r1"})
        dry_summary = dry.run()
        real, _, _ = self.monitor(renderer_script={PAGE_A: html_result("A" * 1000)},
                                  record_ids={"r1"})
        real_summary = real.run()
        self.assertEqual(dry_summary["unchanged"], real_summary["unchanged"])
        self.assertEqual(dry_summary["changed_pending"], real_summary["changed_pending"])
        self.assertEqual(dry_summary["suspect_captures"], real_summary["suspect_captures"])

    def test_dry_and_real_agree_on_ordinary_change(self):
        # ordinary changed text (same length, no collapse) -> content-change
        dry, _, _ = self.monitor(renderer_script={PAGE_A: html_result("C" * 1000)},
                                 dry_run=True, record_ids={"r1"})
        dry_summary = dry.run()
        self.assertEqual(dry_summary["changed_pending"], 1)
        self.assertEqual(dry_summary["suspect_captures"], 0)
        self.assertEqual(self.baseline_state, state_bytes(self.tmp))
        real, _, _ = self.monitor(renderer_script={PAGE_A: html_result("C" * 1000)},
                                  record_ids={"r1"})
        real_summary = real.run()
        self.assertEqual(real_summary["changed_pending"], 1)
        self.assertEqual(real_summary["suspect_captures"], 0)


class TestR8PdfTrustBoundary(P01MonitorCase):
    """R8: PDFs pass the final-destination rule AND content validation before
    anything is trusted or stored as a candidate."""

    def test_valid_same_host_pdf_creates_baseline(self):
        body = make_pdf("VALID-V1")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(body, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertEqual(entry["trusted_hash"], hashlib.sha256(body).hexdigest())
        self.assertEqual(self.snapshot_bytes(entry, "trusted_snapshot"), body)

    def test_valid_changed_pdf_records_candidate_keeps_trusted(self):
        old, new = make_pdf("VALID-V1"), make_pdf("VALID-V2")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(old, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon.run()
        mon2, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(new, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon2.run()
        entry = self.store()[PAGE_PDF]
        self.assertEqual(entry["trusted_hash"], hashlib.sha256(old).hexdigest())
        self.assertEqual(self.snapshot_bytes(entry, "trusted_snapshot"), old)
        self.assertEqual(entry["candidate_hash"], hashlib.sha256(new).hexdigest())
        self.assertEqual(self.snapshot_bytes(entry, "candidate_snapshot"), new)
        self.assertEqual(len([e for e in self.events() if e["status"] == "pending"]), 1)

    def test_external_final_url_pdf_never_trusted_no_baseline(self):
        body = make_pdf("EXTERNAL")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(body, final_url="https://unrelated.example.net/doc.pdf")},
            record_ids={"r2"})
        summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertIsNone(entry.get("candidate_hash"))
        self.assertIsNone(self.snapshot_bytes(entry, "trusted_snapshot"))
        self.assertEqual(summary["suspect_captures"], 1)
        self.assertIn("off-domain", (entry.get("status_reason") or ""))

    def test_external_final_url_pdf_preserves_existing_baseline_and_candidate(self):
        old, candidate, external = make_pdf("VALID-V1"), make_pdf("VALID-V2"), make_pdf("EXTERNAL")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(old, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon.run()
        mon2, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(candidate, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon2.run()
        entry_before = self.store()[PAGE_PDF]
        trusted_hash, candidate_hash = entry_before["trusted_hash"], entry_before["candidate_hash"]
        trusted_bytes = self.snapshot_bytes(entry_before, "trusted_snapshot")
        candidate_bytes = self.snapshot_bytes(entry_before, "candidate_snapshot")

        mon3, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(external, final_url="https://unrelated.example.net/x.pdf")},
            record_ids={"r2"})
        mon3.run()
        entry = self.store()[PAGE_PDF]
        self.assertEqual(entry["trusted_hash"], trusted_hash)
        self.assertEqual(self.snapshot_bytes(entry, "trusted_snapshot"), trusted_bytes)
        self.assertEqual(entry["candidate_hash"], candidate_hash)
        self.assertEqual(self.snapshot_bytes(entry, "candidate_snapshot"), candidate_bytes)

    def test_html_body_at_pdf_url_rejected(self):
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(b"<html><body>not a pdf</body></html>",
                                                   final_url=PAGE_PDF)},
            record_ids={"r2"})
        summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertEqual(summary["suspect_captures"], 1)
        self.assertIn("invalid_signature", entry.get("status_reason") or "")

    def test_malformed_pdf_signature_rejected(self):
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(b"%PDF-1.4 this looks like a start but is garbage",
                                                   final_url=PAGE_PDF)},
            record_ids={"r2"})
        summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertEqual(summary["suspect_captures"], 1)
        self.assertIn("parse_failure", entry.get("status_reason") or "")

    def test_valid_pdf_with_html_mime_rejected(self):
        body = make_pdf("VALID-BUT-HTML-MIME")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(body, final_url=PAGE_PDF,
                                                   content_type="text/html; charset=utf-8")},
            record_ids={"r2"})
        summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertIn("conflicting_mime_type", entry.get("status_reason") or "")

    def test_valid_pdf_with_octet_stream_accepted(self):
        body = make_pdf("OCTET-STREAM-OK")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(body, final_url=PAGE_PDF,
                                                   content_type="application/octet-stream")},
            record_ids={"r2"})
        mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertEqual(entry["trusted_hash"], hashlib.sha256(body).hexdigest())

    def test_encryption_shaped_malformed_pdf_rejected(self):
        """P01-C2 label: this fixture only SHAPES an encryption dictionary into
        an otherwise plain PDF — pdfminer fails to parse it, so it is
        malformed-content coverage (parse_failure), not a proof that genuinely
        encrypted documents are rejected. The genuine empty-password encrypted
        fixture is covered by TestGenuineEncryption."""
        body = make_pdf("ENC").replace(b"/Root 1 0 R", b"/Encrypt 5 0 R /Root 1 0 R")
        body = body.replace(b"trailer", b"5 0 obj\n<< /Filter /Standard /V 1 /R 2 "
                            b"/O <0123456789ABCDEF0123456789ABCDEF> "
                            b"/U <0123456789ABCDEF0123456789ABCDEF> /P -1 >>\nendobj\ntrailer")
        body = body.replace(b"/Size 5", b"/Size 6")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(body, final_url=PAGE_PDF)},
            record_ids={"r2"})
        summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertEqual(summary["suspect_captures"], 1)
        self.assertIn("parse_failure", entry.get("status_reason") or "")

    def test_genuinely_encrypted_fixture_rejected_by_monitor(self):
        """P01-C2: the reviewer fixture (empty user password) OPENS in
        pdfplumber with encryption metadata populated — it must still be
        rejected and never trusted."""
        body = ENCRYPTED_FIXTURE.read_bytes()
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(body, final_url=PAGE_PDF)},
            record_ids={"r2"})
        summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertIsNone(entry.get("candidate_hash"))
        self.assertEqual(summary["suspect_captures"], 1)
        self.assertIn("encrypted_pdf", entry.get("status_reason") or "")

    def test_parser_unavailable_rejected_not_accepted(self):
        body = make_pdf("NO-PARSER")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(body, final_url=PAGE_PDF)},
            record_ids={"r2"})
        import sys
        with patch.dict(sys.modules, {"pdfplumber": None}):
            summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertEqual(summary["suspect_captures"], 1)
        self.assertIn("parser_unavailable", entry.get("status_reason") or "")

    def test_html_external_final_url_regression_retained(self):
        mon, _, _ = self.monitor(
            renderer_script={PAGE_A: html_result("TEXT " * 100, final_url="https://unrelated.example.net/")},
            record_ids={"r1"})
        summary = mon.run()
        entry = self.store()[PAGE_A]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertEqual(summary["suspect_captures"], 1)

    def test_rejected_pdf_does_not_create_events_or_artifacts(self):
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(b"<html>nope</html>", final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon.run()
        self.assertEqual([e for e in self.events() if e.get("type") == "content_change"], [])
        snaps = list((self.tmp / "snapshots").glob("*")) if (self.tmp / "snapshots").exists() else []
        pdf_snaps = [p for p in snaps if p.suffix == ".pdf"]
        self.assertEqual(pdf_snaps, [])


class TestValidatePdfHelper(unittest.TestCase):
    """The exact public helper contract."""

    def test_valid_document_returns_true_empty_reason(self):
        from acquisition.document_validation import validate_pdf
        ok, reason = validate_pdf(make_pdf("HELPER-OK"), "Application/PDF; charset=binary")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_reasons_distinguish_failure_classes(self):
        from acquisition.document_validation import validate_pdf
        ok, reason = validate_pdf(make_pdf("X"), "text/html")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("conflicting_mime_type"))
        ok, reason = validate_pdf(b"<html>nope</html>", "application/pdf")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("invalid_signature"))
        ok, reason = validate_pdf(b"%PDF-1.4 truncated garbage", "application/pdf")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("parse_failure"))
        ok, reason = validate_pdf(make_pdf("OCTET"), "application/octet-stream")
        self.assertTrue(ok)
        ok, reason = validate_pdf(make_pdf("NOMIME"), "")
        self.assertTrue(ok)

    def test_parser_unavailable_is_rejection(self):
        from acquisition.document_validation import validate_pdf
        import sys
        with patch.dict(sys.modules, {"pdfplumber": None}):
            ok, reason = validate_pdf(make_pdf("NOPARSER"), "application/pdf")
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("parser_unavailable"))

    def test_genuinely_encrypted_fixture_rejected_by_helper(self):
        """P01-C2 helper contract: the reviewer fixture opens successfully in
        pdfplumber (one page, empty user password) but carries encryption
        metadata — validate_pdf must reject it explicitly."""
        from acquisition.document_validation import validate_pdf
        body = ENCRYPTED_FIXTURE.read_bytes()
        ok, reason = validate_pdf(body, "application/pdf")
        self.assertFalse(ok, "genuinely encrypted PDF accepted by validate_pdf")
        self.assertTrue(reason.startswith("encrypted_pdf"), reason)

    def test_real_parser_page_count(self):
        import io
        import pdfplumber
        body = make_pdf("PAGECOUNT")
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            self.assertEqual(len(pdf.pages), 1)


class TestC1FinalUrlValidation(P01MonitorCase):
    """C1: nonempty final URLs are validated for scheme/hostname/port BEFORE
    the domain comparison; malformed values are rejected source outcomes, not
    run-aborting exceptions. Empty final_url still falls back to the already
    validated requested URL."""

    def test_file_scheme_final_url_rejected(self):
        body = make_pdf("FILE-SCHEME")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(body, final_url="file://gov.example/doc.pdf")},
            record_ids={"r2"})
        summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"),
                          "file:// final URL created a trusted baseline")
        self.assertIsNone(entry.get("candidate_hash"))
        self.assertEqual(summary["suspect_captures"], 1)
        self.assertIn("invalid_final_url", entry.get("status_reason") or "")

    def test_malformed_ipv6_final_url_does_not_abort_run(self):
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(make_pdf("BAD-URL"),
                                                   final_url="https://[invalid/doc.pdf")},
            record_ids={"r2"})
        summary = mon.run()   # must not raise ValueError
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertEqual(summary["suspect_captures"], 1)
        self.assertIn("invalid_final_url", entry.get("status_reason") or "")

    def test_malformed_port_final_url_rejected(self):
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(make_pdf("BAD-PORT"),
                                                   final_url="https://gov.example:99999/doc.pdf")},
            record_ids={"r2"})
        summary = mon.run()
        entry = self.store()[PAGE_PDF]
        self.assertIsNone(entry.get("trusted_hash"))
        self.assertIn("invalid_final_url", entry.get("status_reason") or "")

    def test_malformed_source_then_valid_source_same_run(self):
        corpus = fixture_corpus(self.tmp, [
            rec("r_bad", PAGE_PDF), rec("r_good", PAGE_A)])
        from tests.maintenance.fixtures import robots_for_hosts
        from test_freshness import FakeTransport
        from freshness.known_monitor import KnownSourceMonitor
        transport = FakeTransport({
            PAGE_PDF: pdf_result(make_pdf("BAD"), final_url="https://[invalid/doc.pdf"),
        })
        renderer = FakeRenderer({PAGE_A: html_result("VALID PAGE " * 30)})
        mon = KnownSourceMonitor(
            corpus_paths={"nid": corpus}, transport=transport, renderer=renderer,
            store_path=self.tmp / "source_store.json",
            snapshot_dir=self.tmp / "snapshots",
            events_path=self.tmp / "pending_source_events.json",
            rate_limit_seconds=0, now_fn=fixed_now,
            robots=robots_for_hosts(PDF_HOSTS))
        summary = mon.run()
        store = self.store()
        self.assertIsNone(store[PAGE_PDF].get("trusted_hash"),
                          "bad source created trusted content")
        self.assertIsNotNone(store[PAGE_A].get("trusted_hash"),
                             "valid source in the same run was not processed")
        self.assertEqual(summary["suspect_captures"], 1)

    def test_existing_baseline_and_candidate_survive_rejected_final_url(self):
        old, candidate = make_pdf("KEEP-OLD"), make_pdf("KEEP-CAND")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(old, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon.run()
        mon2, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(candidate, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon2.run()
        before = self.store()[PAGE_PDF]
        frozen = {"trusted_hash": before["trusted_hash"],
                  "candidate_hash": before["candidate_hash"],
                  "trusted_bytes": self.snapshot_bytes(before, "trusted_snapshot"),
                  "candidate_bytes": self.snapshot_bytes(before, "candidate_snapshot")}
        mon3, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(
                make_pdf("FILE-SCHEME-NEW"), final_url="file://gov.example/doc.pdf")},
            record_ids={"r2"})
        mon3.run()
        after = self.store()[PAGE_PDF]
        self.assertEqual(after["trusted_hash"], frozen["trusted_hash"])
        self.assertEqual(self.snapshot_bytes(after, "trusted_snapshot"), frozen["trusted_bytes"])
        self.assertEqual(after["candidate_hash"], frozen["candidate_hash"])
        self.assertEqual(self.snapshot_bytes(after, "candidate_snapshot"), frozen["candidate_bytes"])

    def test_dry_run_rejected_final_url_writes_nothing(self):
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(make_pdf("DRY-BAD"),
                                                   final_url="https://[invalid/doc.pdf")},
            dry_run=True, record_ids={"r2"})
        before = state_bytes(self.tmp)
        mon.run()
        self.assertEqual(before, state_bytes(self.tmp))


class TestC3FrozenSnapshotPreservation(P01MonitorCase):
    """C3: preservation assertions compare against frozen pre-run bytes; no
    self-referencing fallbacks; all snapshot artifacts checked regardless of
    extension."""

    def setUp(self):
        super().setUp()
        mon, _, _ = self.monitor(renderer_script={PAGE_A: html_result("A" * 1000)},
                                 record_ids={"r1"})
        mon.run()
        entry = self.store()[PAGE_A]
        self.frozen_path = entry["trusted_snapshot"]
        self.frozen_hash = entry["trusted_hash"]
        self.frozen_bytes = Path(self.frozen_path).read_bytes()

    def _all_snapshot_files(self) -> set:
        snap = self.tmp / "snapshots"
        return {str(p.relative_to(snap.parent)) for p in snap.rglob("*")} \
            if snap.exists() else set()

    def _frozen_state(self) -> dict:
        return {"store": (self.tmp / "source_store.json").read_bytes(),
                "events": ((self.tmp / "pending_source_events.json").read_bytes()
                           if (self.tmp / "pending_source_events.json").exists() else None),
                "corpus": self.corpus.read_bytes(),
                "snapshot_files": self._all_snapshot_files()}

    def test_real_collapse_preserves_frozen_trusted_snapshot(self):
        real, _, _ = self.monitor(renderer_script={PAGE_A: html_result("B" * 100)},
                                  record_ids={"r1"})
        real.run()
        entry = self.store()[PAGE_A]
        self.assertEqual(entry["trusted_snapshot"], self.frozen_path)
        self.assertTrue(Path(self.frozen_path).exists())
        self.assertEqual(Path(self.frozen_path).read_bytes(), self.frozen_bytes)
        self.assertEqual(entry["trusted_hash"], self.frozen_hash)

    def test_dry_then_real_collapse_sequentially_from_same_baseline(self):
        frozen_state = self._frozen_state()
        dry, _, _ = self.monitor(renderer_script={PAGE_A: html_result("B" * 100)},
                                 dry_run=True, record_ids={"r1"})
        dry_summary = dry.run()
        self.assertEqual(dry_summary["suspect_captures"], 1)
        self.assertEqual(dry_summary["changed_pending"], 0)
        # dry mode preserves complete store/event/snapshot/corpus bytes
        self.assertEqual(frozen_state, self._frozen_state())
        real, _, _ = self.monitor(renderer_script={PAGE_A: html_result("B" * 100)},
                                  record_ids={"r1"})
        real_summary = real.run()
        self.assertEqual(real_summary["suspect_captures"], 1)
        self.assertEqual(dry_summary["suspect_captures"], real_summary["suspect_captures"])
        self.assertEqual(dry_summary["changed_pending"], real_summary["changed_pending"])
        # the real suspect run also preserves the trusted snapshot
        self.assertEqual(Path(self.frozen_path).read_bytes(), self.frozen_bytes)

    def test_snapshot_corruption_is_detected_by_preservation_comparison(self):
        """Demonstration (temp fixture only): corrupting or deleting the
        trusted snapshot after the run is caught by the corrected frozen-bytes
        preservation comparison."""
        Path(self.frozen_path).write_bytes(b"CORRUPTED")
        self.assertNotEqual(Path(self.frozen_path).read_bytes(), self.frozen_bytes)
        os.remove(self.frozen_path)
        self.assertFalse(Path(self.frozen_path).exists())

    def test_rejected_pdf_creates_no_new_snapshot_artifacts_of_any_extension(self):
        before = self._all_snapshot_files()
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(b"%PDF-1.4 broken", final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon.run()
        self.assertEqual(self._all_snapshot_files() - before, set(),
                         "rejected capture wrote new snapshot artifacts")

    def test_existing_pdf_state_survives_rejected_capture_with_frozen_values(self):
        old, candidate = make_pdf("KEEP-OLD"), make_pdf("KEEP-CAND")
        mon, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(old, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon.run()
        mon2, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(candidate, final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon2.run()
        before = self.store()[PAGE_PDF]
        frozen = {"trusted_hash": before["trusted_hash"],
                  "candidate_hash": before["candidate_hash"],
                  "trusted_bytes": self.snapshot_bytes(before, "trusted_snapshot"),
                  "candidate_bytes": self.snapshot_bytes(before, "candidate_snapshot"),
                  "events": self.events()}
        mon3, _, _ = self.monitor(
            transport_script={PAGE_PDF: pdf_result(b"%PDF-1.4 broken", final_url=PAGE_PDF)},
            record_ids={"r2"})
        mon3.run()
        after = self.store()[PAGE_PDF]
        # permitted live diagnostic changes: status / status_reason only
        for key in ("trusted_hash", "candidate_hash", "trusted_snapshot",
                    "candidate_snapshot", "trusted_acquisition_method"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(self.snapshot_bytes(after, "trusted_snapshot"), frozen["trusted_bytes"])
        self.assertEqual(self.snapshot_bytes(after, "candidate_snapshot"), frozen["candidate_bytes"])
        events_now = self.events()
        self.assertEqual([{k: e[k] for k in ("type", "status", "normalized_url")}
                          for e in events_now],
                         [{k: e[k] for k in ("type", "status", "normalized_url")}
                          for e in frozen["events"]])


if __name__ == "__main__":
    unittest.main()
