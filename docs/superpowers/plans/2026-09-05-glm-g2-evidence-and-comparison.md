# G2 Evidence and Comparison Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` if available. Execute only the released packet; commit, report and stop for Codex review.

**Goal:** Turn supplied evidence into reviewable, precisely scoped e-Passport corpus changes without modifying live knowledge.

**Architecture:** Immutable originals feed versioned extraction blocks and human corrections. A service adapter compares equivalent claims and builds exact replacement rows linked to supporting evidence. Models propose; deterministic checks and operator review resolve uncertainty.

**Tech stack:** Python, SQLite, existing pdfplumber, DOCX ZIP/XML parsing, openpyxl for XLSX, Tesseract ben+eng through a subprocess adapter, an explicitly configured legacy-document converter. Record versions; no automatic installation or new paid provider.

**Spec:** [Design](../specs/2026-09-05-tracker-evidence-update-design.md); [control guide](2026-09-05-glm-implementation-control.md).

## Global constraints

G1 must be accepted before dispatch. All control-guide constraints apply. This phase cannot approve or publish a corpus patch. Other services may retain evidence/extraction, but managed comparison is e-Passport only. Run cumulative offline and exported-commit commands after each packet. Real format/OCR checks are additional gates, not fake-only replacements.

## P10 — Immutable evidence intake and provenance

**Create:** `maintenance/evidence/__init__.py`, `maintenance/evidence/intake.py`, `maintenance/evidence/models.py`, `tests/maintenance/test_p10_intake.py`. Modify store/migrations/artifacts/fixtures; report.

**Interfaces:**

```python
ingest_evidence(store, *, content: bytes, filename: str, source_url: str | None,
                service: str, event_id: str | None, supplied_at: str) -> dict
attest_evidence(store, artifact_hash: str, *, actor: str,
                 source_url: str, context: str, now: str) -> dict
```

Evidence rows follow the spec artifact contract. Separate artifact identity from upload associations, attestation and extraction revisions. Intake limits: 50 MiB per file; PDFs at most 200 pages; ZIP document total expanded data 200 MiB and at most 10,000 entries. Make limits configurable downward/upward explicitly, never bypass them on parser failure. Detected type, extension and parser result are separate fields.

- [ ] **1. Add duplicate-upload and path-safety tests.**

```python
def test_duplicate_bytes_preserve_two_uploads(self):
    from maintenance.evidence.intake import ingest_evidence
    from tests.maintenance.fixtures import tiny_png
    kw = dict(content=tiny_png(), filename='evidence.png', source_url=None,
              service='epassport', event_id=None, supplied_at='2026-09-05T03:00:00Z')
    first = ingest_evidence(self.store, **kw)
    second = ingest_evidence(self.store, **kw)
    self.assertEqual(first['artifact_hash'], second['artifact_hash'])
    self.assertNotEqual(first['upload_id'], second['upload_id'])
    self.assertEqual(first['verification_status'], 'unverified')
```

`tiny_png()` is a checked-in/generated one-pixel valid PNG byte fixture, not a renamed text file. Names such as `../../outside.png` remain display metadata; storage paths come only from full content hashes.

- [ ] **2. Implement intake and schema migration.** Retain originals even when extraction is unsupported, but do not store an over-limit payload as accepted evidence. Store explicit rejected intake result/reason. Unknown URL starts unverified; attestation is distinct from direct retrieval and records actor/context. Document contents cannot call tools or change verification status.
- [ ] **3. Test wrong extension/detected type, encrypted/corrupt PDF, ZIP traversal/bomb limits, orphan event reference and interrupted artifact/database write.** Unreferenced immutable bytes after failure may remain for diagnostic cleanup; they must not appear as a successful upload. No automatic deletion.
- [ ] **4. Run checks, commit and stop.** A18/A24 intake behavior; no extraction promise yet.

## P11 — Digital document extraction

