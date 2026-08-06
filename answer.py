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
from normalize_query import normalize
from disambiguate import load_registry, decide, match_reply_to_service, named_services

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
ANSWER_MAX_TOKENS = 1536

IS_E5 = "e5" in MODEL_NAME.lower()

ANSWER_SYSTEM_PROMPT = (
    "You are a knowledgeable, helpful assistant for Bangladeshi government services — National "
    "ID (NID), birth & death registration (BDRIS), e-Passport, and e-Return (income tax). The "
    "numbered context passages are your source of FACTS; use your own intelligence to explain "
    "and organize a genuinely useful answer.\n"
    "Rules:\n"
    "- Ground every SPECIFIC fact — fee amounts, form/document names, steps, deadlines — in the "
    "context passages. Do NOT invent or recall specific figures from your own memory; if a "
    "specific value is not in the context, do not state it as fact.\n"
    "- You MAY use your own words and general understanding to explain what something is, why it "
    "matters, and how the options relate. The grounding rule is about specific facts, not phrasing.\n"
    "- When the exact answer depends on details the user hasn't given (e.g. a passport fee depends "
    "on 5- vs 10-year validity, 48- vs 64-page, and delivery speed; a correction fee depends on "
    "which kind of correction), LAY OUT the relevant options you find in the context, then ask a "
    "SHORT follow-up question to pin down the exact figure — the way a helpful officer would. "
    "Never give a vague non-answer when the context actually has the options.\n"
    "- If a form or document is needed, say WHERE to get it (the official portal/source in the passages).\n"
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


def answer(question, backend, model, colls, only_service=None):
    norm, hits = retrieve(question, backend, model, colls, only_service=only_service)

    if not hits or hits[0]["sim"] < GROUNDING_FLOOR:
        return {"answer": DECLINE, "sources": [], "hits": hits,
                "normalized": norm.normalized, "services": []}

    context = "\n\n".join(f"[{i+1}] {h['heading']}\n{h['text']}" for i, h in enumerate(hits))
    user_msg = f"CONTEXT PASSAGES:\n{context}\n\nQUESTION: {question}"
    reply = backend.complete(ANSWER_SYSTEM_PROMPT, user_msg, max_tokens=ANSWER_MAX_TOKENS)

    sources = list(dict.fromkeys(h["source_url"] for h in hits if h["source_url"]))
    services = list(dict.fromkeys(h["service"] for h in hits))
    return {"answer": reply, "sources": sources, "hits": hits,
            "normalized": norm.normalized, "services": services}


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
    pending = None            # (original_question, offered_services) from a registry service-clarify
    last = None               # (question, service) previous answered turn — for short follow-ups

    while True:
        try:
            raw = input("\nquestion> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not raw:
            break

        q, only_service, handled = raw, None, False

        # 1) reply to a registry service-clarify (e.g. "passport")
        if pending:
            orig, offered = pending
            pending = None
            chosen = match_reply_to_service(raw, offered, reg)
            if chosen:
                q, only_service, handled = orig, chosen, True

        # 2) a short follow-up that names no NEW service → refine the previous answer
        #    (e.g. after a passport-fee answer: "10 year 64 page regular")
        if not handled and last and len(raw.split()) <= 6 and not named_services(raw, reg):
            prev_q, prev_service = last
            q, only_service, handled = f"{raw} {prev_q}", prev_service, True

        # 3) fresh question → consult the registry
        if not handled:
            d = decide(raw, reg)
            if d["action"] == "clarify":
                print(f"\n{d['question']}")
                pending = (raw, d["services"])
                continue
            if d["action"] == "scope":
                only_service = d["service"]

        result = answer(q, backend, model, colls, only_service=only_service)
        last = (q, only_service)      # remember for follow-ups

        if result["normalized"]:
            print(f"  (normalized: {result['normalized']})")
        if only_service:
            print(f"  (scoped to: {only_service})")
        print(f"\n{result['answer']}\n")
        if result["sources"]:
            print("Sources:")
            for s in result["sources"]:
                print(f"  - {s}")
        if result["services"]:
            print(f"\n  answered from: {', '.join(result['services'])}")
        print("  retrieved:")
        for h in result["hits"]:
            print(f"    [{h['service']}] sim={h['sim']:.3f} ({h['category']}) {h['heading'][:52]}")


if __name__ == "__main__":
    main()
