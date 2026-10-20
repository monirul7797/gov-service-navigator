# Ready-to-paste GLM prompt — P01 only

Copy the text below into GLM after reading the companion P00 acceptance report. This releases only P01, not all of G0.

---

You are GLM 5.3 Flash, the implementation worker for Govt. Service Navigator. Codex remains architect, planner, reviewer and phase owner. P00 has been accepted at final commit **`f808ce3c564d91544219abc8edcac96a58011f14`** after its two corrective reviews.

**Released packet: P01 — Dry-run fidelity and PDF trust boundary.** Implement only this packet, run checks, commit locally and stop for Codex review. Do not proceed to P02.

## Read before implementation

The original project is:
`C:/Users/Monirul/Documents/Structured Data/NID/gov-service-navigator/gov-service-navigator`

Read these files there, since the planning documents remain untracked and may not exist in an exported/new worktree:

1. `docs/superpowers/plans/2026-09-05-glm-implementation-control.md`
2. `docs/superpowers/plans/2026-09-05-glm-g0-baseline-and-sensors.md` — P01 only.
3. `docs/superpowers/specs/2026-09-05-freshness-reconciliation.md` — R6 and R8.
4. `docs/superpowers/specs/2026-09-05-tracker-evidence-update-design.md` — A14/A15 and retained sensor boundaries.
5. `docs/superpowers/reports/2026-09-06-P00-acceptance.md`

Use executing-plans if available, for this packet only. Do not delegate architecture, spawn workers or change the project model/provider.

## Starting state and isolation

The accepted P00 worktree is:
`C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p00-1788625590422472000/worktree`

Verify its HEAD is the accepted SHA and its tracked tree is clean. Preserve the original checkout and that accepted worktree. Create a new worktree/branch from the explicit accepted SHA:

```powershell
$p00Worktree = 'C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p00-1788625590422472000/worktree'
git -C $p00Worktree status --short
git -C $p00Worktree rev-parse HEAD
$p01Scratch = Join-Path $env:TEMP ('gsn-glm-p01-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $p01Scratch | Out-Null
$p01Worktree = Join-Path $p01Scratch 'worktree'
git -C $p00Worktree worktree add -b codex/tracker-g0-p01 $p01Worktree f808ce3c564d91544219abc8edcac96a58011f14
```

Check exit codes and stop on unexpected state or an existing branch; do not reset/delete it. Read planning files directly from the original project or copy only the needed docs for reading, keeping them unstaged. Use the same audited Python3.12.10 interpreter; do not silently install dependencies.

## Allowed files

- Modify `freshness/known_monitor.py`.
- Create `acquisition/document_validation.py`.
- Create `tests/maintenance/test_p01_monitor.py`.
- Modify `tests/maintenance/fixtures.py` only for reusable valid PDF/test-state builders.
- Modify `test_freshness.py`, `test_freshness_v21.py`, `test_acquisition.py` only to replace deliberately successful malformed PDF fixture bytes with valid equivalent documents and adjust their corresponding expected byte hashes. Retain all existing assertions and intentional invalid-content cases.
- Create `docs/superpowers/reports/P01-worker-report.md`.

Do not change harness tools, P00 regression files, production transport/robots/URL policy, routing, metadata/embedding code or live state. If another source file is needed, return the reason to Codex before broadening scope.

## Fixed implementation decisions

### 1. Dry-run and real comparison use the same baseline

The defect is the `and not self.dry_run` condition guarding the trusted snapshot read in `KnownSourceMonitor.run`. A dry run currently compares without baseline text, so it misses the 1,000-to-100-character collapse that real execution detects.

Read the trusted snapshot for both modes. Dry-run controls persistence only; it must not change comparison inputs or quality thresholds. Keep the existing `classify_capture` function and thresholds unchanged.

Use a fixed clock and injected robots/renderer/transport in tests. Establish a real persisted baseline through `KnownSourceMonitor.run`, snapshot its complete state bytes, run dry mode, assert those bytes are unchanged, then run real mode from that still-identical baseline. This avoids copying state with stale absolute snapshot paths.

### 2. Validate the final destination for PDFs as well as HTML

The known-monitor final-domain gate currently includes browser/static methods only. Apply the existing requested-domain/subdomain rule to every successful verified content method, including PDF, before trusting or storing candidate bytes.

Preserve empty-final_url compatibility for old injected fakes by using the requested URL; real transport already records observed final_url. Do not make empty allowlists or missing validation state a new trust bypass. A nonempty invalid/external final URL must be rejected, with diagnostic status/reason.

This is a post-capture trust check. **P01 does not prevent the external request itself.** Pre-request HTTP/browser redirect enforcement belongs to P02/P03. Do not modify transport to implement those phases now.