**Create:** `maintenance/evidence/extract.py`, `maintenance/evidence/pdf.py`, `maintenance/evidence/docx.py`, `maintenance/evidence/xlsx.py`, `tests/maintenance/test_p11_digital_formats.py`, `tests/fixtures/evidence/` small fixture documents and expected JSON. Modify evidence models/store/migrations; report. A separate `requirements_maintenance.txt` may record tested optional parser versions; do not edit existing model requirements.

**Interfaces:** `extract_artifact(store, artifact_hash: str, *, adapters: dict, now: str) -> dict`; each format adapter `extract(content: bytes) -> dict`. Result fields: `extraction_id`, `artifact_hash`, `extractor`, `version`, `language`, `blocks`, `issues`, `status`, `created_at`. Status is extracted/review_required/unsupported/failed. Blocks have stable IDs, kind (heading/paragraph/table/image), text, source-language tag and format-specific location; tables carry ordered cells, headers, spans, units and footnote references. Raw and normalized values are separate.

- [ ] **1. Make hand-checkable fixtures with expected values.** Digital PDF and DOCX contain a heading, Bangla/English amount, multirow table and VAT footnote. XLSX contains sheet labels, one cached amount and a formula with no cached value. Record expected cells/locations in JSON. Use synthetic fee data clearly labelled as such.

```python
def test_docx_locations_do_not_invent_page_numbers(self):
    from maintenance.evidence.docx import extract
    result = extract((FIXTURES / 'fee-table.docx').read_bytes())
    self.assertTrue(any(b['kind'] == 'table' for b in result['blocks']))
    self.assertTrue(all('page' not in b['location'] for b in result['blocks']))
    self.assertTrue(all('block_id' in b['location'] for b in result['blocks']))
```

- [ ] **2. Implement format adapters.** PDF keeps page/bounding boxes and table structure. DOCX walks paragraphs/tables in document order, preserves hyperlinks/footnotes and merged-cell semantics; does not follow external links or invent pagination. XLSX reads formulas and cached values separately without evaluating formulas/macros or external links; missing cached formula value is an issue. Preserve empty/merged cells needed to interpret table scope.
- [ ] **3. Add extraction revision storage.** Same artifact+adapter version+settings is idempotent; a changed version creates a new extraction. A corrupt member or parser exception becomes a per-file issue; other uploaded files still process. Store unsupported formats explicitly.
- [ ] **4. Run all real digital fixtures and byte/semantic assertions, then cumulative/export checks.** Print format/library versions and fixture pass counts. Missing openpyxl is a reported incomplete XLSX gate; installation requires the configured environment workflow, not a hidden fallback to CSV/plain text.
- [ ] **5. Commit and stop.** Part of A16; digital extraction is not OCR or evidence verification.

## P12 — OCR, legacy conversion and quality review

**Create:** `maintenance/evidence/ocr.py`, `maintenance/evidence/convert.py`, `maintenance/evidence/quality.py`, `tests/maintenance/test_p12_ocr_quality.py`, `tests/integration/test_evidence_ocr.py`; add scanned/cropped/screenshot fixtures and expected JSON. Modify extract.py/evidence models; report.

**Interfaces:** `ocr_image(content: bytes, *, runner, languages: str='ben+eng') -> dict`; `convert_legacy(content: bytes, filename: str, *, runner) -> dict`; `validate_critical_spans(extraction: dict, claims: list[dict]) -> list[dict]`; `correct_extraction(store, extraction_id: str, corrections: list[dict], *, actor: str, now: str) -> dict`.

`runner(argv: list[str], input_path: Path, output_dir: Path, timeout_seconds: int) -> dict` is an injected subprocess boundary, not a shell string. Return exit code, stdout/stderr and produced paths verified inside output_dir. Hard time limit120s/file. Use an explicit configured executable. Missing components return unsupported with installation requirement; no download.

- [ ] **1. Write missing-converter and uncertain-digit tests.**

