"""
answer_nid.py — the grounded answer step: question in → answer out, for NID.

This is the "G" in RAG. It ties together everything already built:
  1. normalize the question (normalize_query) — Banglish→Bangla, expand-don't-replace
  2. retrieve top chunks from the NID collection (bge-m3 + ChromaDB), merging the
     original and transliterated queries
  3. GROUND: if the best hit doesn't clear a similarity floor, decline instead of
     guessing — the model never sees a question it has no context for
  4. GENERATE: hand the chunks + the user's ORIGINAL words to the LLM with a strict
     "answer only from this context" instruction, in the user's language
  5. CITE: attach the source_url of every chunk used

The LLM provider is whatever llm_backends is configured for (z.ai GLM free by default) —
the same backend used for normalization, so no extra setup. Keep LLM_DISABLE_THINKING=1
set for snappier answers.

Run (after embed_nid.py, with your LLM env vars set):
    python answer_nid.py            # interactive: ask questions, get grounded answers
"""
from __future__ import annotations

import chromadb
from sentence_transformers import SentenceTransformer

from llm_backends import get_backend
from normalize_query import normalize

MODEL_NAME = "BAAI/bge-m3"
DB_DIR = "data/vectordb"
COLLECTION = "nid"
TOP_K = 4                 # chunks fed to the model as context
CANDIDATE_N = 8           # pulled per sub-query before merging down to TOP_K
GROUNDING_FLOOR = 0.42    # if the best chunk is below this, decline (coarse out-of-scope gate)
ANSWER_MAX_TOKENS = 1024

IS_E5 = "e5" in MODEL_NAME.lower()

ANSWER_SYSTEM_PROMPT = (
    "You are a helpful assistant for Bangladeshi National ID (NID) services. Answer the "
    "user's question using ONLY the numbered context passages provided. Rules:\n"
    "- Ground every fact in the context. If the context does not contain the answer, say "
    "you don't have that information and suggest checking the official NID portal "
    "(https://services.nidw.gov.bd/). NEVER invent fees, documents, steps, or rules.\n"
    "- Preserve exact facts from the context verbatim — fee amounts, document names, "
    "digit counts, deadlines.\n"
    "- Answer in the SAME language as the question: use Bangla (Bangla script) for "
    "questions written in Bangla or romanized Bangla (Banglish); use English for English "
    "questions.\n"
    "- Be concise and natural. Do NOT mention 'context', 'passages', or their numbers in "
    "your answer — just answer the question directly."
)

DECLINE = (
    "I couldn't find this in the NID knowledge base, so I can't answer reliably. "
    "Please check the official portal: https://services.nidw.gov.bd/\n"
    "এই তথ্যটি আমার কাছে নেই। অনুগ্রহ করে সরকারি পোর্টাল দেখুন: https://services.nidw.gov.bd/"
)


def as_query(text: str) -> str:
    return f"query: {text}" if IS_E5 else text


def retrieve(question, backend, model, coll, top_k=TOP_K, candidate_n=CANDIDATE_N):
    """Normalize → embed each query text → merge by best similarity per chunk → top_k."""
    norm = normalize(question, backend)
    best: dict[str, dict] = {}
    for qt in norm.queries:
        emb = model.encode([as_query(qt)], normalize_embeddings=True)
        res = coll.query(query_embeddings=[emb[0].tolist()], n_results=candidate_n,
                         include=["documents", "metadatas", "distances"])
        for cid, doc, md, dist in zip(res["ids"][0], res["documents"][0],
                                      res["metadatas"][0], res["distances"][0]):
            sim = 1 - dist
            if cid not in best or sim > best[cid]["sim"]:
                best[cid] = {"id": cid, "sim": sim, "text": doc,
                             "category": (md or {}).get("category"),
                             "heading": (md or {}).get("heading", ""),
                             "source_url": (md or {}).get("source_url", "")}
    hits = sorted(best.values(), key=lambda h: h["sim"], reverse=True)[:top_k]
    return norm, hits


def answer(question, backend, model, coll):
    norm, hits = retrieve(question, backend, model, coll)

    # GROUND: nothing clears the floor → decline rather than guess.
    if not hits or hits[0]["sim"] < GROUNDING_FLOOR:
        return {"answer": DECLINE, "sources": [], "hits": hits, "normalized": norm.normalized}

    # GENERATE: build numbered context, pass the user's ORIGINAL question.
    context = "\n\n".join(
        f"[{i+1}] {h['heading']}\n{h['text']}" for i, h in enumerate(hits)
    )
    user_msg = f"CONTEXT PASSAGES:\n{context}\n\nQUESTION: {question}"
    reply = backend.complete(ANSWER_SYSTEM_PROMPT, user_msg, max_tokens=ANSWER_MAX_TOKENS)

    # CITE: unique source urls of the chunks used, in rank order.
    sources = list(dict.fromkeys(h["source_url"] for h in hits if h["source_url"]))
    return {"answer": reply, "sources": sources, "hits": hits, "normalized": norm.normalized}


def main():
    backend = get_backend()
    print(f"LLM backend: {backend.name}")
    model = SentenceTransformer(MODEL_NAME)
    coll = chromadb.PersistentClient(path=DB_DIR).get_collection(COLLECTION)
    print(f"collection '{COLLECTION}': {coll.count()} chunks")
    print("=" * 74)
    print("Ask an NID question in English, Bangla, or Banglish (blank line to quit).")
    print("=" * 74)

    while True:
        try:
            q = input("\nquestion> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break
        result = answer(q, backend, model, coll)

        if result["normalized"]:
            print(f"  (normalized: {result['normalized']})")
        print(f"\n{result['answer']}\n")
        if result["sources"]:
            print("Sources:")
            for s in result["sources"]:
                print(f"  - {s}")
        # transparency: what was retrieved and how strongly
        print("\n  retrieved:")
        for h in result["hits"]:
            print(f"    sim={h['sim']:.3f} [{h['category']}] {h['heading'][:60]}")


if __name__ == "__main__":
    main()