### 3. Validate PDF content before acceptance

Introduce this exact public helper:

```python
validate_pdf(body: bytes, content_type: str) -> tuple[bool, str]
```

Use the existing pdfplumber parser with an in-memory byte stream; no new PDF dependency, OCR, rendering, network request or artifact write. Require a PDF signature and a successful structural parse with at least one accessible page. An extension or `%PDF` prefix alone is insufficient. Close parser resources deterministically.

Normalize MIME type case and strip parameters. Permit `application/pdf`, `application/octet-stream`, or absent MIME only when signature and structural checks pass. Reject explicit conflicting HTML MIME, HTML/error bodies, malformed/truncated content that cannot parse, and encrypted/inaccessible documents. A missing parser is a rejected/unavailable-validation outcome with a reason, not automatic acceptance or an installation attempt.

Return `(True, '')` for valid input and `(False, stable_reason)` for rejected input. Distinguish wrong type, invalid signature, parse failure and unavailable parser in diagnostic reasons. Route failure through the existing suspect/failed capture handling without advancing trusted or candidate content.

For rejected live captures, diagnostic metadata may change. Existing trusted hash/method/snapshot bytes and candidate content must not change; no rejected PDF content artifact or new content-change event may be created. For dry runs, all persisted bytes must remain unchanged.

## Required behavioral tests

Write failing tests before the fixes and show the actual failures. A helper-import failure alone is insufficient: demonstrate the old real monitor accepting the forbidden PDF or missing the collapse.

| Case | Required result |
|---|---|
| Real 1,000-character trusted baseline; new 100-character capture | Both dry and real runs classify one suspect collapse; no content-change candidate |
| Same starting state, dry run | All store/event/snapshot/corpus bytes unchanged |
| Normal unchanged and ordinary changed text | Dry and real classification counts agree; existing intended behavior preserved |
| Valid PDF at same-host final URL | Trusted baseline created; hash equals original bytes |
| Valid changed PDF at same-host final URL | Candidate recorded while old trusted bytes/hash remain unchanged |
| Valid PDF with external final URL, no existing baseline | No trusted hash, candidate or PDF artifact; diagnostic outcome |
| External final URL with existing baseline/candidate | Prior trusted/candidate bytes and hashes preserved |
| HTML body served from an allowed `.pdf` URL | Rejected, never trusted |
| Malformed bytes beginning `%PDF` | Rejected by structural validation |
| Structurally valid PDF with explicit text/html MIME | Rejected as conflicting type |
| Valid PDF with octet-stream MIME | Accepted after full validation |
| Encrypted/inaccessible PDF or parser unavailable | Rejected with reason, no state promotion |
| HTML external final URL regression | Existing rejection behavior retained |

Exercise the real `KnownSourceMonitor.run` with temporary corpus/store/snapshot/event paths and injected external boundaries. Do not patch the new validator to always return True in positive tests. Use a small actually parseable PDF fixture and verify its page count through the real parser. Construct two different valid documents for old/new bytes; no new runtime fixture-generation library is needed.

`test_freshness.py` currently uses `b'%PDF-old'` and `b'%PDF-new'` for intended successful PDF monitoring. Replace those with real old/new fixture documents rather than weakening the validator to admit fake PDFs. Preserve the test's original meaning and assertion count. Do not edit P00's legacy-count regression to hide deleted or skipped checks.

Include state preservation assertions that cover the actual paths stored in entries. Do not claim baseline preservation from a summary count alone, and do not assert that the entire live state file is unchanged when a diagnostic status is intentionally recorded.

## Verification, local commit and stop

Run from the P01 worktree:

```powershell
python -B tools/run_offline_checks.py --pattern 'test_p01_*.py'
python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'
git diff --check
```

Require zero ordinary network attempts and no unexplained top-level skips. The nested P00 recursion-prevention skip is already documented. Do not use live govt pages or production corpus/vector directories for these checks.

Write `P01-worker-report.md` before committing. Stage only the explicit allowed changed paths, then create:

```text
fix(tracker): P01 align dry-run comparison and validate known PDFs
```

Verify the exact final commit, including the report:

```powershell
python -B tools/verify_candidate.py --legacy --pattern 'test_p*.py'
```

Use a new corrective commit if needed; no amending predecessor history. Report final SHA, branch/worktree, file changes, red/green evidence per defect, test counts, valid-PDF fixture/parser evidence, state-preservation checks, exported verification and any limitations.

No push, merge, scheduler, tracker implementation, corpus update, embedding or Visa activation. Keep P02/P03 and unrelated R2-R5/R7/R9 work out of this packet.

End with **“P01 complete for review; stopped. P02 has not been started.”** If incomplete, state the exact blocker instead. Codex decides acceptance.
