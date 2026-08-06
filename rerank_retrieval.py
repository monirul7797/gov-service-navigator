"""
rerank_retrieval.py — two-stage retrieval (dense recall -> cross-encoder rerank).

WHY THIS EXISTS
    Pure dense retrieval (bge-m3 + Chroma) reliably recalls the right *neighbourhood*
    but mis-orders within it: a short record containing the near-verbatim answer gets
    outranked by longer, vocabulary-dense records that merely share topic words. The
    NID eval showed this directly (e.g. the "13 vs 17 digit" FAQ, which literally
    contains the question, loses to fee records on shared "জাতীয় পরিচয়পত্র" vocabulary).

    A cross-encoder reads the (query, passage) pair jointly instead of comparing two
    independent vectors, so it can see exact-term overlap that bi-encoder embeddings
    smear away. The standard remedy is: recall a WIDE candidate set with the fast dense
    index, then RERANK that set with the slower-but-sharper cross-encoder and keep top-k.

DESIGN NOTES
    - Models are module-level singletons: loaded once, reused across calls (important
      for the chatbot, which calls retrieve() per user turn).
    - Same embedder as the rest of the pipeline: BAAI/bge-m3, normalize_embeddings=True.
    - Reranker: BAAI/bge-reranker-v2-m3 (same family, multilingual, ~600MB first download).
    - Two scores are carried per result and they are NOT the same scale:
        * dense_sim   = 1 - cosine_distance from Chroma, range ~[0,1]. Kept because the
                        project's "low similarity => thin coverage, not broken retrieval"
                        convention is defined on this scale.
        * rerank_score = cross-encoder logit, unbounded, higher = more relevant. Used
                        ONLY for ordering. A sigmoid-normalised copy (rerank_prob) is
                        also provided for readability.
    - candidate_n is the recall depth. The reranker can only promote a record that dense
      recall actually returned, so candidate_n must be wide enough to contain the right
      record. 30 is a sensible default for these ~40-110 record collections.

USAGE (library)
    from rerank_retrieval import retrieve
    hits = retrieve("nid", "how much to reissue a lost NID card", top_k=4, candidate_n=30)
    for h in hits:
        print(h["id"], h["category"], round(h["rerank_score"], 3), round(h["dense_sim"], 3))

USAGE (CLI demo)
    python rerank_retrieval.py nid "amar card hariye gese notun card er fee koto"
"""
from __future__ import annotations

from pathlib import Path
import math
import sys

import chromadb
from sentence_transformers import SentenceTransformer, CrossEncoder

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "data" / "vectordb"

EMBED_MODEL_NAME = "BAAI/bge-m3"
RERANK_MODEL_NAME = "BAAI/bge-reranker-v2-m3"
RERANK_MAX_LENGTH = 512

DEFAULT_TOP_K = 4
DEFAULT_CANDIDATE_N = 30

_IS_E5 = "e5" in EMBED_MODEL_NAME.lower()

# --- lazy singletons ---------------------------------------------------------
_embedder: SentenceTransformer | None = None
_reranker: CrossEncoder | None = None
_client: chromadb.api.client.Client | None = None


def _as_query(text: str) -> str:
    # bge-m3 needs no prefix; kept for parity with the rest of the pipeline.
    return f"query: {text}" if _IS_E5 else text


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBED_MODEL_NAME)
    return _embedder


def get_reranker() -> CrossEncoder:
    global _reranker
    if _reranker is None:
        _reranker = CrossEncoder(RERANK_MODEL_NAME, max_length=RERANK_MAX_LENGTH)
    return _reranker


def get_client() -> chromadb.api.client.Client:
    global _client
    if _client is None:
        if not DB_DIR.exists():
            raise SystemExit(f"No vector DB at {DB_DIR}\nRun the embed step first.")
        _client = chromadb.PersistentClient(path=str(DB_DIR))
    return _client


def _sigmoid(x: float) -> float:
    # guard against overflow on large-magnitude logits
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def dense_candidates(collection_name: str, query: str, candidate_n: int) -> list[dict]:
    """Stage 1: wide dense recall. Returns candidates in dense-similarity order."""
    collection = get_client().get_collection(collection_name)
    n = min(candidate_n, collection.count())
    emb = get_embedder().encode([_as_query(query)], normalize_embeddings=True)
    res = collection.query(
        query_embeddings=[emb[0].tolist()],
        n_results=n,
        include=["documents", "metadatas", "distances"],
    )
    out = []
    for i, cid in enumerate(res["ids"][0]):
        meta = res["metadatas"][0][i] or {}
        out.append({
            "id": cid,
            "text": res["documents"][0][i],
            "category": meta.get("category"),
            "heading": meta.get("heading"),
            "dense_sim": 1.0 - res["distances"][0][i],
            "dense_rank": i + 1,          # 1-based rank in the dense pool
        })
    return out


def rerank(query: str, candidates: list[dict]) -> list[dict]:
    """Stage 2: cross-encoder rerank of the candidate pool (in place-safe copy)."""
    if not candidates:
        return []
    pairs = [(query, c["text"]) for c in candidates]
    scores = get_reranker().predict(pairs)  # logits, higher = better
    ranked = []
    for c, s in zip(candidates, scores):
        c2 = dict(c)
        c2["rerank_score"] = float(s)
        c2["rerank_prob"] = _sigmoid(float(s))
        ranked.append(c2)
    ranked.sort(key=lambda x: x["rerank_score"], reverse=True)
    for i, c in enumerate(ranked):
        c["rerank_rank"] = i + 1
    return ranked


def retrieve(collection_name: str, query: str,
             top_k: int = DEFAULT_TOP_K,
             candidate_n: int = DEFAULT_CANDIDATE_N) -> list[dict]:
    """Full two-stage retrieval: dense recall -> rerank -> top_k. Chatbot entry point."""
    cands = dense_candidates(collection_name, query, candidate_n)
    return rerank(query, cands)[:top_k]


def _demo() -> None:
    if len(sys.argv) < 3:
        print('usage: python rerank_retrieval.py <collection> "<query>" [top_k] [candidate_n]')
        raise SystemExit(1)
    coll = sys.argv[1]
    query = sys.argv[2]
    top_k = int(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_TOP_K
    cand_n = int(sys.argv[4]) if len(sys.argv) > 4 else DEFAULT_CANDIDATE_N

    hits = retrieve(coll, query, top_k=top_k, candidate_n=cand_n)
    print(f"query: {query}")
    print(f"collection: {coll}  (recall {cand_n} -> rerank -> top {top_k})")
    print("-" * 70)
    for h in hits:
        head = (h.get("heading") or h["text"][:60]).split("\n")[0][:60]
        print(f"  rerank#{h['rerank_rank']} (was dense#{h['dense_rank']})  "
              f"score={h['rerank_score']:.3f} prob={h['rerank_prob']:.3f} "
              f"dense_sim={h['dense_sim']:.3f}  [{h['category']}] {h['id']}")
        print(f"      {head}")


if __name__ == "__main__":
    _demo()
