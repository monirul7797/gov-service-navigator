# Project status and handoff

Written 2026-10-20 after a ~6-week pause (last activity 2026-09-06). Reconstructed from the working
tree, the local git history (including unpushed branches), `docs/superpowers/`, the review bundles
and the GitHub repo. Update this file as decisions are made.

## 1. The owner's goals (in his words, lightly edited)

1. **Complete the dataset manually.** The Visa dataset is incomplete; with Visa it is about 5
   services. Expand to more government services. Automated collection from these sites hasn't
   worked, so data has been collected manually.
2. **Tracker.** Continuously monitor leading government newspapers and government sites for changes
   to those services, notify the admin (me) and rank the changes. Based on the ranking I collect
   the data and hand it to the system, which cleans it up and adds it to the dataset.
3. **A proper public website** where people post queries and find answers about those services.
   A server is ready. I have never built a website, so I need guidance on everything: frontend,
   backend, server, management, security. **Backend in Rust**; everything else to be discussed.
   Advice wanted on a scalable, robust site that needs minimal management staff.
4. Whatever else genuinely makes this a working system.

## 2. What exists today

| Period | Work | Location |
|---|---|---|
| Jul – Aug 17 | Chatbot over NID (102 records), e-Passport (66), e-Return (83), BDRIS (42). Deterministic language detection + Banglish transliteration, rule-based cross-service disambiguation, bge-m3 + ChromaDB retrieval, grounded generation with citations, multi-turn memory, Streamlit UI + CLI, per-service eval harnesses, `test_multiturn.py`. LLM: z.ai free GLM-4.7-Flash. | GitHub (`master` @ `3aaaa0c`) |
| Sep 3 | Build spec (`gsn-freshness-and-new-service-spec.md`) executed by GLM: freshness change-detection pipeline with human review gate; provenance-aware acquisition layer + bounded crawler (`acquisition/`, `crawl.py`); Visa corpus `data/chunks/visa.jsonl` grown 17 → 39 records (Phases 3–3.6b). | Local only |
| Sep 5 | Freshness V2 / V2.1. Independent audit (`freshness_v21_final_review/`): **"not yet safe enough"**, 9 bugs (R1–R9). | Local only |
| Sep 5 | Codex reconciliation + new design: `docs/superpowers/specs/2026-09-05-freshness-reconciliation.md` and `...-tracker-evidence-update-design.md`. Phase plans G0–G3 + control guide in `docs/superpowers/plans/`. | Local only, **untracked** |
| Sep 5–6 | Tracker build, Codex reviewing / GLM implementing in temp worktrees: P00 ✅ `f808ce3`, P01 ✅ `41b8eed`, P02A ✅ `ac2930a`, **P02B implemented but failing, not reviewed** (`33c94b7`). | Local branches `codex/tracker-g0-p00…p02b` |

**Visa is not live**: no `embed_visa.py`, no eval/gold set, not in `ALLOWED_COLLECTIONS` or
`concept_registry.json`. Its 39 records are mostly English (38 `en`, 1 `bn+en`) from
visa.gov.bd / immi.specialbranch.gov.bd / voa.specialbranch.gov.bd.

### Where the tracker build stopped (P02B)

GLM's report (`docs/superpowers/reports/P02B-worker-report.md` on branch
`codex/tracker-g0-p02b`): 7 of 26 new P02B tests fail, 2 P00 tests regress, 2 offline-guard network
attempts. A legacy test (`test_b12b` in `test_acquisition.py`) expects subdomain redirects to be
allowed, which the new exact-host policy denies, and that file was outside GLM's allowed scope.
Needs review and a corrective pass before P03 (browser navigation + robots policy) and the G0 gate.

## 3. Git state and risks (as of 2026-10-20)

- Local `master` = `8d3c83c`, **12 commits ahead** of `origin/master` (`3aaaa0c`). Nothing since
  Aug 17 is on GitHub.
