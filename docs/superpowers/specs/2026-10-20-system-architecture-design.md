# Government Service Navigator — System Architecture Design

- **Date:** 2026-10-20
- **Status:** Draft for owner review
- **Scope:** Whole-system architecture. Phase 1 (pilot) is specified in detail; Phase 2 (tracker),
  Phase 3 (evidence workflow) and the Rust migration are specified at the level of boundaries and
  order only — each gets its own spec later.
- **Supersedes:** the deployment assumptions (single laptop, local SQLite, Streamlit operator UI,
  in-app-only alerts, no public hosting) of
  `2026-09-05-tracker-evidence-update-design.md` and plans G1–G3. That spec's data-integrity and
  crawler-safety rules remain in force.
- **Decision log:** `docs/PROJECT_STATUS.md` §5 "Decided 2026-10-20".

---

## 1. Goals

1. A public website where people ask about Bangladeshi government services in Bangla, English or
   Banglish and get grounded, cited, fact-checked answers.
2. A safe way for one admin, working remotely, to operate the system, publish data changes and
   roll them back.
3. (Phase 2) A tracker that watches official sites and newspapers, ranks changes and alerts the
   admin by email and Telegram.
4. (Phase 3) An evidence workflow: upload screenshots/PDFs, extract, compare, approve, publish.
5. Low maintenance for a single operator; everything recorded; easy to move to a bigger server.
6. Long term, the whole **backend** in Rust (the frontend is not part of that goal).

### Success criteria for Phase 1 (the pilot)

- 50–100 invited users can use the site on budget Android phones over slow mobile data.
- Every answer is either fact-checked against its cited records or clearly marked sources-only.
- The admin can see feedback, flagged answers and unanswered questions, and can roll back a data
  release, from a phone, without the development PC.
- Backups are encrypted, off-provider, and restored automatically every week.
- The admin surface is unreachable from the public internet (verified from outside).

### Non-goals (deliberately excluded; may come later on demonstrated need)

Public user accounts, mobile apps, Kubernetes, multiple servers, Redis or a message broker,
Cloudflare during the pilot, multiple admin roles, a public Telegram bot, locally hosted LLMs.

---

## 2. Constraints and context

- **Owner:** one person, new to web development, servers and security. Works remotely; does not
  always have the development PC.
- **Development:** Windows PC + GitHub. **Test server:** OVH, Singapore, Debian, public IP, 32 GB
  RAM, ample CPU, no GPU. Used for the pilot, later as staging; production moves to another server,
  so nothing may depend on this machine.
- **Existing assets:** Python RAG pipeline (`answer.py::handle_turn`, `normalize_query.py`,
  `disambiguate.py`, `concept_registry.json`, `llm_backends.py`), corpora in `data/chunks/*.jsonl`
  (NID 102, e-Passport 66, e-Return 83, BDRIS 42 live; Visa 39 not live), per-service eval
  harnesses and gold queries, `test_multiturn.py`.
