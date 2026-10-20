# Government service tracker and evidence update specification

Date: 5 September 2026

Status: Proposed implementation specification for user review. The user authorized reconciliation and specification writing; this document does not mean implementation, scheduling, publication, account connection, or a corpus change has occurred.

Companion evidence: [Freshness audit reconciliation](2026-09-05-freshness-reconciliation.md).

## 1 Purpose and outcome

Give Monirul a manageable inbox of possible changes to Bangladesh government services. Each alert explains what may have changed, its urgency, its evidence and where to verify it. After Monirul supplies official screenshots or documents, prepare precise changes against the actual current corpus. Following approval of those changes, automate corpus publication, affected-text embedding and retrieval verification.

The system must reduce manual transcription, document organization and repeated reading without claiming comprehensive coverage or allowing news reports, OCR mistakes or model judgments to silently alter the knowledge base.

### Scope

- Monitor NID, e-Passport, e-Return, BDRIS and Visa through curated official sources and selected Bangladesh news outlets.
- Support independent uploads as well as evidence attached to an alert.
- Accept screenshots, digital/scanned PDF, DOCX, and spreadsheet evidence; provide controlled conversion for legacy DOC/XLS.
- Identify updates, additions, explicit retirements, future changes, conflicts, duplicates and pre-existing coverage gaps.
- Preserve Bangla source wording and distinguish generated translations.
- Offer one exact change-set approval followed by automatic preparation, publication and verification.
- Keep the existing four-service chatbot usable during development. Keep deterministic routing and existing retrieval thresholds unchanged unless a separate change is benchmarked and approved.
- Keep Visa monitoring and evidence preparation available while live Visa publishing remains blocked until its metadata, embedding, routing and evaluation gate is separately accepted.

### Exclusions from the first release

Universal crawling, CAPTCHA evasion, applicant-account collection, autonomous transactions, public hosting, new messaging-account integrations, automatically changing retrieval thresholds, and training an OCR or language model are outside this implementation. The small browser-assisted collector is a subsequent acquisition convenience.

## 2 Decisions and alternatives

| Approach | Assessment |
|---|---|
| Repair every legacy JSON queue and extend the manual-edit workflow | Smaller initial code change, but preserves duplicate state stores and makes exact approval, additions, retirement and crash recovery harder |
| Rebuild the chatbot and all corpora around a new platform | Excessive scope; risks disturbing working routing and corpus-specific semantics |
| Retain repaired sensors and service adapters; introduce a maintenance store and staged publication | Selected: addresses the actual update workflow while preserving the working chatbot and reusable acquisition work |

Use Python with the existing project stack, a local SQLite maintenance database, immutable evidence files, a separate Streamlit operator app and a CLI using the same application services. Use the existing configured LLM backend for bounded classification, summarization and patch drafting; no provider or model change is implied. Keep BAAI/bge-m3 and existing Chroma distance/normalization settings for the active corpus.

SQLite transactions make related operational-state writes atomic within that database; they do not make arbitrary JSONL and Chroma writes one transaction. Consequently, publication uses immutable staging and a single transactional active-release pointer. This is a design choice based on the database's transaction boundary. [SQLite transactions](https://www.sqlite.org/lang_transaction.html), [atomic commit](https://www.sqlite.org/atomiccommit.html).

The first data-processing pilot is e-Passport. Its fees expose numeric, language, table and scope errors clearly. All five services can receive alerts before all five support managed publication.

## 3 Operator experience

The separate maintenance app has four views: Inbox, Evidence and proposals, Update history, and Source health. It uses the existing visual conventions initially; a redesign of the public chatbot is unnecessary.

### Alert card

Every alert contains:

- Priority and a short reason for that priority.
- Service or services, topic, affected population and geographic/application scope.
- A concise title and a 60-120-word summary where sufficient article text is available. Short items can have shorter summaries.
- A brief verbatim excerpt, clearly separated from the paraphrased summary. Limit reproduced newspaper quotations to 25 words per source per alert; retain the URL and attribution.
- Publisher, article title, publication time, last substantive article update, first discovery time, and the separately identified policy effective date, if known.
- Evidence status and policy status; never infer implementation from a headline.
- Potential corpus impact and action: watch, verify evidence, review patch, or none.
- Official verification links labelled Direct supporting notice found, Official page inspected, or Suggested official starting point.
- For each official link: purpose, authority, last link check and result. Unchecked or inaccessible suggested links must not appear verified.
- Related reports grouped under the same event and a short explanation of what changed since the previous notification.

