# P01 Codex review

**Decision: changes requested. P02 remains unreleased.**

Reviewed commit: `b7289d79e431d77017eae249e226dc15a141334f`, based on accepted P00 `f808ce3c564d91544219abc8edcac96a58011f14`.

The dry-run baseline-read change is correct by inspection. Valid PDF fixtures and the real-parser integration improve the prior tests. Three acceptance gaps remain.

## C1 — Invalid final URLs are accepted or abort the run

Location: `freshness/known_monitor.py:249-251`.

The monitor calls same_domain without first validating final URL scheme/syntax. Independent probes through real `KnownSourceMonitor.run`, with injected transport/robots and actual valid PDF bytes, produced:

| Supplied final URL | Observed result |
|---|---|
| `file://gov.example/doc.pdf` | New trusted baseline created; suspect count0 |
| `https://[invalid/doc.pdf` | Uncaught ValueError: Invalid IPv6 URL |

P01 explicitly requires rejection of nonempty invalid final URLs. Validate HTTP(S) scheme, host and port, and turn expected parsing errors into source diagnostics so later valid sources can still run. This is post-capture validation; no claim of actual off-policy network contact was made by these injected probes.

## C2 — Accessible encrypted PDFs are accepted

Location: `acquisition/document_validation.py:57-68`.

Successful pdfplumber opening/page enumeration does not prove that a document is unencrypted. The review generated a genuinely encrypted one-page PDF with an empty user password. The project's existing parser reports one page and populated `pdf.doc.encryption`; `validate_pdf` returns `(True, '')`.

The original P01 prompt requires encrypted/inaccessible documents to be rejected. Explicitly check encryption metadata rather than relying only on parser exceptions. The existing test alters a PDF's encryption/trailer structure; its failure is not sufficient coverage of a valid encrypted document that can be opened.

Reviewer fixture: `p01_empty_password_encrypted.pdf` in the evidence directory below. SHA-256: `9f2cea19ab245c5307614bf627ffff3caedcb62ee993132fbc9a2978f3fcc69e`. It was generated with the already bundled document runtime outside the project; no package was installed and no runtime project dependency was added. The corrected worker test can commit these fixture bytes and use the existing pdfplumber parser.

## C3 — Trusted snapshot preservation assertion is self-referential

Location: `tests/maintenance/test_p01_monitor.py:124-127`.

Both sides read the same stored snapshot after the real run. The fallback also allows missing content to compare equal. Independent injection overwrote the temporary trusted snapshot with `CORRUPTED AFTER REAL COLLAPSE` after the real monitor returned; the actual preservation test still passed.

Freeze expected path/hash/bytes before the run and compare against those values afterward. Require the file to exist. Check rejected-PDF artifacts regardless of extension, because candidate snapshot names are not guaranteed to end in `.pdf`.

## Review evidence and next step

Independently reproduced the exported final commit's cumulative run: 44 unittest tests, zero top-level failures/errors/skips and zero recorded network attempts; legacy60/31/63 plus multiturn pass; exit0. This used the same external Windows temporary/tar-directory permission workaround documented in previous reviews. The tested source/assertions were unchanged; the result is qualified by that review-environment adjustment. Green existing tests do not cover the negative cases above.

External evidence directory:
`C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/p00_review_20260905/`

Files: `p01_final_url_probes.json`, `p01_empty_password_encrypted.pdf`, `p01_empty_password_encrypted.json`, `p01_snapshot_mutation.json`.

All probes used temporary fixture state and injected capture boundaries. The corruption injection changed only the temporary test snapshot. No worker source or original project data was changed for these probes.

Use the [P01-C1 corrective prompt](../plans/2026-09-06-glm-p01-correction-prompt.md). It groups all three findings in one bounded correction, including the genuine encrypted fixture. GLM must commit locally and stop; Codex will review the final corrective commit before releasing P02.