- **LLM:** DeepSeek API (owner's choice; model named "DeepSeek-V4.1-Flash-1017" — the exact model
  ID, pricing and data-use terms must be verified against DeepSeek's documentation in M2). No
  local LLM.
- **Project data-integrity rules** (`CLAUDE.md`) apply to every component: never invent URLs, fees,
  dates or documents; Bangla source text authoritative and verbatim; fees VAT-inclusive; numerals
  per answer language; portal URLs for "how to apply"; no evasion of site blocks; dedup before
  adding; LLM understanding with code-checked fact boundaries (see
  `2026-10-21-llm-query-understanding-design.md`).

---

## 3. Architecture overview

Approach A: **Rust at the edge, Python inside**, joined by a documented, language-neutral
internal API. Five containers under Docker Compose, identical on the PC and on servers.

```
            Internet                                   Tailscale (private network)
               │                                                │
        ┌──────▼───────┐                                        │
        │    caddy     │  :80/:443 public, automatic HTTPS      │
        └──────┬───────┘                                        │
               │                                                │
        ┌──────▼──────────────────────────────┐                 │
        │  web  (Rust)                         │                │
        │  public listener: JSON API + static  │                │
        │     public frontend (SvelteKit)      │                │
        │  admin listener (Tailscale IP only): │◄───────────────┘
        │     admin API + admin frontend       │
        │  sessions, invites, rate limits,     │
        │  budget cap, PII scrub, logging,     │
        │  admin auth (password+passkey/TOTP), │
        │  audit log writes                    │
        └──────┬─────────────────────┬────────┘
   internal    │ HTTP (SSE)          │ SQL (read/write, web role)
        ┌──────▼────────┐     ┌──────▼──────────────────────────────┐
        │ engine (Py)   │ SQL │ postgres (+pgvector)                │
        │ /v1/turn      │────►│ records, versions, releases, audit, │
        │ normalize,    │ RO  │ conversations, feedback, invites,   │
        │ route, embed  │     │ usage, notifications outbox, jobs   │
        │ query, retrieve│    └──────▲──────────────────────────────┘
        │ LLM, fact-check│           │ SQL (worker role)
        └───────────────┘     ┌──────┴──────────────────────────────┐
                              │ worker (Py, same image as engine)   │
                              │ gsn CLI: release build/publish/     │
                              │   rollback; notification sender;    │
                              │   backups + weekly restore test;    │
                              │   retention cleanup                 │
                              │ (Phase 2: tracker; Phase 3: OCR,    │
                              │   extraction, patch proposals)      │
                              └─────────────────────────────────────┘
```

| Container | Language | Reachable from | Phase 1 responsibility |
|---|---|---|---|
| `caddy` | — | Internet (80/443) | TLS (Let's Encrypt), reverse proxy to `web`'s public listener |
| `web` | Rust | caddy (public listener); Tailscale IP (admin listener) | Everything user- or admin-facing; the only writer of sessions, logs, feedback, invites, audit (for web actions) |
| `engine` | Python | `web` only (Docker internal network) | Stateless answer pipeline; read-only DB access |
| `worker` | Python | nothing inbound | Release CLI, notification sender, backups, restore tests, retention |
| `postgres` | — | the three services only | All persistent state |

Technology choices: Rust — axum/tokio, sqlx (final crate choices fixed in the M0/M3 plans).
Python — FastAPI, psycopg 3, pgvector, sentence-transformers (bge-m3). Frontend — SvelteKit
(static adapter), **Svelte 5 pinned** with lint rules rejecting legacy Svelte 4 syntax,
TypeScript, Tailwind CSS. Database — PostgreSQL with the pgvector extension.

---

## 4. Public site (Phase 1)

### 4.1 Frontend

- SvelteKit built as static files and served by `web`. Public pages (home, about, privacy,
  contact) are prerendered; the conversation view is client-side and talks to the JSON API.
- **Bangla-first** UI with an English toggle; UI strings in translation files. Answers follow the
  question's language (existing engine behaviour). Noto Sans Bengali self-hosted (no third-party
  font requests).
- Mobile-first, low-bandwidth (bundle budget set in M5), accessible (keyboard, screen-reader
  labels, contrast), dark mode.
- Pages: home (question box, covered services, example questions in bn/en/Banglish),
  conversation view, about ("independent, unofficial; always confirm with the official site"),
  privacy notice, contact. No government logos or look-alike branding.
- Each answer shows: text, citations (source link, portal vs info-page label, "last verified on
  <date>" only when a verified date exists — §6.3), feedback controls (👍 / 👎, "wrong",
  "outdated", optional note). Clarification turns render the offered services as buttons.
- While an answer is prepared, the UI shows progress stages streamed from the server
  ("Searching official sources…", "Writing answer…", "Checking facts…"). **The answer text itself
  is not streamed**: it is shown only after the fact check (§5.3).

### 4.2 Sessions and the pilot gate

- Anonymous session: random ID in a `Secure; HttpOnly; SameSite=Lax` cookie; conversation state
  stored in PostgreSQL under that ID. `conversations.user_id` exists and is null, so accounts can be
  attached later without redesign. State-changing API requests also require a matching `Origin`
  header (cross-site request protection).
- **Invite gate** (switchable off for public launch): an invite link carries a code; a valid code
  marks the browser session as admitted. Codes have a label, optional use limit, and can be
  revoked. Usage is reported per code.

### 4.3 Abuse and cost protection

- Rate limits **primarily per session and per invite code** (initial: 20 questions/hour/session;
  tunable in admin Settings). Per-IP limits are loose flood protection only, because Bangladeshi
  mobile carriers place many users behind shared addresses (CGNAT).
- **Daily LLM budget cap**: `web` tracks token usage reported by the engine; when the cap is
  reached the site answers "busy, please try again tomorrow" without calling the engine. Alerts at
  80% and 100%.
- **Maintenance switch** (admin Settings): site shows a notice; no engine calls.

### 4.4 Personal-data scrubbing

- Applied in `web` to every question **before** it is logged or sent to the engine (and hence the
  LLM). Replaces likely NID numbers (10/13/17 digits), Bangladeshi phone numbers (`01…`, `+880…`,
  with spaces/dashes), passport numbers and email addresses with placeholders (`[NID]`, `[PHONE]`,
  `[PASSPORT]`, `[EMAIL]`), in both Western and Bengali digits (০–৯).
- Must **not** alter fees (`৳৫০০০`, `5000 টাকা`), years, durations (`৭ কার্যদিবস`) or other
  service figures. Both directions are covered by fixtures (§10).
- The question box warns users not to type personal numbers. The privacy notice states that
  questions are processed by an external AI provider after scrubbing.

### 4.5 Logging

Per turn, `web` stores: scrubbed question, detected language, result kind
(answer/clarify/decline/sources_only), routed service(s), cited record version IDs, verification
outcome, LLM provider and token counts, timings, release ID, feedback (if given). Declines feed the
"unanswered questions" report.

---

## 5. Answer engine (Phase 1)

### 5.1 Placement

- Python FastAPI service wrapping the existing `handle_turn` orchestration.
- **Stateless**: conversation state comes in with each request and goes back out.
- **Read-only** database role: can read published records, versions, releases and the active
  release pointer; nothing else.
- Loads bge-m3 once at start-up. Reads the active release pointer per request (one indexed row),
  so publish/rollback take effect immediately without restarts. The concept registry is loaded
  from the active release's snapshot and cached by release ID.

### 5.2 Contract: `POST /v1/turn`

Request (JSON):

| Field | Type | Notes |
|---|---|---|
| `request_id` | string | Correlates logs across services |
| `question` | string | Already scrubbed by `web` |
| `state` | object | Opaque to `web`; returned by the previous turn (empty for a new conversation). Must be JSON-serialisable (today's tuples become arrays). |

Response: a Server-Sent Events stream.

- `stage` events: `understanding`, `searching`, `writing`, `checking` (for the progress UI).
- One final `result` event:

| Field | Notes |
|---|---|
| `kind` | `answer` \| `clarify` \| `decline` \| `sources_only` |
| `text` | Answer, clarification question, decline message or sources-only summary |
| `options` | For `clarify`: offered services (rendered as buttons) |
| `citations` | `[{record_id, version_id, url, url_kind, verified_at}]` |
| `verification` | `{status: passed \| retried_passed \| failed \| not_applicable, problems: [...]}` |
| `llm` | `{provider, model, calls, tokens_in, tokens_out}` (all LLM calls in the turn, including the understanding call) |
| `release_id` | Release the answer was produced from |
| `timings` | Per stage, ms |
| `new_state` | State to send with the next turn |

Errors: an `error` event with a code (`engine_unavailable`, `timeout`, `bad_request`); `web` shows
a friendly message and logs it. The contract lives in `contracts/` as a written description plus
example request/response JSON files used by tests on both sides (§10).

### 5.3 Fact check (deterministic)

After generation, before anything is shown:

1. Extract from the answer every number, money amount (৳, Tk, BDT, টাকা), date, duration and URL.
   Bengali and Western numerals are normalised to one form for comparison.
2. Each extracted figure must appear in the cited records' text (any language field), or in the
   user's own question. Ordered-list markers (`1.`, `২)`) are exempt.
3. Each URL must exactly match (after trivial normalisation such as a trailing slash) a URL of a
   cited record.
4. On failure: one retry with a stricter instruction. If it still fails, return `sources_only`,
   and flag the turn for the admin's "Flagged answers" view.

### 5.4 LLM providers and failure modes

- Primary: DeepSeek via the existing OpenAI-compatible backend in `llm_backends.py`. A second
  provider is configured as fallback (choice open — §13).
- Per-call timeout and a circuit breaker per provider (after repeated failures, skip that provider
  for a cool-down period).
- If no provider is available: `sources_only` — the top matching records' headings, Bangla text
  verbatim (with English where present, labelled as translation) and their links, plus a short
  fixed note.
- Routing is one LLM understanding call (`understand.py`, design
  `2026-10-21-llm-query-understanding-design.md`): it classifies the turn and writes Bangla and
  English search queries; code validates the output and filters model-written replies and
  clarifying questions. If the call fails, the registry router (`disambiguate.py` +
  `concept_registry.json`) is the fallback. Adopted 2026-10-21 on the evidence in
  `docs/reports/2026-10-21-llm-and-conversation-test.md` §9.

### 5.5 Retrieval

- Records come from PostgreSQL/pgvector, filtered to the active release, replacing the ChromaDB
  per-service collections. `ALLOWED_COLLECTIONS` becomes "services that are `live` in the active
  release".
- Retrieval parameters (`MAX_CONTEXT`, per-service k, grounding floor) are carried over unchanged
  and must reproduce current eval results before ChromaDB is retired (M1 gate).

---

## 6. Data model, versions and releases

### 6.1 Tables

| Table | Contents |
|---|---|
| `services` | `service_id`, names (bn/en), `status` (`live` / `hidden`). Visa starts `hidden`. |
| `records` | Stable identity: `record_id` (existing corpus IDs), `service_id`. |
| `record_versions` | **Immutable.** `version_id`, `record_id`, common fields (category, heading, `embed_text` — exactly today's embedded text, `text_bn` verbatim, `text_en` labelled translation, `lang`, `source_url`, `url_kind`, `official`, `source_verified` (from `source_url_unverified`), `verified_at` nullable), `extra` (JSONB: every other original field, so nothing is lost), `content_hash`, `embedding` (vector), `embedding_model`, `created_at`, `created_by`, `change_note`, `source_commit` (git commit the content came from). |
| `releases` | `release_id`, status (`draft → validated → published → retired`), created by/at, note, `source_commit`, registry snapshot (JSONB copy of `concept_registry.json`), eval results (JSONB), thresholds used. |
| `release_members` | (`release_id`, `version_id`). |
| `active_release` | Single row: the live `release_id`. Publish and rollback are one transactional update of this row. |
| `audit_log` | Append-only: `at`, `actor` (admin user or system component), `action`, `target`, `details` (JSONB). No role has UPDATE or DELETE on it. |
| `conversations`, `messages` | Session ID, nullable `user_id`, timestamps; scrubbed turns and per-turn metadata (§4.5). |
| `feedback` | Turn reference, rating/kind, note, triage status (`new / investigating / fixed / dismissed`), admin note, fixing `release_id`. |
| `invite_codes` | Code (stored hashed), label, use limit, uses, revoked flag. |
| `usage_daily` | Per-day token and cost totals, per provider. |
| `settings` | Budget cap, rate limits, maintenance switch, invite-gate switch. |
| `admin_users`, `admin_credentials`, `admin_sessions` | Argon2id password hash, passkeys (WebAuthn), TOTP secret, hashed recovery codes, sessions. |
| `notifications` | Outbox: severity, channels, payload, per-channel status, attempts, next attempt, dedup key. |

Database roles: `web` (read/write on its own tables; on `audit_log` SELECT for the admin panel and
INSERT of `actor, action, target, details` only — `id` and `at` are set by the database), `engine`
(read-only on published data), `worker` (release and notification tables; column-level INSERT on
`audit_log`, no SELECT), and a migration role used only by the deploy step. `audit_log` rejects
UPDATE, DELETE and TRUNCATE for every role including its owner (as built in M0, migration
`0001_audit_log.sql`); M1 adds protection against the owner dropping or replacing those triggers
(superuser-owned event trigger) and a hash chain for tamper evidence.

### 6.2 Source of truth, by phase

- **Phase 1:** `data/chunks/*.jsonl` and `concept_registry.json` in git remain where content is
  authored. `gsn release build` imports a given git commit's content as new versions; unchanged
  records (same `content_hash`) reuse their existing version. The database is the history of what
  was built and published.
- **Phase 3:** authoring moves to the admin panel; the database becomes the source of truth; each
  published release is exported to JSONL and committed to GitHub (off-server copy of public data).

Never both at once.

### 6.3 `verified_at`

Only set from real evidence: a later admin re-check, or an existing field that records when the
source itself was read (candidates: `fetched_at`, `last_crawled`). The M1 plan lists, per corpus
file, which field (if any) maps to `verified_at`, and the owner confirms that mapping before the
first release. A record's `last_updated` (when *we* edited it) is not a verification date. Never
back-filled with guesses. Records without it
show the source link without a date. The list of records lacking `verified_at` is a re-check
to-do list.

### 6.4 Release CLI (Phase 1, run in the `worker` container over SSH via Tailscale)

```
gsn release build [--commit <sha>]   import content from that commit of the GitHub repo
                                     (read-only clone on the server), validate, embed only
                                     changed records, create a draft, run all gates (§10.2),
                                     mark validated or report failures
gsn release publish <release_id>     only if validated; switch active_release; audit-logged;
                                     notification sent
gsn release rollback                 switch back to the previously published release
gsn release list | show <id>         status, eval results, differences from the live release
```

Validation includes: schema check; data-integrity rules (no `TODO(verify)` records, source URL
present, Bangla kept verbatim where the source is Bangla, dedup against the release); all gates in
§10.2. The admin panel lists releases and offers **Rollback** (publish stays on the CLI in
Phase 1).

### 6.5 Embedding model changes

A new embedding model (or a Rust/ONNX re-implementation) means re-embedding every record into a
new release. Vectors carry `embedding_model`; retrieval never mixes models.

### 6.6 ChromaDB migration

The first release is built from today's JSONL. All four retrieval evals and `test_multiturn.py`
must match the current ChromaDB results before the engine switches; ChromaDB and
`data/vectordb/` are retired only after that.

---

## 7. Admin panel and system alerts (Phase 1)

### 7.1 Access

1. Separate SvelteKit app, served by `web` on a second listener bound only to the server's
   Tailscale address. The public listener has no admin routes.
2. HTTPS inside the tailnet with a Tailscale-issued certificate for the server's private name
   (passkeys require HTTPS).
3. Login: password (Argon2id) **plus** passkey (preferred) or TOTP code; ten one-time recovery
   codes kept offline. Admin accounts are created only by a CLI command on the server.
4. Sessions: secure cookie, 12-hour expiry, `SameSite=Strict`, CSRF protection on state-changing
   requests. Re-authentication (passkey/TOTP) required for rollback, invite creation and settings
   changes if not done in the last 10 minutes. Failed logins back off, then lock.
5. Every admin action is written to `audit_log`.

### 7.2 Screens

Dashboard (volume, result kinds, fact-check failures, spend vs budget, errors, latency) ·
Feedback inbox (triage with notes; link fixes to releases) · Flagged answers · Unanswered
questions (declines grouped by likely service/topic) · Question log (search/filter, scrubbed) ·
Invites (create/label/limit/revoke, usage) · Releases (list, eval results, live marker,
Rollback) · Settings (budget cap, rate limits, maintenance switch, invite gate) · Audit log
(read-only).

### 7.3 System alerts (email + Telegram)

- `worker` sends from the `notifications` outbox: retried until each channel confirms; dedup keys
  prevent repeats. Telegram via a bot to the admin's private chat; email via a transactional email
  provider (choice open — §13), never directly from the server's IP.
- Phase 1 alerts: budget 80% / 100%, engine or web unhealthy, fact-check failure spike, backup or
  restore-test failure, disk nearly full, release published / rolled back.
- Severity routing: urgent → both channels immediately; informational → daily email digest.
- Phase 2's tracker reuses this sender unchanged.

---

## 8. Deployment and operations

### 8.1 Environments

- **Local (PC):** Docker Desktop; same Compose file; a fake LLM backend so tests cost nothing.
- **Test server (OVH):** pilot; becomes **staging** when the production server arrives.
- **Production:** future server; same Compose file and deploy script.

### 8.2 Build and deploy

1. Push to GitHub → GitHub Actions runs the gates for code (§10.2), builds images, pushes them
   to GitHub Container Registry tagged with the commit SHA.
2. On the server, `deploy/deploy.sh <sha>`: pull images → take a database dump if migrations are
   pending → run migrations → restart services → health checks + one real smoke-test question →
   on failure, automatically restore the previous image tag.
3. The server never compiles code. The bge-m3 model is downloaded once into a dedicated volume,
   pinned to an exact revision and verified by hash.

### 8.3 Network exposure

| Port | Reachable from |
|---|---|
| 80/443 (`caddy`) | Internet |
| Admin listener, SSH | Tailscale only |
| `engine`, `worker`, `postgres` | Docker internal network only (not published) |

Docker publishes ports by writing firewall rules that bypass ufw; therefore only `caddy` is
published on all interfaces, the admin listener is published on the Tailscale address only, and
nothing else is published. The deploy step verifies exposure from outside the server.

### 8.4 Server hardening (Debian)

Personal admin user with sudo; root SSH login disabled; SSH keys only; SSH closed on the public
interface once Tailscale access is confirmed (performed with the OVH KVM/rescue console available
as break-glass, documented in a runbook); firewall allowing only 80/443 publicly; automatic
security updates; time sync; Docker log rotation.

### 8.5 Secrets

One `.env` on the server (mode 600, never in git) holding the DeepSeek and fallback LLM keys,
email-provider key, Telegram bot token and database passwords. Locally, a gitignored `.env`
replaces `setenv.bat`. GitHub secret scanning enabled.

### 8.6 HTTPS and headers

A domain name pointing at the server is required by M4. Caddy obtains Let's Encrypt certificates
automatically. Headers: HSTS, strict Content-Security-Policy (no inline scripts), `frame-ancestors
'none'`, `X-Content-Type-Options: nosniff`, minimal `Referrer-Policy`. Cloudflare is reconsidered
at public launch.

### 8.7 Backups

- Nightly `pg_dump`, compressed and encrypted to a public key whose private key the owner keeps
  offline; uploaded to object storage at a different provider (choice open — §13).
- Retention: 7 daily, 4 weekly, 6 monthly.
- **Weekly automated restore test:** latest backup restored into a temporary database on the same
  PostgreSQL server, sanity-checked (row counts, active release present), then dropped. Failure
  alerts.
- Database dumps never go to GitHub.

### 8.8 Monitoring

External uptime checker on the public site (every minute) → Telegram/email. Heartbeat checks for
the backup and restore-test jobs (a job that silently stops is detected). Internal alerts per §7.3.
Structured JSON logs (already scrubbed), rotated.

### 8.9 Dependency hygiene

Dependabot updates; `cargo audit`, `pip-audit`, `npm audit` in CI.

### 8.10 Data retention (initial values, confirmed with the privacy notice in M7)

Conversation state: deleted after 30 days of inactivity. Scrubbed per-turn logs and feedback: 12
months. Audit log: kept indefinitely. Backups: per §8.7.

---

## 9. Request and release flows

**A question:** browser → caddy → `web` (session/invite check → maintenance/budget check → rate
limit → scrub → load state) → `engine /v1/turn` (route → retrieve from active release → generate
→ fact check → retry/fallback) → progress events relayed to browser → final result → `web` logs
turn, updates usage and state → browser renders answer, citations, feedback controls.

**A data change (Phase 1):** owner edits JSONL/registry (PC or GitHub web editor) → push → on the
server `gsn release build --commit <sha>` → gates → `validated` → `gsn release publish <id>` →
pointer switch → audit + notification → engine serves new release on the next request. Problem →
admin panel **Rollback** (or `gsn release rollback`).

**A code change:** push → CI gates → images → `deploy.sh <sha>` → migrations, restart, smoke test
→ automatic revert on failure.

---

## 10. Testing and release gates

### 10.1 Test layers

| Layer | Purpose | Tooling |
|---|---|---|
| Unit | Function-level correctness | `cargo test`, pytest, Vitest |
| Contract | `web` and `engine` agree on `/v1/turn`; both tested against the same JSON examples in `contracts/` (and any future Rust port must pass them) | Rust + Python |
| Behaviour fixtures | Routing (`test_multiturn.py` cases as JSON), retrieval (existing gold queries with per-service thresholds), PII scrubber (must-scrub and must-not-scrub cases, Western and Bengali digits) | Language-neutral JSON |
| Generation eval | 60–100 bn/en/Banglish questions with owner-checked expected facts; measures fact-check pass rate, citation correctness, answer language, numeral style; used to compare providers; includes the 40-turn conversation suite (`conversation_cases.json`) and the 7-turn held-out set (`conversation_cases_heldout.json`) scored by `eval_conversations.py` | Real LLM |
| End-to-end | Browser flows: ask → verified answer with citation; invite gate; rate limits; admin routes absent on the public listener | Playwright, fake LLM |
| Security | Secret scanning, dependency audits, header checks, external exposure check | CI + deploy step |
| Load (before pilot) | 100 concurrent users | k6 |

### 10.2 Gates

1. **Every push:** unit, contract, routing fixtures, scrubber fixtures, end-to-end (fake LLM),
   security checks, retrieval evals (model cached in CI; moved to a nightly staging job if too slow
   on free CI runners).
2. **Every data release:** integrity validation, retrieval evals, routing fixtures, generation
   eval. `gsn release publish` refuses releases below thresholds. Thresholds start at measured
   current scores; lowering one requires an explicit override with a written reason, recorded in
   `audit_log`.
3. **Every deploy:** health checks + real smoke-test question; automatic revert on failure.

### 10.3 Existing tests

Converted and kept: `test_multiturn.py`, `eval_nid.py`, `eval_epassport.py`, `eval_ereturn.py`,
`eval_bdris.py`, normalisation tests. Parked until Phase 2 (not deleted): `test_freshness.py`,
`test_freshness_v21.py`, `test_acquisition.py`, and the P00–P02B tests on the
`codex/tracker-g0-*` branches.

### 10.4 Implementation workflow discipline

Opus 5.5 plans, orchestrates and reviews; Sonnet 5.5 subagents implement small packets. Each
packet names its allowed files and comes with failing tests first. Opus runs the tests itself on
review. A packet that needs a file outside its scope stops and reports instead of working around
the restriction.

---

## 11. Phases and milestones

### Phase 1 — pilot

| # | Milestone | Done when |
|---|---|---|
| M0 | Foundations: monorepo layout (`web/`, `engine/` Python package, `frontend/public/`, `frontend/admin/`, `contracts/`, `deploy/`), Compose skeleton, migrations, CI. Legacy root scripts keep working until replaced. | `docker compose up` runs an empty stack locally; CI green |
| M1 | Data in PostgreSQL: schema, pgvector, `gsn release` CLI, ChromaDB parity | All four evals + `test_multiturn.py` match ChromaDB results |
| M2 | Engine service: `/v1/turn`, fact check, failover, sources-only, DeepSeek wired (model ID/pricing/terms verified) | Contract + routing fixtures pass; fact check catches planted errors |
| M3 | Rust `web`: public API, sessions, invites, rate limits, budget, scrubber, logging, progress relay; plain test page | End-to-end: question in, verified answer out |
| M4 | Thin slice on the OVH server: domain, Caddy, Tailscale, hardening, deploy script, backups + restore test, monitoring; owner-only use | Live over HTTPS; restore test passes; admin unreachable from the internet (verified externally) |
| M5 | Public frontend (SvelteKit) with a full design pass | Usable on a budget Android phone over slow data; accessibility checks pass |
| M6 | Admin panel + system alerts | Pilot operable from a phone |
| M7 | Pilot readiness: generation eval, load test, privacy notice, about page, retention confirmed, checklist | Invites sent to 50–100 people |

Each milestone gets its own implementation plan. Data work (completing Visa, adding services)
proceeds in parallel once M1 exists: corpus → gold queries → registry entry → release.

### Phase 2 — tracker (own spec, written during the pilot)

Curated official and newspaper sources, each with a reachability setting (server-fetchable vs
manual-only, since some Bangladeshi sites block foreign IPs — never evaded); change detection;
ranking; alerts in the admin panel plus email/Telegram via the §7.3 sender. Decision at the start
of Phase 2: finish and correct P02B, or fold the G0 crawler-safety rules (exact-host policy,
offline test guard, provenance records) into the new design. The G1–G3 lease-fencing and
compare-and-swap machinery is not carried forward.

### Phase 3 — evidence workflow (own spec)

Upload screenshots/PDFs; OCR (Bangla OCR output is always a draft checked against the image);
extraction into proposed record versions; before/after review; approval; Publish button. The
database becomes the content source of truth; each release is exported to GitHub.

---

## 12. Rust migration path (backend only; starts after the pilot is stable)

Order: notification sender → normalisation and transliteration → router (disambiguation +
registry) → retrieval, LLM orchestration and fact check (bge-m3 via ONNX; must hold eval
thresholds) → tracker → OCR/extraction last, or never if Python remains adequate.

Each Rust replacement implements the same contract and fixtures, then runs in **shadow mode**:
real traffic goes to both implementations, only the Python result is used, disagreements are
logged. Python is retired for that piece only after a sustained period of agreement. Pieces are
ported when there is a reason, not for completeness.

---

## 13. Open items (needed before the milestone shown)

| Item | Needed by |
|---|---|
| DeepSeek model ID, pricing and data-use terms verified | M2 |
| Fallback LLM provider | M2 |
| Monthly budget and daily cap value | M3 |
| Domain name | M4 |
| Transactional email provider | M4 (system alerts) |
| Backup storage provider | M4 |
| Uptime-monitoring service | M4 |
| Retention periods confirmed; privacy notice text | M7 |
| Bangladesh data-protection requirements checked | Before public (non-invite) launch |

---

## 14. Risks

| Risk | Mitigation |
|---|---|
| LLM states a wrong fee/date/URL | Deterministic fact check; sources-only fallback; flagged-answer review; generation eval gate |
| DeepSeek outage or slowdown | Fallback provider, circuit breaker, sources-only mode |
| Personal data reaches logs or the LLM | Scrubber in `web` before logging and engine calls; fixtures in both directions; privacy notice |
| Scrubber removes legitimate figures | Must-not-scrub fixtures (fees, years, durations) |
| Admin surface exposed | Separate listener bound to Tailscale; no admin routes on public listener; external exposure check on every deploy |
| Locked out of the server (Tailscale problem) | Documented OVH KVM/rescue console break-glass; key-only SSH |
| Data loss | Encrypted off-provider backups; weekly automated restore test; pre-migration dumps |
| Bad data release | Gates refuse below-threshold releases; one-step rollback from the admin panel |
| pgvector changes retrieval quality | M1 parity gate before ChromaDB is retired |
| Too much complexity for one operator | Five containers max; no broker/Redis/Kubernetes; runbooks; alerts instead of watching dashboards |
| Svelte 5 vs legacy syntax from AI workers | Version pinned; lint rejects legacy syntax |
| Per-IP limits block mobile users behind CGNAT | Session/invite-based limits; loose per-IP flood limits only |
| Government sites block the Singapore server (Phase 2) | Per-source reachability setting; manual checking; no evasion |