Use the article's language for the initial excerpt. Summaries can be displayed in Bangla or English by operator preference without altering the underlying quotation or evidence. Generated translations are labelled.

If only the headline or search snippet was retrieved, set content_access to headline_only or snippet_only, identify the limitation on the card, and restrict the summary to that available text. Do not invent an excerpt or summarize an inaccessible full article.

### Priority

| Priority | Rule | Delivery |
|---|---|---|
| Urgent | Credible evidence suggests current guidance may already be wrong in a consequential way, or an applicable effective date/deadline is within 72 hours | Immediate in-app notification on the next successful check; clearly label media-only evidence |
| Needs review | A relevant official resource or substantial reported change needs investigation, without the urgent condition | Inbox and next daily digest |
| Watch | A proposal, consultation or tentative announcement lacks established implementation | Watch list and daily digest; no corpus change |
| Informational | Relevant background or explanation with no identified change to current guidance | Inbox; excluded from digest unless requested |

Priority is independent of evidence strength. A news-only item can be urgent to investigate. An official proposal can be low urgency. A high-confidence interpretation is not equivalent to verified authority.

The operator can override priority with a recorded reason. A material later development re-evaluates priority and preserves the override history. Routine repeated observations cannot escalate a proposal into a fee change.

### Operator actions

Watch, snooze until a date, mark irrelevant, merge duplicate, mark resolved, attach evidence, request extraction, inspect proposal, approve selected changes, reject, and request rollback.

Resolving a news alert does not approve a corpus update. Approving extracted text alone does not approve a patch. A proposal approval screen always shows the exact record changes.

Snoozing silences routine repeats until its date; a newly verified effective change or an imminent effective date may resurface the event with an explanation. Irrelevance decisions apply to the specified event/topic scope, not every future notice from that domain.

## 4 Sources and checking

### Curated service and topic directory

Introduce config/tracker_sources.yaml, distinct from the old acquisition allowlist until migration is validated. Each source has source_id, role (official or news), authority/publisher, explicit host policy, seed or feed/search URL, supported services/topics, expected language, check method, interval, enabled flag, last manual verification and purpose.

Store website ownership/authority separately from TLS transport status and document applicability. A .gov.bd suffix is a useful hint, not a complete authority decision. Some documents are hosted on approved linked storage domains; those destinations need explicit verification and registry entries.

The e-Passport verification directory starts from already observed addresses:

- https://www.epassport.gov.bd/instructions/passport-fees for domestic and overseas fee categories.
- https://dip.gov.bd/ for department notices.
- https://ssd.gov.bd/ for responsible-division notices.

These are starting points, not a claim that a particular circular is present. The remaining service entries are seeded from existing registries and corpus provenance, then inspected and classified during implementation. An unvalidated entry stays disabled. Never construct circular paths by guessing URL patterns.

Initial news candidates are Prothom Alo, The Daily Star, bdnews24 and The Business Standard. Register only feed/search endpoints that pass a real acquisition smoke check. RSS availability must not be assumed; previous attempts in this conversation did not establish reliable feeds for all four outlets. Use validated feeds where possible and an explicitly configured search provider for gaps. No new paid provider, credentials, plugin, or email subscription is automatically installed.

The first prototype may use replayed news observations to exercise the pipeline, but cannot be called a live tracker until at least two independent publisher paths and the official pilot paths pass live acquisition acceptance.

### Lightweight official monitoring

Check selected pages and notice indexes rather than harvesting whole sites. A small scoped fetch is still needed to detect changes.

- Record response validators where available; conditional fetching is an optimization. Do not use modification headers as proof of a policy change.
- Compare normalized relevant page sections and newly linked notices. Record binary document changes as source-change signals, not an interpretation of their meaning.
- Keep observation fingerprints tied to extraction version and page/state identity. A language toggle, login shell, bot wall or changed extraction method is not automatically a policy change.
- When a page can be read only through a browser, use the repaired acquisition adapter. Failed access produces a health record and optional manual-check task.
- Preserve explicit robots restrictions; report indeterminate/failed robots checks separately. For the new unattended collector, defer fetching when robots access is indeterminate rather than silently inheriting the legacy fail-open behavior. This is an intentional proposed policy change, not a statement that the current code behaves this way.
- Approve and validate all redirect destinations before following them; apply the same checks to HTML, PDFs, document downloads and browser sub-navigation. Cap redirect chains at five hops. Do not automatically follow a link merely because a newspaper calls it official.
- A bounded crawl that hits a limit, omits pagination, or fails to inspect an index completely has coverage_complete=false. It cannot establish resource disappearance. Even confirmed source disappearance never authorizes retirement of a rule.