- `codex/tracker-g0-p00 → p01 → p02a → p02b`: linear chain of 10 more commits, local only.
- GitHub has one commit not in local: `ecb0ec4` (Oct 16, README wording "CSE299 …" → "Junior
  Design Project"). Local commits never touch README, so a rebase is clean.
- `docs/superpowers/` (2 specs, 13 plans/prompts, 7 review/acceptance reports) is untracked.
- GLM worktrees live under `C:/Users/Monirul/AppData/Local/Temp/gsn-glm-p0*-…`; Windows cleanup can
  delete them (commits stay safe on the branches; run `git worktree prune` if so). Codex review
  evidence is under `C:/Users/Monirul/.codex/visualizations/…`, outside the repo.
- The Codex reconciliation cites `Govt_Service_Navigator_Ultra_Handoff_2026-09-05.docx`, which is
  not in the project folder.

Suggested backup (explicit paths only):

```powershell
git add docs/superpowers CLAUDE.md docs/PROJECT_STATUS.md
git commit -m "docs: tracker spec, phase plans, reviews; project status and Claude instructions"
git pull --rebase --autostash origin master
git push origin master
git push origin codex/tracker-g0-p02b
```

## 4. How the goals map onto existing plans, and the gaps

| Goal | Existing plan | Gap / conflict |
|---|---|---|
| 1 Dataset | Corpus conventions in the build spec; G2 (P10–P14) = evidence intake, extraction (screenshots, PDF, DOCX, XLSX, OCR), comparison and exact patch proposals | Visa incomplete; Visa publishing deliberately gated (G5). No plan yet for adding services 6+ |
| 2 Tracker | G1 (P04–P09) = curated official + news sources, event grouping, priority ranking, alert inbox, source health. G3 (P15–P20) = approved, versioned publication with rollback | Spec excludes external messaging (in-app inbox only); the owner wants to be notified. Spec assumes a **single laptop** |
| 3 Public website | **Not covered.** Spec lists public hosting as an exclusion; operator UI is a separate Streamlit app; storage is local SQLite | Needs a whole-system architecture: public site, answer engine, tracker workers, admin app, on the owner's server |
| 4 Working system | — | See ideas in §6 |

## 5. Open decisions (to settle at the start of the next session)

1. **Re-scope the plan.** The website changes deployment assumptions behind G1–G3. Decide whether
   to keep the G0 safety work (likely yes: crawler safety is needed regardless), then redesign
   G1+ around the server deployment.
2. **Rust boundary.** Rust for the public API layer (auth, rate limiting, sessions, serving the
   frontend) is a good fit. Open question: keep the working Python RAG pipeline (normalization,
   disambiguation, bge-m3, ChromaDB) as an internal service, or port parts to Rust later.
3. **LLM for public traffic.** The free GLM-4.7-Flash tier (~1 req/s, ~1000/day) is fine for demos,
   not for a public site. Needs a provider and a monthly budget.
4. **Workflow.** Continue the Codex (architect/reviewer) + GLM (worker) loop, switch to Claude Code,
   or combine them — but keep one source of truth for the plan.
5. **Notifications.** Which channel(s) for admin alerts (email, Telegram, etc.).

### Decided 2026-10-20 (architecture session)

- **Backup done:** `master` (rebased onto `ecb0ec4`, now `dde8808`) and `codex/tracker-g0-p02b`
  pushed to GitHub. The rebase gave old master commits new IDs (`8d3c83c` → `751cfad`); the
  `codex/tracker-g0-*` chain still sits on the pre-rebase copies.
- **Deployment path:** build and test everything locally (Windows) + GitHub; deploy only when the
  system works. First target is an OVH **test** server (Singapore, Debian, public IP) for a pilot
  with 50–100 invited users; a production-sized server comes later, so nothing may be tied to the
  test machine.
- **LLM (decision 3):** DeepSeek API (owner's pick: "DeepSeek-V4.1-Flash-1017"; verify model ID and
  pricing against DeepSeek docs when wiring). No locally hosted LLM.
- **Workflow (decision 4):** Claude Code. Opus 5.5 (max effort) plans, orchestrates and reviews;
  it sends small instruction packets to Sonnet 5.5 (xhigh/max) subagents that write code and
  report back; Opus reviews each report before the next packet. Replaces the Codex/GLM loop.
- **Public accounts:** none for the pilot (anonymous use, rate limits, invite gate). Design so
  required accounts can be added later. Admin side still needs a secure login.
- **Notifications (decision 5):** tracker alerts go to the admin by **email and Telegram**
  (overrides the old spec's in-app-inbox-only exclusion).
- **Admin/data work runs on the server** (owner works remotely, not always at the PC): tracker,
  evidence upload, cleaning/OCR, review, embedding and publishing all server-side behind an admin
  web panel. This reverses the old single-laptop assumption of G1–G3.
- **Everything is recorded:** append-only audit log of every admin/system action, and full version
  history of every dataset record (never edited in place), linked to its evidence.
- **Database:** PostgreSQL (multiple concurrent writers: Rust site, tracker, workers). Published
  releases are also copied to GitHub as an off-server backup.
- **Admin access:** admin panel and SSH reachable only over a Tailscale private network, plus
  password + 2FA/passkey login. Not exposed on the public internet.
- **Test server resources:** 32 GB RAM, ample CPU; no GPU assumed.
- **Architecture (decision 2):** approach A — Rust `web` service at the edge (public site, API,
  rate limits, sessions, PII scrub, admin panel, auth, audit), Python `engine` (existing RAG) and
  Python `worker` (tracker, OCR, embedding, releases, notifications) behind an internal API;
  PostgreSQL + pgvector replaces ChromaDB (only if all four evals match); Docker Compose; Caddy for
  HTTPS. **Long-term goal: the whole backend in Rust** (engine and worker ported piece by piece
  behind the same contracts). The frontend is not part of that goal.
- **Frontend:** SvelteKit (static build) + TypeScript + Tailwind CSS, talking to the Rust JSON API
  (answers streamed via Server-Sent Events). Admin panel is a separate frontend app served only on
  Tailscale.
- **Stack critique accepted (2026-10-20):**
  1. Deploy a thin vertical slice to the test server early and grow it there (no big-bang deploy).
  2. Three phases: **Phase 1 pilot** = public site + answer API + feedback + minimal admin (invites,
     feedback inbox, query logs) + script-published data releases; **Phase 2** = tracker + email/
     Telegram alerts; **Phase 3** = evidence upload/OCR/review in the admin panel.
  3. Deterministic answer verification: every number, fee, date and URL in a generated answer must
     appear in its cited records, else block/flag; plus a generation eval set (bn + en).
  4. Fallback LLM provider + "sources-only" answer mode when no LLM is available.
  5. Rate limits primarily per session / invite code (Bangladeshi mobile CGNAT); loose per-IP
     flood limits; daily LLM budget cap as backstop.
  Also: max five containers (web, engine, worker, postgres, caddy); pin Svelte 5 + lint against
  legacy syntax; document OVH KVM/rescue console as break-glass if Tailscale fails; encrypted
  off-provider DB backups with a practised restore (GitHub only gets public release data); the old
  G1–G3 machinery (lease fencing, CAS protocols) is dropped, G0 crawler-safety work is salvaged.
### Decided 2026-10-21

- **LLM:** `deepseek-flash` (= DeepSeek-V4.1-Flash-1017) with thinking **disabled**
  (`setenv_deepseek.bat`, gitignored). GLM is not the primary model. Evidence:
  `docs/reports/2026-10-21-llm-and-conversation-test.md`.
- **Routing change approved:** "LLM understands the question, code checks the answer" replaces the
  deterministic routing rule, kept only if it beats the current routing on the conversation
  suite, the four evals and `test_multiturn.py`. Design:
  `docs/superpowers/specs/2026-10-21-llm-query-understanding-design.md`. Fix code problems now,
  before M0.
- **Dev machine:** Rust 1.99.1 installed; Docker Desktop installed on `G:\Docker` (C: has ~4 GB
  free). No MSVC Build Tools: Rust compiles and tests inside Docker.

- **Architecture spec:** `docs/superpowers/specs/2026-10-20-system-architecture-design.md`
  (draft for owner review). Phase 1 milestones M0–M7; each milestone gets its own plan.

### Information needed from the owner

- Server specs: provider/location, OS, CPU, RAM, GPU (if any), disk, bandwidth.
- Monthly budget (LLM API, domain, backups).
- Domain name (owned or to buy).
- Expected users and whether public users need accounts.
- Who operates it (just the owner?).

## 6. Ideas for goal 4 (suggestions, not decisions)

- Feedback on every answer ("wrong / outdated") that feeds the tracker inbox.
- Log unanswered and low-confidence questions to show which services or topics to collect next.
- Show "source last verified on <date>" with every answer.
- Scrub personal data (NID, phone, passport numbers) from query logs; publish a privacy policy.
- Clear "unofficial, independent service" labelling; no government logos or look-alike branding.
- Mobile-first, low-bandwidth, Bangla-first UI.
- Use the eval suites + `test_multiturn.py` as a release gate for every data or code change.
- Staging and production environments, automated backups, uptime monitoring, rate limiting.
