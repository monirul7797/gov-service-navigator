# Ready-to-paste GLM prompt — P01-C1 only

Copy the text below into the existing GLM implementation conversation.

---

You are GLM 5.3 Flash, the implementation worker. Codex reviewed P01 commit `b7289d79e431d77017eae249e226dc15a141334f` and requests three bounded corrections. **P01 is not accepted; P02 is not released.**

Continue in:
`C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p01-1788642519219644900/worktree`

Branch: `codex/tracker-g0-p01`. Expected starting HEAD: `b7289d79e431d77017eae249e226dc15a141334f`. Check HEAD/status first. Preserve untracked docs and user changes; do not amend P01, touch the P00 worktree or alter the original checkout.

## Allowed files

- Modify `freshness/known_monitor.py` for final-URL validation only.
- Modify `acquisition/document_validation.py` for explicit encrypted-document rejection only.
- Modify `tests/maintenance/test_p01_monitor.py` for the associated regressions and state-preservation assertions.
- Add `tests/fixtures/p01/encrypted-empty-password.pdf` from the supplied reviewer fixture below.
- Add `docs/superpowers/reports/P01-C1-worker-report.md`.

No harness changes, new runtime dependencies, transport/robots/shared-URL-policy changes, legacy assertion edits, routing, embedding or live state writes. Read the review report at the original project's `docs/superpowers/reports/2026-09-06-P01-codex-review.md`.

## C1 — Validate final URL syntax/scheme before domain comparison

The current `same_domain` call checks only hostname. Independent real-monitor probes found:

- `file://gov.example/doc.pdf` creates a trusted PDF baseline.
- `https://[invalid/doc.pdf` raises ValueError and aborts the run.

The original P01 contract requires nonempty invalid final URLs to be rejected; this remains a post-capture check, not P02 pre-request enforcement.

1. Add real-monitor regressions for the two cases before fixing them.
2. For nonempty final_url, validate that it is a syntactically valid HTTP(S) URL with a hostname and valid port before the requested-domain/subdomain check. Reject unsupported schemes, malformed IPv6/ports, missing hosts and malformed values with an explicit diagnostic. Handle expected URL parsing errors as a rejected source outcome, not an exception that aborts the loop. Reuse existing normalization where suitable, but do not alter the shared URL policy module in this packet.
3. Keep empty-final_url compatibility for old fakes by falling back to the already validated requested URL. Keep legitimate same-host/subdomain behavior. Do not silently normalize a nonempty malformed URL into the fallback.
4. Perform this final-URL validation before parsing/accepting the captured content. Apply it consistently to PDF/browser/static verified methods.
5. Test a malformed source followed by a separate valid source in the same run: the bad source cannot create trusted/candidate content, and the valid source is still processed. Also test an existing baseline and candidate survive a rejected final URL. Diagnostic status may change in real mode; dry mode must write nothing.

## C2 — Explicitly reject encryption even if the parser can open the PDF

Your current fixture inserts an encryption dictionary into an existing PDF. Its parse failure does not prove all encrypted PDFs are rejected. A genuinely encrypted PDF with an empty user password opens successfully in pdfplumber and currently returns `(True, '')` from validate_pdf.

Use the reviewer-generated synthetic fixture at:
`C:/Users/Monirul/.codex/visualizations/2026/09/05/01a07036-0d29-71e1-9d14-dd7e5b9cc67c/p00_review_20260905/p01_empty_password_encrypted.pdf`

Expected SHA-256:
`9f2cea19ab245c5307614bf627ffff3caedcb62ee993132fbc9a2978f3fcc69e`

Copy it to the allowed fixture path and verify the hash. It is a synthetic one-page document, generated outside the project for this review. No runtime PDF-writer dependency is required; do not install pypdf or a new parser. The fixture must be committed so candidate-export tests are self-contained.

1. Prove with the real existing parser that the fixture has one page and encryption metadata. The review confirmed `pdf.doc.encryption` is populated while the page remains accessible.
2. Add helper and real-monitor tests asserting this document is rejected, with a reason such as `encrypted_pdf`, and cannot create trusted/candidate content.
3. Inside the parser context, explicitly inspect encryption metadata and reject encrypted documents before returning success. Do not assume successful opening means unencrypted. Password-protected/inaccessible files may still fail parsing first; both outcomes remain rejection.
4. Keep the old damaged/encryption-shaped sample as malformed-content coverage if useful, but label it accurately. Retain real unencrypted PDF acceptance and MIME/parser-unavailable tests.

## C3 — Compare against frozen pre-run snapshot bytes

`test_real_run_detects_same_collapse_from_same_baseline` currently reads the same snapshot on both sides after the run. Its fallback can also make missing data compare equal. Codex injected corruption into the temporary trusted snapshot after the real collapse run; this test still passed.

1. In setup, save the original trusted path, hash and byte content before the tested run.
2. After the real collapse run, assert the stored path is still the expected path, the file exists, its bytes equal the saved pre-run bytes, and the trusted hash is unchanged. Remove the self-reference/fallback expression.
3. Run dry and real collapse comparisons sequentially from the same unchanged baseline, as in the original P01 plan, and compare their classification counts. Assert dry mode preserves complete store/event/snapshot/corpus bytes.
4. For rejected PDF tests, check all newly created snapshot artifacts, regardless of file extension. Candidate bytes may use a `.txt` filename; an assertion limited to `.pdf` files does not establish no artifact was written. For existing state, compare trusted/candidate bytes and event content against frozen pre-run values while permitting the documented live diagnostic changes.
5. Demonstrate in a temporary fixture only that corrupting or deleting the trusted snapshot after the monitored run causes the corrected preservation assertion to fail. Do not modify production source or user snapshots for the demonstration.

## Verification and local commit

Show the intended RED results for invalid final URL, genuine accessible encryption and the old preservation assertion. Implement only these corrections. Then run:

```powershell
python -B tools/run_offline_checks.py --pattern 'test_p01_*.py'
python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'
git diff --check
```

Use the audited interpreter and zero ordinary network attempts. Do not alter the P00 harness/tests or their legacy-count assertions to hide regressions.

Write `P01-C1-worker-report.md` before committing. Stage only the allowed changed paths and create a new local commit:

```text
fix(tracker): P01-C1 reject invalid destinations and encrypted PDFs
```

Verify the exact final commit, including the binary fixture and report:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'
```

Report base/final SHA, branch/worktree, file scope, RED/GREEN evidence for C1-C3, genuine encrypted fixture hash/parser evidence, malformed-source isolation, preservation mutation result, cumulative/exported counts and any limits.

No push, merge, P02 work, live crawling, corpus changes or embedding. End with **“P01-C1 complete for review; stopped. P02 has not been started.”** If incomplete, report that instead.