### News observations and event matching

Classify using service names/aliases, Bangla and English topics, issuing authority, action verbs, affected group, circular identifier and dates. Use models to propose interpretations with supporting text spans; deterministic validation ensures cited spans exist and URLs were actually observed.

Distinguish, for example, passport issuance fees from currency endorsement charges; Bangladesh visa rules from another country's visa policy; tax-return filing deadlines from unrelated tax news.

Keep articles as separate observations. Cluster them into one evolving event when their authority, subject and affected scope align and their document identifiers or stated decision facts are compatible. Headline similarity alone is insufficient. When identity is ambiguous, suggest a merge rather than force it.

Multiple reprints of the same wire report are supporting links, not independent confirmations. Preserve attribution when available. Later corrections, postponements or withdrawal reports attach to the event with history.

### Scheduling and health

Proposed default: one daily official-source check at 09:00 Asia/Dhaka, news checks every six hours, and a daily digest at 18:00. These defaults are configurable and are not active now.

Run a local maintenance worker with a SQLite job lease; a second worker cannot run the same job concurrently. It checks due work after startup/wake, performs one bounded catch-up run, and records missed intervals rather than replaying every missed tick. A sleeping or offline laptop cannot perform checks.

Per source, store last_attempt_at, last_success_at, last_content_change_at, last_complete_check_at, next_due_at, status, reason, failure streak and coverage. After two consecutive failed due checks show degraded; after two missed scheduled intervals show stale. Notify once on a transition that requires action and once on recovery, not on every retry.

Use one concurrent fetch per host and an initial two-second minimum interval, with bounded retries and server retry guidance. A 403, challenge, certificate failure or 429 becomes an explicit outcome; do not retry indefinitely or turn it into a content change.

First-run baseline uses a seven-day news lookback. Older articles found later are background or coverage-gap leads, not new policy announcements. Publication date, page-wide date, effective date and retrieval date are separate fields. When the publication date is unclear, label it unknown and route to review.

Initially, notification delivery is local: inbox, in-app badge and digest. The background worker persists notifications while the UI is closed. Email, Telegram, push or other external delivery are separate integrations requiring destination selection. An outbox key of event_id plus material_revision prevents duplicate enqueueing; repeated identical checks remain silent.

## 5 State and identity

Use stable UUID event identifiers and an indexed canonical matching key. Do not include a growing retry/missing count in an event ID. Store every contributing observation and transition. Resource absence episodes have their own identifier; reappearance closes the episode.

Keep these dimensions independent:

| Dimension | Values |
|---|---|
| Policy status | unknown, proposed, reported_approved, officially_approved, effective, postponed, withdrawn, superseded, contradicted |
| Evidence status | news_only, official_link_found, official_artifact_supplied, verified, incomplete, conflicting |
| Processing status | awaiting_evidence, extracting, extraction_review, comparing, proposal_ready, awaiting_approval, approved_waiting_effective, preparing, ready_to_publish, published, verification_failed, rejected, no_change |

Statuses require evidence and a reason; they are not a compulsory linear chain. Discovery can start with an already effective circular. A document can be verified while its applicability remains conflicting. Store approval and effective dates per claim/patch item when a document contains different commencement dates.

Reserve published for a verified active release. A source baseline advance, approval or completed OCR run is not publication.

## 6 Storage and interfaces

All new state lives under data/maintenance/. It is not created until implementation.

| Store | Contents |
|---|---|
| maintenance.sqlite3 | Source health, observations, event membership/history, evidence metadata, extraction runs, change sets, approvals, jobs, release metadata, active release pointer and notification outbox |
| artifacts/sha256/<full hash>/ | Immutable original bytes and immutable extraction artifacts; original filename is metadata rather than a path supplied by the uploader |
| releases/<release ID>/ | Immutable service JSONL snapshots and a manifest of hashes, record IDs/revisions, model/settings and Chroma collection names |
| exports/ | Optional compatibility exports of the currently active corpora; not the source of truth once managed publishing is enabled |