```python
def test_missing_legacy_converter_is_actionable(self):
    from maintenance.evidence.convert import convert_legacy
    def missing(*args, **kwargs):
        raise FileNotFoundError('configured converter absent')
    result = convert_legacy(b'legacy-fixture', 'source.doc', runner=missing)
    self.assertEqual(result['status'], 'unsupported')
    self.assertEqual(result['issues'][0]['code'], 'missing_converter')
```

- [ ] **2. Implement OCR adapters and mixed PDFs.** Use existing renderer capability if available or report missing rendering component. OCR only raster/low-text regions; deduplicate with digital text using coordinates, not fuzzy whole-document deletion. Tesseract TSV supplies token coordinates/confidence; reconstruct lines/tables conservatively. Confidence never certifies amounts, dates, eligibility or negation. Cropped headers/unclear digits block affected critical claims. Screenshot ordering is supplied/confirmed by operator.
- [ ] **3. Implement isolated legacy conversion.** Convert copies only. Require a converter adapter with documented macro/external-resource suppression; if the local tool cannot guarantee that mode, leave DOC/XLS unsupported and report the gate. Do not invoke office automation against the user's running document session. Preserve original and converted hashes and version provenance.
- [ ] **4. Implement correction revisions.** Store corrected block/cell values, original values, actor and reason. Translation remains generated and cannot supersede original-language evidence. Changed extraction invalidation is enforced later by P15, but the revision IDs must already be immutable.
- [ ] **5. Run offline tests, then `python -B -m unittest discover -s tests/integration -p 'test_evidence_ocr.py' -v`.** Real Bangla screenshot, scanned PDF, mixed PDF and deliberately illegible/cropped evidence are manually checked against expected JSON. Missing ben/eng data or converter is reported per format. No “all formats passed” claim if unsupported. Commit/export and stop.

**Reviewer gate:** A16/A17/A38; all changed critical values have legible traceable source spans. Low-quality evidence remains review_required, not auto-corrected by model plausibility.

## P13 — e-Passport comparison and exact patches

**Create:** `maintenance/corpus/__init__.py`, `maintenance/corpus/adapters.py`, `maintenance/corpus/facts.py`, `maintenance/corpus/compare.py`, `maintenance/corpus/patches.py`, `tests/maintenance/test_p13_comparison.py`. Modify models/fixtures; report.

**Interfaces:**

```python
load_records(service: str, path: Path) -> list[dict]
compare_fee(old: dict, new: dict) -> dict
prepare_patch(*, service: str, base_release_id: str, records: list[dict],
              extraction: dict, claims: list[dict], backend, retrieve) -> dict
validate_patch(patch: dict, records: list[dict], extraction: dict) -> list[dict]
```

`retrieve(query: str, service: str) -> list[str]` proposes candidate record IDs, never performs writes; implement a deterministic all-record scan fallback when semantic retrieval is unavailable, with explicit retrieval-mode reporting. Model proposes claims/rows with evidence references using existing backend. Service adapter uses actual e-Passport fields: `id`, `text`, `content`, `char_len`, question/answer language fields where present, source_url, metadata and topics. No bulk replacement by text similarity.

Fee fact keys: amount decimal string, currency, vat_basis (`inclusive|exclusive|unknown`), vat_rate decimal string/null, jurisdiction, applicant_group, pages, validity_years, delivery_speed, effective_from/to, span_refs. Unknown scope is not a wildcard. `compare_fee` returns classification/reason/normalized_values; use Decimal and scope equality before monetary comparison.

Patch schema v1: patch_hash, service, original_base_release_id, base_record_hashes, dependency_hashes, evidence/extraction hashes, items, effective_at, issues, status. Each item contains operation (replace/add/retire), record_id, exact before/after complete rows, rationale, claim classification, span references and dependent item IDs. New IDs are stable across retries for identical approved inputs. No_change yields no item. Retire requires explicit authoritative withdrawal/replacement scope. New routing concepts yield an explicit routing_review_required issue; this pilot does not author a routing-registry change.

- [ ] **1. Write the VAT equivalence and scope tests first.**

