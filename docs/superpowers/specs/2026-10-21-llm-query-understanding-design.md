# LLM Query Understanding — Design

- **Date:** 2026-10-21
- **Status:** Draft for owner review
- **Approved direction (owner, 2026-10-21):** "LLM understands the question, code checks the
  answer", kept only if it beats the current routing on the test suite.
- **Evidence:** `docs/reports/2026-10-21-llm-and-conversation-test.md`
- **Changes:** the project rule "Routing is deterministic (`disambiguate.py` +
  `concept_registry.json`). Do not replace it with LLM judgment." (`CLAUDE.md`) and spec
  `2026-10-20-system-architecture-design.md` §5 (see §8 below).

## 1. Problem

Most bad conversation behaviour in the test came from code that decides before the LLM reads the
conversation: the keyword router asks "Which service?" for real follow-ups, a ≤ 6-word rule and a
fragile FOLLOWUP/NEW call glue random questions to the previous topic, an off-topic turn wipes
the topic, and a service name inside a follow-up switches service. Greetings run the full answer
pipeline. A modern model (DeepSeek V4.1 Flash, thinking off) understood these conversations
correctly even when the code routed them wrongly.

## 2. Goal and success criteria

Replace the routing decisions with **one LLM "understanding" call per turn** that reads the
conversation, while code keeps every hard boundary on facts. The change is kept only if, on the
same model (`deepseek-flash`, thinking off):

1. The 41-turn conversation suite scores **strictly better** than the current routing on
   follow-ups resolved, and **no worse** on random turns handled cleanly, invented facts and
   answers flagged by the fact check.
2. `eval_nid.py`, `eval_epassport.py`, `eval_ereturn.py`, `eval_bdris.py` results are unchanged
   (retrieval code is not changed by this work).
3. All unit tests pass, including `test_multiturn.py` for the kept fallback path.

## 3. Non-goals

Rewriting the answer prompt (a separate, measured experiment later); personal-data scrubbing
(spec §4.4, built in the Rust web layer, M3); the fact check as a blocking gate (spec §5.3, M2 —
here it is only measured); fixing the two data problems from the report (separate data task);
the M0 restructuring.

## 4. New turn flow

```
message + conversation history (last 6 turns)
        │
        ▼
 understand()  ── one LLM call, JSON out ──► on failure: current registry path (fallback)
        │
        ├─ small_talk / off_topic ─► short reply (filtered: no links, no figures) ─► done
        ├─ unclear ───────────────► clarifying question ─────────────────────────► done
        └─ service_question
                │
                ▼
        retrieve(standalone queries, services)   (existing retrieval code)
                │  nothing above the grounding floor ─► fixed decline ─► done
                ▼
        answer()  (existing answer prompt, history, language rule)
```

### 4.1 `understand(message, history, backend) -> Understanding`

New module `understand.py`. One chat call, temperature 0, `max_tokens` 600, thinking disabled.
The prompt describes the four live services (from `concept_registry.json`, so adding a service
needs no prompt edit) and asks for **JSON only**:

| Field | Type | Meaning |
|---|---|---|
| `type` | `service_question` \| `small_talk` \| `off_topic` \| `unclear` | What the message is, *in the context of the conversation* |
| `services` | list of live service IDs | Services the question is about; empty when not a service question or unknown |
| `query_bn` | string | Self-contained search query in Bangla (pronouns and ellipsis resolved from history) |
| `query_en` | string | The same query in English |
| `answer_language` | `bn` \| `en` | Bangla for Bangla or Banglish messages, English for English |
| `reply` | string | Only for `small_talk` / `off_topic`: one or two sentences in `answer_language` |
| `clarify` | string | Only for `unclear`: one short clarifying question |

Validation in code (deterministic): parse JSON (tolerating a fenced block); unknown types →
failure; service IDs not live → dropped; empty queries for a `service_question` → use the raw
message; missing `answer_language` → existing deterministic detection (`bangla_ratio`,
`looks_english`). Any failure (exception, timeout, invalid JSON) → fallback (§4.5). The raw model
output and the parsed result are kept on the response for logging.

### 4.2 Small talk and off-topic replies

