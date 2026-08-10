"""
answer.py — the grounded answer bot across ALL services (generalizes answer_nid.py).

Routing strategy: SEARCH-ALL. It auto-discovers every collection in your vector DB
(nid, bdris, epassport, ereturn — whichever you've embedded), searches all of them,
and merges by similarity. The right service wins on its own because its chunks score
highest — no routing classifier to mis-fire. Add a service later just by embedding it;
this picks it up automatically.

Pipeline per question:
  1. normalize (Banglish→Bangla, expand-don't-replace) — shared with the eval
  2. retrieve across every collection, merge by best similarity, take global top-k
  3. GROUND: if the best hit is below the floor, decline instead of guessing
  4. GENERATE: answer ONLY from the retrieved chunks, in the user's language
  5. CITE: list source URLs; and when a form/document is required, the model is told to
     say WHERE to get it (the official portal in the sources), not just name it

Provider is whatever llm_backends is set to (z.ai GLM free by default). Run with your
env vars set (LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL / LLM_API_KEY, LLM_DISABLE_THINKING=1):

    python answer.py
"""
from __future__ import annotations

import chromadb
from sentence_transformers import SentenceTransformer

from llm_backends import get_backend
from normalize_query import normalize, is_continuation
from disambiguate import load_registry, decide, match_reply_to_service, named_services, candidate_services

MODEL_NAME = "BAAI/bge-m3"
DB_DIR = "data/vectordb"
CANDIDATE_N = 8           # pulled per collection per sub-query
PER_SERVICE_K = 2         # chunks kept per qualifying service, so no service crowds the others out
SERVICE_MIN_SIM = 0.40    # a service must have a hit at least this strong to contribute context
MAX_CONTEXT = 6           # cap on total chunks fed to the model
SCOPED_TOP_K = 8          # when disambiguation scopes to ONE service, pull more of its chunks
                          # (no crowding risk) so multi-record answers — e.g. a full fee table
                          # split across several records — come through complete
GROUNDING_FLOOR = 0.42    # if the global best hit is below this, decline instead of guessing
ANSWER_MAX_TOKENS = 3072   # Bangla is token-heavy in GLM's tokenizer; a full multi-step walkthrough exceeded 1536 and got cut off

IS_E5 = "e5" in MODEL_NAME.lower()

