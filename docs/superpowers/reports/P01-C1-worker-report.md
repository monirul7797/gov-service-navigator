# P01-C1 worker report — reject invalid destinations and encrypted PDFs

Packet: P01-C1 (corrections to P01)
Base commit: `b7289d79e431d77017eae249e226dc15a141334f` on `codex/tracker-g0-p01`, same worktree; untracked docs and user changes preserved; P01 not amended.
Final local commit SHA: reported in the dispatch response after commit and exported verification.

## Changed files

| File | Change | Correction |
|---|---|---|
| `freshness/known_monitor.py` | new `_final_url_rejection()` — validates a nonempty final URL's syntax, scheme (http/https only), hostname and port (ValueError-safe) BEFORE any domain comparison, parsing or acceptance; malformed values become rejected source outcomes (`invalid_final_url` suspect captures), never run-aborting exceptions and never silent fallbacks to the requested URL; empty final_url still falls back to the already-validated requested URL (old fakes) | C1 |
| `acquisition/document_validation.py` | inside the parser context, encryption metadata (`pdf.doc.encryption`) is inspected explicitly before acceptance — a genuinely encrypted PDF that opens and parses fine is rejected with an `encrypted_pdf` reason; password-protected/inaccessible files still fail parsing first (both are rejections) | C2 |
| `tests/maintenance/test_p01_monitor.py` | C1 regressions (file:// scheme, malformed IPv6, malformed port, malformed-source-then-valid-source in one run, baseline+candidate survival, dry-run non-mutation), C2 regressions (helper + monitor rejection of the genuine empty-password encrypted fixture), C3 rewrite (frozen pre-run path/hash/bytes preservation, no fallback expression, sequential dry→real collapse from one unchanged baseline with complete state-byte equality, all-extension artifact checks, frozen-event comparison, corruption/deletion demonstration) | C1, C2, C3 |
| `tests/fixtures/p01/encrypted-empty-password.pdf` | reviewer-supplied synthetic fixture, committed for self-contained candidate exports | C2 |
| `docs/superpowers/reports/P01-C1-worker-report.md` | this report | — |

No harness tools, P00 regression files, production transport/robots/URL-policy modules, legacy assertion counts, corpora or vector data changed.

## Fixture evidence

`tests/fixtures/p01/encrypted-empty-password.pdf` — SHA-256 `9f2cea19ab245c5307614bf627ffff3caedcb62ee993132fbc9a2978f3fcc69e` (matches the reviewer value exactly, verified before and after copy; 828 bytes). Real-parser proof: `pdfplumber.open` succeeds with **1 accessible page** and **`pdf.doc.encryption` populated** (tuple) — successful opening ≠ unencrypted.

## RED → GREEN

- C1 RED: `file://gov.example/doc.pdf` created a trusted PDF baseline; `https://[invalid/doc.pdf` raised ValueError aborting the run; malformed port accepted; malformed source aborted the run leaving the valid source unprocessed; baseline/candidate overwritten by a file:// capture; dry run aborted. GREEN: all six now reject with `invalid_final_url` diagnostics, the valid source in the same run is processed, existing baseline/candidate bytes and hashes survive, dry mode writes nothing.
- C2 RED: `validate_pdf` returned `(True, '')` for the genuine encrypted fixture and the monitor created a trusted baseline. GREEN: helper returns `(False, 'encrypted_pdf: …')`; monitor records a suspect capture with no trusted/candidate content; the old encryption-shaped sample is retained and accurately relabeled as malformed-content coverage (`parse_failure`).
- C3 RED: the old preservation assertion used a self-referencing fallback expression that made missing data compare equal and passed even with a corrupted snapshot. GREEN: frozen pre-run path/hash/bytes asserted after the run (path, existence, byte equality, hash); sequential dry→real collapse from one unchanged baseline with complete store/event/snapshot/corpus byte equality in dry mode; rejected-PDF tests assert no NEW snapshot artifacts of ANY extension and compare trusted/candidate bytes and event content against frozen values while permitting only documented diagnostic changes; the corruption/deletion demonstration (temp fixture only) proves the corrected comparison fails when the snapshot is damaged.

## Verification (audited interpreter 3.12.10)

- `python -B tools/run_offline_checks.py --pattern 'test_p01_*.py'` → exit 0, ran 33, skips 0, attempts 0.
- `python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'` → exit 0; legacy [60, 31, 63] + multiturn; discovery ran 57 (24 P00 + 33 P01), failures 0, errors 0, skips 0, attempts 0.
- `git diff --check` → exit 0.
- Exported: `python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'` → exit 0 on the final commit (binary fixture included in the archive).

## Limitations

- C1 remains a post-capture check: the external request itself is still not prevented (P02/P03 scope).
- The C1 malformed-value class covers parse errors, unsupported schemes, missing hosts and invalid ports; other malformed forms that urlparse tolerates are handled by the domain rule or upstream policy.
- Password-protected PDFs that fail parsing report `parse_failure` rather than `encrypted_pdf`; both are rejections.

STOPPED; next packet not started.