The model's `reply` is used only after a deterministic filter: it must contain no URL and no
digit (Western or Bengali); otherwise, or if empty, a fixed bilingual template is used
(`OFF_TOPIC_REPLY` / `SMALL_TALK_REPLY` constants). No retrieval, no second LLM call.

### 4.3 Clarification

`unclear` returns the model's `clarify` question (kind `clarify`). There is no `pending` state:
the next turn's `understand()` sees the question and the user's reply in the history and
resolves it (e.g. "nid").

### 4.4 Retrieval and answering

`retrieve()` gains a `services: list[str] | None` parameter (existing `only_service` keeps
working): one service → current scoped behaviour; several → only those collections, normal
per-service caps; none → all live services. Queries searched: `query_bn`, `query_en` and the raw
message (deduplicated). The Banglish transliteration call is skipped on this path because
`query_bn` replaces it.

`answer()` gains optional keyword arguments `queries: list[str] | None`, `services:
list[str] | None` and `answer_language: str | None`. When `queries` is given it skips
`normalize()` and retrieves with those queries; when `answer_language` is given it sets the
language directive. Called without them it behaves exactly as today (fallback path and evals).
The question shown to the answer model is the raw message; the model still sees the history.
Grounding floor, context building and the answer prompt are unchanged.

### 4.5 Fallback

The current `handle_turn` is kept, renamed `handle_turn_registry`, and used only when
`understand()` fails. `test_multiturn.py` keeps testing it. The new `handle_turn` has the same
signature and response shape, so `app.py` and the CLI need no changes; responses gain
`"route": "llm" | "registry"` and `"understanding": {...}`.

### 4.6 Conversation state

`{"history": [...], "pending": ..., "last": ...}` stays the same shape; the LLM path reads only
`history` and appends every turn (including small talk, off-topic and clarifications) so the
next `understand()` sees the whole conversation. `pending`/`last` are maintained only for the
fallback path.

## 5. Settings

`deepseek-flash` with `LLM_DISABLE_THINKING=1` for every call (understanding, answer, fallback
transliteration). Unchanged: `MAX_CONTEXT`, per-service k, `GROUNDING_FLOOR`,
`ANSWER_MAX_TOKENS`.

## 6. Testing

| Test | Needs LLM? | What it proves |
|---|---|---|
| `test_understand.py` (new) | No (fake backend) | JSON parsing incl. fenced output; invalid/unknown → failure; non-live services dropped; empty query → raw message; language fallback |
| `test_turn_routing.py` (new) | No (fake backend + fake retrieval) | Each `type` takes the right branch; `services` reach `retrieve()`; failure → registry path; reply filter strips links/figures → template; history grows on every branch |
| `test_multiturn.py` (existing) | No | Fallback path unchanged |
| `eval_conversations.py` + `conversation_cases.json` (new, from the test harness) | Yes (~US$0.05/run) | The 41-turn suite with per-turn expectations: expected kind, expected service set, forbidden content (e.g. "thanks" must not contain fee figures), plus the prototype fact check. Prints a scorecard; `--route registry` runs the old path for comparison |
| `eval_*.py` (existing) | No | Retrieval unchanged |

The suite's expectations are written as checkable fields per turn (`kind`, `services`,
`must_not_contain`, `must_contain_any`), not prose, so the comparison is mechanical.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Non-determinism: the same message may be understood differently | Temperature 0; validation; scorecard over the suite; fallback |
| A wrong `services` label hides the right records | Raw message is also searched; suite measures follow-up resolution; can widen to "preferred, not exclusive" if the suite shows misses |
| Extra latency/cost | Replaces two calls (transliteration + follow-up check) with one; measured in the suite |
| Prompt injection via the understanding call | Output is JSON validated against fixed types/IDs; replies filtered; the answer call is unchanged |

## 8. Document changes when adopted

- `CLAUDE.md`: routing rule becomes: "Routing: one LLM understanding call (`understand.py`)
  classifies the turn and writes self-contained search queries; code validates it and keeps all
  fact boundaries. `disambiguate.py` + `concept_registry.json` remain the service catalogue and
  the fallback router. Adding a service = corpus → embed → registry entry."
- Architecture spec §5.2: `stage` events add `understanding`; §5.4: replace the
  deterministic-routing bullet with this design; §10.1: add the conversation suite to the
  generation eval.
- `docs/PROJECT_STATUS.md`: decision log entry.