Use foreign keys, unique constraints for observation/version identity and notification keys, and explicit transactions. Keep the SQLite file on the local disk. Use a single application writer lease for long publication preparation; database transactions themselves remain short.

Core persisted records:

| Record | Required contract |
|---|---|
| Observation | ID, source ID, observed/requested/final URL, publication/update/observation times, content-access level, content hash, title, available text artifact, extraction version, suggested services/topics/scope, quoted supporting spans, retrieval outcome |
| Event | ID, subject and scope, three status dimensions, priority/reason, material revision, effective-date claims, operator disposition and related observation IDs |
| Evidence artifact | Full byte hash, original filename/type, detected type, original source URL or explicit unknown, upload time, capture/publication time if supplied, authority assertion, verification status/method/reviewer, event links |
| Extraction | Artifact hash, extractor/version, language, ordered blocks, page/sheet/row/cell/image coordinates, tables and footnotes, raw and normalized values, uncertainty issues and correction history |
| Change set | Immutable patch hash, base release ID, base complete-record hashes, item operations, exact replacement rows, evidence hashes/spans, effective dates, dependencies, proposed test cases and rationale |
| Approval | Approval ID, operator/time, exact patch hash and evidence revisions, selected dependency-complete items, publication/effective-date authorization, revocation and expiry reason if invalidated |
| Release | ID, preparation base/parent release, approved change-set IDs, complete service-to-snapshot/collection map, routing-registry snapshot/hash, manifest hash, preparation/verification status and publication time |
| Job | ID and idempotency key, type, referenced input hashes, lease, attempt, stage, outcome and compact errors; malformed imports cannot crash another job |

Use (service, record_id) as the logical corpus identity. Store complete record JSON for revisions, not only text, because metadata changes can alter retrieval and citations.

Application boundaries:

- Sensors return observations and health outcomes; they cannot import or call the publisher.
- Event processing consumes observations and produces inbox changes.
- Extraction consumes evidence references and produces blocks/issues, not corpus records.
- Comparison consumes validated extraction and one pinned corpus release; it produces a proposed change set.
- Approval records operator consent to an immutable patch.
- Publication consumes approved input hashes and produces a verified release.
- Chatbot readers resolve a release; they never read proposal drafts or news observations.

## 7 Evidence intake and extraction

### Formats

| Input | Extraction path |
|---|---|
| PNG/JPEG screenshots | Local OCR with Bangla and English recognition; preserve image coordinates and original image |
| Digital PDF | Existing pdfplumber-based text/table extraction first; retain page coordinates and ordering |
| Scanned or mixed PDF | Render affected pages for OCR; combine with digital text without duplicating the same region |
| DOCX | Parse paragraphs, tables, headings and hyperlinks; record structural block IDs and available document metadata |
| XLSX | Read sheets/cells and displayed/cached values; preserve row/column labels, units and formulas as evidence, without executing them |
| DOC/XLS | Convert a copy through an isolated configured converter with macros and external-resource execution disabled; preserve original bytes and conversion provenance |

