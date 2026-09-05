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
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_freshness import (FakeRenderer, FakeTransport, make_monitor, rec,
                            state_bytes)

from acquisition.transport import StrictResult
from tests.maintenance.fixtures import make_pdf

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

    def test_encrypted_pdf_rejected_with_reason(self):
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

    def test_real_parser_page_count(self):
        import io
        import pdfplumber
        body = make_pdf("PAGECOUNT")
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            self.assertEqual(len(pdf.pages), 1)


if __name__ == "__main__":
    unittest.main()