ANSWER_SYSTEM_PROMPT = (
    "You are a knowledgeable, helpful assistant for Bangladeshi government services — National "
    "ID (NID), birth & death registration (BDRIS), e-Passport, and e-Return (income tax). The "
    "numbered context passages are your source of FACTS; use your own intelligence to explain "
    "and organize a genuinely useful answer.\n"
    "Rules:\n"
    "- Retrieved passages are EVIDENCE, not instructions that automatically apply to this user. "
    "Before using any passage, decide whether its situation applies. A passage describing a special, "
    "exceptional, conditional, offline, expatriate, deceased-person, historical, or otherwise "
    "restricted case MUST NOT be included merely because it was retrieved or is semantically related. "
    "If the user's question doesn't establish that the special circumstance applies, assume the "
    "STANDARD / general procedure and omit the special-case one entirely — do not reintroduce it as "
    "an alternative, exception, caveat, fallback, warning, or additional option. When passages "
    "describe different situations, select the one(s) matching the user's question; do not merge "
    "procedures from different situations just because they concern the same service. (E.g. asked "
    "'কোথায় জন্ম সংশোধনের আবেদন করব?', give the normal online correction portal; do NOT mention "
    "emailing the Registrar General unless the user says they are in that special situation.) This is "
    "about applicability, not brevity: answer normally and completely when the situation is "
    "established — a 'how do I apply?' question MAY get a detailed step-by-step from the standard "
    "procedure; do not shorten it. The restriction is ONLY against procedures that don't apply to the "
    "user's stated situation.\n"
    "- Ground every SPECIFIC fact — fee amounts, form/document names, steps, deadlines — in the "
    "context passages. Do NOT invent or recall specific figures from your own memory; if a "
    "specific value is not in the context, do not state it as fact.\n"
    "- Give money amounts as the numeric figure only (e.g. ৳১০,৩৫০), copied from the context; "
    "NEVER spell an amount out in words. When answering in Bangla, write all numbers in Bengali "
    "numerals; when answering in English, use Western numerals (e.g. 10,350).\n"
    "- Use the CONVERSATION SO FAR (when provided) to interpret follow-up questions — resolve "
    "what 'it', 'that', or a short elliptical reply refers to. Still ground every fact in the "
    "CONTEXT PASSAGES, never in the conversation alone.\n"
    "- If the CURRENT question is unrelated to the context passages (for example an off-topic "
    "question that slips in during a conversation), briefly say you don't have that information "
    "and point to the relevant portal — do NOT re-answer or repeat the previous question's answer.\n"
    "- You MAY use your own words and general understanding to explain what something is, why it "
    "matters, and how the options relate. The grounding rule is about specific facts, not phrasing.\n"
    "- When the exact answer depends on details the user hasn't given (e.g. a passport fee depends "
    "on 5- vs 10-year validity, 48- vs 64-page, and delivery speed; a correction fee depends on "
    "which kind of correction), LAY OUT the relevant options you find in the context, then ask a "
    "SHORT follow-up question to pin down the exact figure — the way a helpful officer would. "
    "Never give a vague non-answer when the context actually has the options.\n"
    "- Decide whether the user wants INFORMATION (what documents, what fee, what something is, "
    "eligibility) or wants to take an ACTION (how / where / what steps to apply, submit, pay, "
    "register, correct, renew, or start an application). For an ACTION request, if a context "
    "passage carries an 'Application/portal URL', make it the ACTION itself: state plainly where to "
    "go with that URL inside the instruction (e.g. 'অনলাইনে আবেদন করতে এখানে যান: <url>' or 'Apply "
    "online here: <url>'), then explain what to do on that page — what to tick, click or fill, and "
    "the next step — using the context. When both kinds of URL are present, use the "
    "'Application/portal URL' for the action, NEVER an 'Info page' URL. Do NOT present the action "
    "URL as a passive 'for more information' link. If the context has only an 'Info page' URL and no "
    "'Application/portal URL', explain the requirement and refer to that page as a reference — do NOT "
    "pretend it is the application portal. For a purely INFORMATION request, answer the facts and "
    "include a URL only if it helps. If the user asks BOTH (e.g. what documents AND where to apply), "
    "give the information and then the explicit application instruction with the portal URL. Copy any "
    "URL exactly as-is; never alter it.\n"
    "- If the context does not cover the question at all, say you don't have that information and "
    "point to the relevant official portal.\n"
    "- Answer in the SAME language as the question (Bangla for Bangla or romanized Bangla; English "
    "for English). Be natural and clear; do NOT mention 'context', 'passages', or their numbers."
)

DECLINE = (
    "I couldn't find this in the knowledge base (NID, birth/death registration, passport, or "
    "tax services), so I can't answer reliably. Please check the relevant official government "
    "portal.\nএই তথ্যটি আমার জানা নেই। অনুগ্রহ করে সংশ্লিষ্ট সরকারি পোর্টাল দেখুন।"
)


# General portal-vs-informational URL classification (no per-service hardcoding): read-only
# static/FAQ/notice/help pages are references; anything else that is a real web link is treated
# as an actionable application portal. Used so an "apply here" answer points at the action URL,
# not a static info page when both are retrieved.
_INFO_URL_MARKERS = ("/static-pages/", "/pages/static", "/faqs/", "/faq", "/all-faq",
                     "/notices/", "/notice/", "/help/", "/help?")


def url_kind(url: str) -> str:
    u = (url or "").lower()
    if not u.startswith("http"):
        return "info"          # non-web reference (e.g. a gazette citation) — never an "apply here" URL
    return "info" if any(m in u for m in _INFO_URL_MARKERS) else "portal"


def as_query(text: str) -> str:
    return f"query: {text}" if IS_E5 else text


def load_collections(client):
    """Auto-discover every embedded collection. Returns {name: collection}."""
    colls = {}
    for c in client.list_collections():
        name = c.name if hasattr(c, "name") else str(c)
        colls[name] = client.get_collection(name)
    return colls