Initial local OCR adapter: Tesseract with Bengali (ben) and English (eng) language data, subject to the pilot quality gate. [Tesseract language data](https://tesseract-ocr.github.io/tessdoc/Data-Files-in-different-versions.html). Parser and converter versions are recorded. Installations and exact dependency pins belong to implementation, not this documentation task. If a required component is absent, report a per-file unsupported/missing-component issue and retain the upload for retry; never silently discard it or claim all formats passed.

A DOCX does not have stable page numbers independent of rendering. Use structural locations unless a rendition has actually been produced; label rendition page references accordingly. For screenshots, document order is supplied or confirmed by the operator; filename sorting alone is not proof of page order.

Compare detected file type with extension, enforce configurable size/page limits and reject corrupt or encrypted input with a clear reason. Treat content as data, including instructions embedded in documents or webpages. Uploaded filenames cannot select arbitrary filesystem paths; document links cannot authorize external actions.

### Quality and provenance

Preserve source-language text, headings, table row/column relationships, currencies, VAT status, footnotes, exceptions and legal/circular identifiers. Normalized Bangla/English digits and dates are comparison aids; original values stay available.

OCR/model confidence alone cannot verify a fee or effective date. Every changed critical amount, date, eligibility condition and negation must have a legible supporting span; uncertain critical fields block the affected proposal until corrected/confirmed in the evidence view. Record manual corrections as revisions.

A screenshot with unknown origin starts unverified. Monirul can confirm its official URL and context; store that attestation distinctly from a successful direct retrieval. TLS-validated transport alone does not establish that a document is current, applicable or legally superseding.

Original evidence is immutable. Re-uploading identical bytes reuses the artifact while retaining the separate upload/source association. OCR corrections or a new parser create a new extraction revision. They do not rewrite the original image/document.

English translations derived from Bangla evidence are labelled generated translations linked to the original spans. A translated value cannot override the source-language value. Partial pages never prove that omitted guidance has been withdrawn.

## 8 Corpus comparison and patch preparation

1. Pin the active release and load complete service records using service-specific adapters. Before managed publishing exists, import the current JSONL and vector metadata as an explicit baseline; first successful website fetch does not certify corpus freshness.
2. Generate candidate affected records from service, topic, source/circular IDs, metadata, normalized facts and multilingual semantic retrieval. Search all record variants that may repeat the same fact; semantic proximity alone cannot authorize a replacement.
3. Compare equivalent claims. For fees match currency, VAT basis, location, applicant group, page count, validity, delivery speed and effective period. Unknown scope stays unknown.
4. Classify each claim as no_change, correction, replacement, additional_information, new_topic, supersedes, withdrawal, future_effective_change, conflict, or coverage_gap.
5. Build exact proposed rows and explain why each changes. Preserve stable IDs for an existing concept; assign new IDs for new concepts. Retirement requires explicit replacement/withdrawal evidence and stated scope, never a missing URL or screenshot omission.
6. Validate schema, citations, supporting spans, numeric/date consistency, duplicate content, Bangla/English consistency and dependencies. Flag unresolved overlaps/conflicts for human review.
7. Present old/new values and complete affected rows beside the evidence location. Include related records considered but intentionally excluded, with scope reasons where useful.

Service adapters preserve the current per-service schemas. Update derived fields together: text, content where present, answer/question language fields, headings, char_len, source_url and embedding metadata. Do not leave a stale Bangla answer beside a corrected English fee.

Changes to a topic that affect concept_registry.json produce an explicitly reviewed routing-registry patch, coupled to the dependent corpus changes in the same release. Every release carries an immutable registry snapshot/hash, normally unchanged. Updating a fee within an existing concept does not change routing. News classification may use an LLM; user-query service routing remains deterministic. New routing concepts require their own benchmark and approval before publication; they are outside the initial fee-update pilot.

The often-discussed 4,025-versus-3,500 comparison is a required synthetic test: 3,500 plus 15% VAT equals 4,025. It must yield no fee reduction when those are the only differences. It is not a claimed real policy change.

### Patch approval

The approval screen binds selected items to one exact patch hash, evidence/extraction versions, effective-date interpretation and base record/release hashes. Selecting one item includes its required dependent language/summary updates or explains why it cannot be approved alone.

Editing an approved patch invalidates its approval. Changes to relevant target records, dependencies, extraction/evidence or applicability block publication and require revised review. A newer unrelated corpus release triggers automatic restaging against the latest release when all approved target/dependency hashes, evidence and intended changes remain identical; preserve the approval and record the rebase. Keep the original reviewed base in the immutable change set and record the preparation base separately. Mere last-checked timestamps or repeated identical news do not invalidate approval.

Unknown source state is not a reason to skip a guard. New relevant evidence that may supersede an approved change holds that change for review. A valid stored official artifact does not automatically expire merely because its website is temporarily unavailable.

For future-effective changes, approval explicitly includes scheduled activation of the exact patch at the recorded time/date. Use Asia/Dhaka for date-only Bangladesh commencement dates, keep the original wording, and hold ambiguous times. At activation, repeat revision/conflict checks. If the laptop was asleep, run these checks on wake and report the delay. No additional approval is needed when the same authorized patch remains valid.

## 9 Safe publication and retrieval

### Why stage a new release

The current reembed CLI updates records in place and app.py caches collection handles. A multi-record update can therefore be partially visible, and a future switch of collection names could leave Streamlit using the old handle. Both issues must be addressed before enabling the publisher.

Use immutable Chroma collections for changed services, named from the logical service and release ID. Copy existing embeddings for unchanged record text and compatible metadata/settings; encode only new or changed text with the unchanged BGE-M3 model. Metadata-only changes update metadata without recomputing unchanged vectors. Retired IDs are absent from the new active collection. Existing historical collections remain available for rollback but are not queried by normal readers.

Chroma supports retrieving records including embeddings and adding them to another collection. The implementation must verify behavior against the project's installed, pinned version; this design does not assume a cross-store transaction or an atomic collection rename. [Chroma collection copying](https://cookbook.chromadb.dev/core/collections/).

### Publication protocol

1. In a short SQLite transaction, validate the approval, evidence and base revision; acquire a publication job lease and record preparing. Only one publisher prepares an active switch at a time.
2. Outside the transaction, materialize immutable candidate JSONL snapshots for affected services and populate new Chroma collections. Carry references to unchanged service snapshots/collections into the candidate release map. Include the unchanged routing-registry snapshot, or the exact approved registry patch coupled to this release.
3. Verify exact ID sets, record/document hashes, required metadata, embedding dimensions/settings and citation provenance. Check retired IDs are absent. Run targeted deterministic fact checks and Bangla/English retrieval tests against the candidate collections.
4. Recheck the approval, source/conflict state, effective time and preparation-base active-release ID immediately before publication. If a relevant precondition changed, leave the candidate unpublished pending review. If only an unrelated release advanced the pointer, restage and reverify the identical approved patch against that release without new approval; preserve all unrelated changes.
5. In one SQLite transaction, compare-and-swap active_release_id, record the published release/job result, and enqueue its notification. No JSONL or Chroma write occurs inside this commit.
6. Readers starting after the commit resolve the new release. Requests already in progress use their pinned previous release consistently. Do not label the release published before verification and pointer commit.
7. Produce optional compatibility exports and the operator result. Export failure is reported separately and cannot move the active pointer back silently.

Failure before the pointer switch leaves the previous release active. A crash after the switch is reconciled by reading the transactional pointer and publication record. Retry stages are idempotent by release/job key and verify existing artifacts rather than blindly appending.

Rollback selects a verified prior release through a recorded operator action, rechecks subsequent changes and applicability, then switches the pointer transactionally. It preserves the faulty release and evidence for diagnosis. A known expired rule cannot be silently restored as current truth; show the issue before rollback.

Do not delete old collections during publication. Retention initially keeps all approved evidence and published revisions; present storage usage. Any future cleanup requires a reference scan, a preview and protection for active, in-use, rollback and approval references.

### Compatibility and migration

Introduce a common release resolver under src/knowledge_release.py. All supported chatbot, ad-hoc query and evaluation entry points must use it when maintenance-managed publication is enabled.

- answer.py continues to use logical service names and the same allowed-service set; physical collection names are resolved through the release manifest.
- app.py caches the model/backend separately from collection resolution. Pin a release at the start of each turn; key collection and routing-registry caches by release ID. disambiguate.py and other routing callers use that release's registry snapshot while retaining deterministic routing logic.
- Update query_nid.py, query_epassport.py, query_ereturn.py, query_bdris.py and corresponding eval entry points to resolve the active release or explicitly request a historical one.
- Embedding/reembed scripts must reject direct mutation of a maintenance-managed service unless operating on an explicitly designated unpublished staging target.
- Exclude historical/staging collections from normal collection discovery. Test that old guidance cannot leak via a fallback list_collections call.
- A service not yet managed uses its legacy collection. Bootstrap a managed service only after corpus/index parity and retrieval checks; unknown provenance stays marked legacy instead of becoming newly verified.
- After cutover, immutable release snapshots and the active pointer govern current knowledge. data/chunks files are maintained compatibility exports and must be checked for manual drift; tools must not silently treat edited exports as published truth.
- Do not perform this cutover during the alert-only phase. The existing chatbot remains on its current paths until the complete pilot gate passes.

## 10 Implementation boundaries

These paths describe proposed responsibilities, not files created by this specification.

| Path or component | Responsibility |
|---|---|
| acquisition/transport.py, acquire.py, crawler.py, urls.py, robots.py | Reconcile existing edits; make capture outcomes and destination/robots policies explicit and shared |
| freshness/known_monitor.py and mapping.py | Retain comparison/mapping logic behind an adapter; no authority to approve or publish |
| maintenance/store.py and migrations.py | SQLite schema, transactions, leases, validated legacy import |
| maintenance/models.py | Versioned observation/event/evidence/patch contracts and validators |
| maintenance/sensors/official.py and news.py | Bounded acquisition and source outcomes |
| maintenance/events.py, priority.py, verification_links.py | Event membership/history, priority rules and curated official lookup |
| maintenance/notifications.py and worker.py | Due checks, catch-up, outbox deduplication and local delivery |
| maintenance/evidence/intake.py, extract.py, quality.py | Immutable files, parser/OCR/converter adapters and extraction issues |
| maintenance/corpus/adapters.py and compare.py | Per-service schemas, affected-record search and proposed patches |
| maintenance/approvals.py | Exact patch approval and invalidation |
| maintenance/publish.py and verify.py | Release preparation, tests, pointer switch, recovery and rollback |
| maintenance_app.py and maintenance/cli.py | Operator interfaces calling the same application services |
| src/knowledge_release.py plus current reader entry points | Active-release pinning and compatible collection lookup |
| config/tracker_sources.yaml | Curated official/news sources, service-topic mappings and cadence |

Keep modules small enough to isolate each boundary; split format adapters into files when implemented. Avoid a new service or queue server for this single-laptop deployment. New operational tables supersede active JSON queues after a validated one-time import; do not maintain competing writable stores.

## 11 Rollout and acceptance gates

| Gate | Deliverable | Required evidence before advancing |
|---|---|---|
| G0 Baseline and retained-sensor repair | Reproducible acquisition/comparison code and test isolation | R1, R6 and R8 fixed for retained paths; reviewed local edits integrated deliberately; exact candidate commit passes affected tests with networking prohibited |
| G1 Alert inbox | Official/news observations, event grouping, priorities, excerpts, verification links and health | Proposal fixture produces Watch/no writes; live publisher/source endpoints validated; duplicate and failure cases below pass |
| G2 Evidence and comparison | Upload/extraction/review and exact patch preparation | Real representative e-Passport evidence extracts accurately; supported-format and scope tests pass; no corpus/vector writes |
| G3 Approved publication pilot | One complete e-Passport update through staging, approval, activation and rollback | Real Chroma in an isolated database; unchanged-text embeddings reused; readers refresh; crash/stale-approval tests pass |
| G4 Service expansion | NID, e-Return and BDRIS adapters and fixtures | Schema/metadata parity and per-service multilingual regression checks; no changed routing behavior |
| G5 Assisted acquisition and Visa decision | Focused browser collection; separate Visa release decision | Provenance capture tested; Visa corpus coverage reviewed and Phase 4 accepted separately |

The first three end-to-end stories are a proposal that changes nothing, an evidence-backed scoped fee update, and additional guidance that creates a nonduplicate record. Add retirement and future-effective stories before accepting G3.

### Concrete acceptance scenarios

| ID | Input or fault | Required result |
|---|---|---|
| A01 | News says a fee reduction is proposed | Watch; proposal status; no corpus or embedding action |
| A02 | Media reports an already effective deadline change; no official document retrieved | Urgent investigation; media-only label; official verification links; no automatic corpus change |
| A03 | Three reports repeat one announcement, including a syndicated copy | One event, three attributed observations, no false independent-confirmation count |
| A04 | Same event gains an official circular or withdrawal | Material revision and one new notification; preserved history |
| A05 | Old article resurfaces; page banner shows today's date | Original article date retained where available; historical/background label, not a fresh policy alert |
| A06 | Full article unavailable | Headline/snippet-only label; no invented quotation or full-article summary |
| A07 | Passport endorsement article and passport issuance article | Correctly separate scopes and corpus impact |
| A08 | Search finds no confirming circular | Curated official starting links labelled suggested; no fabricated direct URL |
| A09 | 403, challenge, TLS failure, incomplete pagination or laptop sleep | Failed/degraded/stale coverage shown separately from unchanged content |
| A10 | Initial crawl sees differences from legacy corpus | Comparison can propose coverage/review work; no claim that first observation proves a newly enacted rule |
| A11 | A new URL replaces an old one; different old URL in another episode | Complete transition identity, correct review target, no collision |
| A12 | Repeated missing-resource observations | One episode updated; no growing duplicate queue; no retirement inference |
| A13 | Malformed imported event/job precedes valid work | Bad row quarantined; valid independent work continues |
| A14 | 1,000-character baseline becomes a 100-character error-like capture | Dry and real comparison classifications match; dry mode changes no persisted state |
| A15 | PDF redirects to an unapproved host or returns HTML with a PDF extension | No trusted PDF baseline or corpus evidence; explicit diagnostic |
| A16 | Clear Bangla screenshot, scanned PDF, digital table, DOCX and XLSX | All critical amounts/dates/conditions match hand-checked evidence; locations and footnotes preserved |
| A17 | Cropped table or illegible digit | Affected proposal blocked for correction/completeness; no guessed amount |
| A18 | Same evidence uploaded twice | Artifact deduplicated; both upload associations preserved; no duplicate records |
| A19 | 3,500 before VAT versus 4,025 including 15% VAT | No fee-reduction proposal |
| A20 | Overseas fee change; domestic table is similar | Only applicable overseas claims change; domestic exclusions explained |
| A21 | Same fee repeated in Bangla, English and a summary | All applicable variants proposed together with consistent derived fields |
| A22 | Old but valid guidance absent from corpus | Addition labelled coverage_gap rather than new policy |
| A23 | Official future date or conflicting official instructions | Pending activation or conflict hold; current answers unchanged |
| A24 | Document contains instructions to ignore rules or write files | Treated as evidence text; no tool execution or authorization |
| A25 | Operator approves exact patch, then a relevant target/dependency or evidence changes | Publication blocked pending a revised review; timestamps alone do not invalidate |
| A26 | Operator approves a subset missing a dependent bilingual edit | Approval blocked or dependency-complete selection required |
| A27 | No approval, forged job reference, missing accepted evidence or unknown base hash | No published corpus/index mutation |
| A28 | Two changed records, one new record, one retirement | Exact new active ID set; only changed/new text encoded; unchanged vectors reused; retirement absent from active retrieval |
| A29 | Metadata-only change | Metadata updated; unchanged text vector reused |
| A30 | Crash after any staging step or just before/after pointer commit | Old or new complete release; never a mixed active release; idempotent recovery |
| A31 | Corpus JSONL and vector ID/hash mismatch | Publication refused; previous release remains active |
| A32 | Streamlit cache survives publication; CLI starts later | New turns use new release; in-flight turn stays on pinned old release; no stale historical collection fallback |
| A33 | Approved change becomes effective while laptop is off | Revalidated on wake; delayed activation reported; no blind activation of superseded evidence |
| A34 | Rollback requested after another update | Show affected revisions and applicability; no silent loss of later changes |
| A35 | Visa patch while live Visa support is absent | Prepare/review evidence but block live embedding/routing; explicit unsupported-service outcome |
| A36 | Existing NID/e-Passport/e-Return/BDRIS gold queries | No unexplained routing, multilingual, grounding or citation regression |
| A37 | Worker restarts or two workers claim the same check | One active job lease; no duplicate material-event outbox entries |
| A38 | Legacy DOC/XLS converter missing or conversion fails | Original retained; per-file actionable issue; other files processed; no false extraction success |
| A39 | An unrelated release advances the pointer after approval or during staging | Same approval retained when target/dependency hashes and evidence are unchanged; restage/reverify against latest release; preserve unrelated updates |
| A40 | An approved new concept needs both corpus and registry changes | Both become visible through one verified release; each turn pins matching registry/corpus versions; unapproved routing changes block publication |

Acceptance is based on outcomes, not merely assertion counts. Run deterministic tests with all network access prohibited and injected source/robots responses. For publication acceptance, use an isolated real Chroma store in addition to fakes. Test candidate-commit code, not only a modified working tree.

## 12 Delivery boundary and next work

This specification and its audit companion are the deliverables of the current approved task. No tracker, scheduler, OCR pipeline, inbox, database, corpus patch or vector publication is active.

After specification review, produce separate executable plans for G0-G1, G2 and G3. Each plan must define production entry points and exact regression tests, so test fixtures cannot silently enable a guard that real execution omits. Keep G4-G5 outside the initial implementation plan.

The implementation must report measured source accessibility and extraction quality rather than promising comprehensive monitoring or perfect OCR. If source access or provider setup is missing, keep that source disabled/degraded while allowing the rest of the workflow to work.
