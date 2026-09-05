# P01 worker report — dry-run fidelity and PDF trust boundary

Packet: P01 (G0)
Base commit: `f808ce3c564d91544219abc8edcac96a58011f14` (accepted P00) on branch `codex/tracker-g0-p01`, new isolated worktree created from the accepted SHA (procedure followed; accepted P00 worktree and original checkout untouched).
Final local commit SHA: reported in the dispatch response after commit and exported verification.

## Changes and behavior verified

| File | Change | Defect |
|---|---|---|
| `freshness/known_monitor.py` | trusted snapshot is now read for BOTH dry and real runs (the `and not self.dry_run` condition removed — R6); the final-destination rule applies to every successful verified content method including `METHOD_PDF`, with empty `final_url` falling back to the requested URL (R8 destination gate); PDF bytes are validated through `validate_pdf` before acceptance — rejected PDFs become suspect captures with no trusted/candidate/artifact/event writes (R8 content boundary) | R6, R8 |
| `acquisition/document_validation.py` | new exact public helper `validate_pdf(body, content_type) -> (bool, reason)`: MIME normalized (case/parameters), explicit conflicting HTML MIME rejected, `%PDF-` signature required, structural parse via the existing pdfplumber on an in-memory stream with ≥1 accessible page, encrypted/inaccessible documents surface as parse failures, missing parser = `parser_unavailable` rejection (never auto-accept/install), deterministic resource close, no network/rendering/artifacts | R8 |
| `tests/maintenance/fixtures.py` | `make_pdf(identifier)` — deterministic, genuinely parseable one-page PDF builder (computed xref); verified through the real parser (pages == 1; old/new documents distinct) | fixture |
| `tests/maintenance/test_p01_monitor.py` | new: 20 tests over the real `KnownSourceMonitor.run` covering the full P01 behavioral table (R6 collapse/unchanged/change dry-vs-real agreement with state-byte equality; PDF baseline/candidate/external-URL/html-body/malformed/html-mime/octet-stream/encrypted/parser-unavailable/external-HTML-regression/rejected-no-artifact) | both |
| `test_freshness.py` | the PDF drift test's fake `b'%PDF-old'`/`b'%PDF-new'` bytes replaced with real `make_pdf` documents; the existing single 13c check now also asserts trusted-snapshot bytes equal the ORIGINAL document and candidate bytes equal the NEW document via the actual stored entry paths (same-host `final_url` added to the injected transport results) | fixture |

Not changed: harness tools, P00 regression files, production transport/robots/URL policy, routing, metadata/embedding code, corpora, vectors.

## RED → GREEN evidence

RED (before fixes), same test file: 20 tests → 12 failing for the intended reasons:
- `test_dry_run_detects_collapse_without_writes` FAIL — dry run missed the 1,000→100 collapse (`suspect_captures 0 != 1`), while the real-run sibling passed (R6 exactly).
- 9 R8 monitor tests FAILED — the external-final-URL PDF was accepted as a trusted baseline/candidate, HTML-at-`.pdf`-URL and malformed `%PDF` bodies were accepted, html-MIME accepted, encrypted accepted, parser-unavailable accepted, and rejected PDFs created artifacts/events.
- 3 helper tests ERRORED (`acquisition.document_validation` missing).
- Two valid-same-host-PDF acceptance tests passed pre-fix as regression anchors.

GREEN after the fixes: all 20 P01 tests pass.

## Required table coverage

collapse dry+real detected with no content-change candidate ✓; dry-run state bytes byte-identical (store/events/snapshots) ✓; unchanged and ordinary-change dry/real count agreement ✓; valid same-host PDF baseline with hash == original bytes ✓; valid changed PDF → candidate while trusted bytes/hash unchanged (snapshot file bytes asserted) ✓; external-final-URL PDF with no baseline → no trusted hash/candidate/PDF artifact, diagnostic status ✓; external URL with existing baseline/candidate → both preserved byte-for-byte ✓; HTML body at allowed `.pdf` URL → `invalid_signature` rejection ✓; malformed `%PDF` bytes → `parse_failure` ✓; valid PDF with `text/html` MIME → `conflicting_mime_type` ✓; octet-stream accepted after full validation ✓; encrypted/`parser_unavailable` → rejected with reason, no promotion ✓; HTML external-URL regression retained ✓.

## Verification (Python 3.12.10, audited interpreter)

- `python -B tools/run_offline_checks.py --pattern 'test_p01_*.py'` → exit 0, ran 20, network attempts 0.
- `python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'` → exit 0; legacy self-reported [60, 31, 63] + multiturn pass; discovery ran 44 (9 P00 baseline + 15 P00-C1 + 20 P01), failures 0, errors 0, skips 0, attempted_network 0.
- `git diff --check` → exit 0.
- Exported: `python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'` → exit 0 on the final commit.

## Notes

- P00's C1 regression asserted the exact legacy count `[60]`; folding the new
  state-preservation assertions into the EXISTING 13c check keeps that count
  at 60 (no deleted or skipped checks — the check got strictly stronger), so
  no P00 regression file needed editing.
- P01 is a post-capture trust check: it does not prevent the external request
  itself (P02/P03 scope). R2–R5/R7/R9 remain out of scope.
- Dry-run R6 proof avoids copying state (stale absolute snapshot paths):
  baseline is established in-place, state bytes snapshotted, dry run asserted
  byte-identical, then real run proceeds from the same live baseline.

STOPPED; next packet not started.