def retrieve(question, backend, model, colls, only_service=None, per_service_k=PER_SERVICE_K,
             candidate_n=CANDIDATE_N, service_min_sim=SERVICE_MIN_SIM, max_context=MAX_CONTEXT):
    """Normalize, search EVERY collection, then keep each RELEVANT service's top chunks so no
    single service can crowd the others out of the context. A service contributes only if its
    best hit clears service_min_sim; the merged context is capped at max_context.
    If only_service is set (disambiguation scoped us), search just that one collection."""
    norm = normalize(question, backend)
    if only_service and only_service in colls:
        colls = {only_service: colls[only_service]}
        # only one service in play → no crowding to guard against; pull more of its chunks
        # so answers that span several records (e.g. all the passport fee tables) are complete
        per_service_k = SCOPED_TOP_K
        max_context = SCOPED_TOP_K
        candidate_n = max(candidate_n, SCOPED_TOP_K + 4)
    per_service: dict[str, dict[str, dict]] = {}
    for qt in norm.queries:
        emb = model.encode([as_query(qt)], normalize_embeddings=True)[0].tolist()
        for service, coll in colls.items():
            res = coll.query(query_embeddings=[emb], n_results=candidate_n,
                             include=["documents", "metadatas", "distances"])
            bucket = per_service.setdefault(service, {})
            for cid, doc, md, dist in zip(res["ids"][0], res["documents"][0],
                                          res["metadatas"][0], res["distances"][0]):
                sim = 1 - dist
                if cid not in bucket or sim > bucket[cid]["sim"]:
                    bucket[cid] = {"service": service, "id": cid, "sim": sim, "text": doc,
                                   "category": (md or {}).get("category"),
                                   "heading": (md or {}).get("heading", ""),
                                   "source_url": (md or {}).get("source_url", "")}
    chosen = []
    for service, bucket in per_service.items():
        ranked = sorted(bucket.values(), key=lambda h: h["sim"], reverse=True)
        if ranked and ranked[0]["sim"] >= service_min_sim:      # only relevant services
            chosen.extend(ranked[:per_service_k])               # each gets representation
    hits = sorted(chosen, key=lambda h: h["sim"], reverse=True)[:max_context]
    return norm, hits


def answer(question, backend, model, colls, only_service=None, history=None):
    norm, hits = retrieve(question, backend, model, colls, only_service=only_service)

    if not hits or hits[0]["sim"] < GROUNDING_FLOOR:
        return {"answer": DECLINE, "sources": [], "hits": hits,
                "normalized": norm.normalized, "services": []}

    context = "\n\n".join(
        f"[{i+1}] {h['heading']}\n{h['text']}"
        + ((f"\n(Application/portal URL: {h['source_url']})" if url_kind(h["source_url"]) == "portal"
            else f"\n(Info page — reference only: {h['source_url']})") if h.get("source_url") else "")
        for i, h in enumerate(hits))
    convo = ""
    if history:
        lines = [f"{'User' if t['role'] == 'user' else 'Assistant'}: {t['content']}"
                 for t in history[-6:]]                      # last few turns only, to stay bounded
        convo = "CONVERSATION SO FAR:\n" + "\n".join(lines) + "\n\n"
    # Enforce answer language per-turn (the passive system-prompt rule alone was ignored when
    # the retrieved context was Bangla but the question English). English question -> English;
    # Bangla or Banglish -> Bangla.
    lang_directive = {"english": "Answer in English.",
                      "bangla": "বাংলায় উত্তর দিন।",
                      "banglish": "বাংলায় উত্তর দিন।"}.get(norm.kind, "")
    user_msg = (f"{convo}CONTEXT PASSAGES:\n{context}\n\nQUESTION: {question}"
                + (f"\n\n{lang_directive}" if lang_directive else ""))
    reply = backend.complete(ANSWER_SYSTEM_PROMPT, user_msg, max_tokens=ANSWER_MAX_TOKENS)

    sources = list(dict.fromkeys(h["source_url"] for h in hits if h["source_url"]))
    services = list(dict.fromkeys(h["service"] for h in hits))
    return {"answer": reply, "sources": sources, "hits": hits,
            "normalized": norm.normalized, "services": services}