```python
def test_vat_basis_is_not_a_fee_reduction(self):
    from maintenance.corpus.facts import compare_fee
    from tests.maintenance.fixtures import fee
    old = fee(amount='4025', vat_basis='inclusive', vat_rate='0.15')
    new = fee(amount='3500', vat_basis='exclusive', vat_rate='0.15')
    self.assertEqual(compare_fee(old, new)['classification'], 'no_change')
```

`fee()` constructs all scope/date/span fields with identical domestic48-page5-yearregular scope by default. Different overseas scope must return not_comparable, not replacement.

- [ ] **2. Build exact affected-record search.** Search source/circular IDs, topics, normalized claims and semantic candidates across every language/summary variant; explain excluded similar rows. Store complete base row hashes. Unknown origin/critical issue creates blocked proposal, not invented evidence. Valid older guidance absent from corpus is coverage_gap. Additional guidance in an existing routing concept can add a row; an actual new concept is held for separate review.
- [ ] **3. Implement proposed-row generation and deterministic parity checks.** Update text/content/answer variants/char_len together using the existing record format. Do not manufacture translations as official text. Preserve unaffected keys. Contradictory official evidence or future-effective changes remain held/pending. New fact mentions in old summaries must be listed as dependencies.
- [ ] **4. Add A19-A23/A26/A35 fixtures.** One replacement, one addition, duplicate, coverage gap, explicit scoped retirement, missing footer, conflict, new routing concept and unsupported service. Assert corpus byte hashes identical before/after `prepare_patch` and no vector API use.
- [ ] **5. Run checks, commit and stop.** Output is an exact proposal only; “ready” is not approval.

## P14 — Evidence/proposal UI and G2 pilot

**Create:** `maintenance/ui/evidence.py`, `maintenance/ui/proposals.py`, `tests/maintenance/test_p14_review_ui.py`, `docs/EVIDENCE_REVIEW.md`. Modify `maintenance_app.py`, `maintenance/cli.py`, store/migrations and adapters only for wiring; report.

**Interfaces:** CLI adds `upload --file --service [--event-id] [--source-url]`, `extract --artifact-hash`, `compare --extraction-id --service epassport --corpus <explicit-jsonl>` and `proposal --id`. Every command uses explicit maintenance root; comparing the original corpus is read-only. UI uses the same application functions, not duplicated business logic.

- [ ] **1. Write entry-point tests.** Upload one supported document, extract, compare and reload a proposal from SQLite. A second file fails extraction without losing the first. Use a complete temp corpus and assert unchanged bytes. Selection of one dependent bilingual edit must include the other or remain invalid.

```python
def test_proposal_review_does_not_expose_publish(self):
    from maintenance.ui.proposals import available_actions
    self.assertNotIn('publish', available_actions({'status':'proposal_ready'}))
    self.assertIn('inspect', available_actions({'status':'proposal_ready'}))
```

`available_actions(proposal: dict) -> list[str]` is a pure UI adapter introduced here. It returns inspect/reject/edit-selection for prepared proposals; approval comes in P15.

- [ ] **2. Implement side-by-side review.** Original page/image/cell, extracted text, source and attestation labels, uncertainty issues, scope, effective dates, old/new full rows and related excluded records. Correction updates extraction revision and regenerates proposal; never silently changes a ready patch in place. Separate generated summary/translation from verbatim evidence.
- [ ] **3. Run representative e-Passport pilot in a separate root.** Read existing supplied official evidence from `data/evidence`/`data/raw` and an explicitly selected screenshot/document; do not alter it. Track real extraction accuracy and all critical-value corrections. Use synthetic changes for replacement/addition/retirement tests unless an actual verified new rule is supplied. Do not present synthetic values as govt policy.
- [ ] **4. Run cumulative/export checks, inspect UI and report format matrix.** Record which formats actually passed real extraction, which need optional components, and which claims are held. G2 cannot claim all-format acceptance with missing required adapters.
- [ ] **5. Commit and stop for G2 review.** A16-A24/A26/A35/A38; preparation works, production corpus remains unchanged. Approval and publication remain G3.