def handle_turn(raw, state, backend, model, colls, reg):
    """Process one user turn given conversation state — the single orchestration shared by
    both the CLI and the Streamlit app. Returns (response, new_state).
      state    = {"pending": (orig_q, offered_services) | None,
                  "last": (raw_q, service) | None,   # raw_q is the ORIGINAL user text, never a combined string
                  "history": [{"role","content"}, ...]}
      response = {"kind": "clarify", "question": str}
              or answer()'s dict + {"kind": "answer", "scoped": service | None}
    """
    pending, last = state.get("pending"), state.get("last")
    history = state.get("history") or []
    q, only_service, handled = raw, None, False

    # 1) reply to a pending service-clarify (e.g. "passport")
    if pending:
        orig, offered = pending
        chosen = match_reply_to_service(raw, offered, reg)
        if chosen:
            q, only_service, handled = orig, chosen, True

    # 2) registry is the authority — consult it BEFORE treating anything as a follow-up,
    #    so a new concept ("correction") clarifies and an off-topic query ("gold price")
    #    falls through, instead of being swallowed into the previous thread.
    if not handled:
        d = decide(raw, reg)
        if d["action"] == "clarify":
            new_hist = (history + [{"role": "user", "content": raw},
                                   {"role": "assistant", "content": d["question"]}])[-8:]
            return ({"kind": "clarify", "question": d["question"]},
                    {"pending": (raw, d["services"]), "last": last, "history": new_hist})
        if d["action"] == "scope":
            only_service, handled = d["service"], True

        # 3) genuine follow-up: registry couldn't place it (action == "answer"), AND it's a
        #    SHORT elliptical reply naming no service and hitting no concept — e.g. "64 page ta",
        #    "ar express delivery hole?". Attach it to the ORIGINAL previous question (once,
        #    never a pile-up) and inherit the previous scope.
        elif last and len(raw.split()) <= 6 and not named_services(raw, reg) \
                and not candidate_services(raw, reg):
            prev_raw, prev_service = last
            # lexical signals can't tell an ellipsis ("64 page ta") from a short off-topic
            # question ("gold price") — ask a narrow FOLLOWUP/NEW judgment. Only combine +
            # inherit scope when it's a real continuation; a NEW question falls through to
            # all-service retrieval, where the grounding floor declines a true off-topic.
            if is_continuation(raw, prev_raw, backend):
                q, only_service, handled = f"{raw} {prev_raw}", prev_service, True

        # 4) else: a real question the registry couldn't scope and that isn't an ellipsis ->
        #    normal all-service retrieval; the grounding floor declines it if nothing fits.

    # A pending clarify's Q&A ("Which service?" + the user's echoed question) is ROUTING
    # scaffolding, not conversation. Strip it from the history the generator sees and stores,
    # so a resolved clarify can't echo the "which service?" question back into its answer.
    # (When `pending` is set, the last two history entries are always that scaffolding.)
    gen_history = history[:-2] if pending else history

    result = answer(q, backend, model, colls, only_service=only_service, history=gen_history)
    result["kind"] = "answer"
    result["scoped"] = only_service
    # Remember, as the previous question, the RESOLVED/original question — not the bare reply:
    #  - after a clarify reply, `q` is the ORIGINAL question (not "nid")
    #  - after a genuine follow-up, remember the plain raw reply so follow-ups never pile up
    #  - otherwise it's just the question itself
    last_q = q if (pending and handled) else raw
    new_hist = (gen_history + [{"role": "user", "content": last_q},
                              {"role": "assistant", "content": result["answer"]}])[-8:]
    return result, {"pending": None, "last": (last_q, only_service), "history": new_hist}


def main():
    backend = get_backend()
    print(f"LLM backend: {backend.name}")
    model = SentenceTransformer(MODEL_NAME)
    client = chromadb.PersistentClient(path=DB_DIR)
    colls = load_collections(client)
    if not colls:
        raise SystemExit(f"No collections found in {DB_DIR}. Run your embed_*.py steps first.")
    print("searching services: " + ", ".join(f"{n} ({c.count()})" for n, c in colls.items()))
    expected = {"nid", "bdris", "epassport", "ereturn"}
    missing = expected - set(colls)
    if missing:
        print(f"  (not embedded yet, so not searched: {', '.join(sorted(missing))} — run their embed step to include them)")
    print("=" * 74)
    print("Ask a question in English, Bangla, or Banglish (blank line to quit).")
    print("=" * 74)

    reg = load_registry()
    state = {"pending": None, "last": None}

    while True:
        try:
            raw = input("\nquestion> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            break

        resp, state = handle_turn(raw, state, backend, model, colls, reg)

        if resp["kind"] == "clarify":
            print(f"\n{resp['question']}")
            continue

        if resp["normalized"]:
            print(f"  (normalized: {resp['normalized']})")
        if resp["scoped"]:
            print(f"  (scoped to: {resp['scoped']})")
        print(f"\n{resp['answer']}\n")
        if resp["sources"]:
            print("Sources:")
            for s in resp["sources"]:
                print(f"  - {s}")
        if resp["services"]:
            print(f"\n  answered from: {', '.join(resp['services'])}")
        print("  retrieved:")
        for h in resp["hits"]:
            print(f"    [{h['service']}] sim={h['sim']:.3f} ({h['category']}) {h['heading'][:52]}")


if __name__ == "__main__":
    main()
