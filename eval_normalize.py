"""
eval_normalize.py — measure the WHOLE Banglish pipeline end-to-end.

Unlike the query_nid.py experiment (which used hand-made transliterations to test only
the retrieval half), this runs the real front half too: each Banglish query is passed
through the LLM normalizer, then embedded with expand-don't-replace, then retrieved. It
reports, per query, whether the expected record is found with RAW retrieval vs with
NORMALIZED (expanded) retrieval — so you see the actual lift the LLM step buys, and a
real hit-rate over a batch of messy, varied, code-mixed queries.

Needs a working LLM backend (an API key). Provider via LLM_PROVIDER (default gemini):
    set GEMINI_API_KEY=...   &&  python eval_normalize.py
    set LLM_PROVIDER=claude  &&  set ANTHROPIC_API_KEY=...  &&  python eval_normalize.py

The test set is intentionally messier than the gold file: different spellings, heavier
code-mixing, typos — the kind of input real users type. Expected record ids are the
known targets from nid_gold_queries.json (these are Banglish rephrasings of the same
intents).
"""
from __future__ import annotations

import chromadb
from sentence_transformers import SentenceTransformer

from llm_backends import get_backend
from normalize_query import normalize

MODEL_NAME = "BAAI/bge-m3"
DB_DIR = "data/vectordb"
COLLECTION = "nid"
TOP_K = 4
RECALL_PER_QUERY = 8          # pull a few extra per sub-query before merging down to TOP_K

IS_E5 = "e5" in MODEL_NAME.lower()


def as_query(text: str) -> str:
    return f"query: {text}" if IS_E5 else text


# Banglish queries with known target records (ids from nid_gold_queries.json intents).
TEST_SET = [
    {"q": "amar card hariye gese, notun card pete koto taka lagbe first time?",
     "expected_id": "9260a976f7fcb9e9", "intent": "reissue fee"},
    {"q": "eksathe duibar registration hoye gese amar, ekhon ki hobe?",
     "expected_id": "50d66a66a22cd5a0", "intent": "duplicate registration"},
    {"q": "amar NID number 13 digit but karo karo 17 digit keno?",
     "expected_id": "221cccb1be6d3cb6", "intent": "13 vs 17 digit"},
    {"q": "biye biched hoye gese, card theke husband er naam kivabe baad debo?",
     "expected_id": "79abb3857edf8e76", "intent": "remove husband name"},
    {"q": "amar card hariyeo gese ar tottho o vul ache, eksathe thik kora jabe?",
     "expected_id": "99b73f516742b5e6", "intent": "lost + correction sequencing"},
    {"q": "kon kon upazila special area hisebe ghoshito?",
     "expected_id": "0328798228089b51", "intent": "special upazila list"},
]


def main() -> None:
    backend = get_backend()
    print(f"LLM backend: {backend.name}")
    model = SentenceTransformer(MODEL_NAME)
    coll = chromadb.PersistentClient(path=DB_DIR).get_collection(COLLECTION)
    print(f"collection '{COLLECTION}': {coll.count()} chunks")
    print("=" * 82)

    def top_ids(query_texts: list[str], k: int) -> list[str]:
        """Embed each query text, merge by best similarity per id, return top-k ids."""
        best: dict[str, float] = {}
        for qt in query_texts:
            emb = model.encode([as_query(qt)], normalize_embeddings=True)
            res = coll.query(query_embeddings=[emb[0].tolist()], n_results=RECALL_PER_QUERY,
                             include=["distances"])
            for cid, dist in zip(res["ids"][0], res["distances"][0]):
                sim = 1 - dist
                if cid not in best or sim > best[cid]:
                    best[cid] = sim
        return [cid for cid, _ in sorted(best.items(), key=lambda kv: kv[1], reverse=True)[:k]]

    raw_hits = norm_hits = 0
    for case in TEST_SET:
        r = normalize(case["q"], backend)
        raw_ids = top_ids([case["q"]], TOP_K)                 # original only
        norm_ids = top_ids(r.queries, TOP_K)                  # expand-don't-replace
        raw_hit = case["expected_id"] in raw_ids
        norm_hit = case["expected_id"] in norm_ids
        raw_hits += raw_hit
        norm_hits += norm_hit

        flip = "  <== recovered by normalization" if (norm_hit and not raw_hit) else ""
        print(f"\n[{case['intent']}]  expect {case['expected_id']}")
        print(f"  query        : {case['q']}")
        print(f"  normalized   : {r.normalized if r.normalized else '(unchanged — detected ' + r.kind + ')'}")
        print(f"  raw retrieval: {'HIT' if raw_hit else 'MISS'}")
        print(f"  normalized   : {'HIT' if norm_hit else 'MISS'}{flip}")

    n = len(TEST_SET)
    print("\n" + "=" * 82)
    print(f"RAW (romanized as-is)      : {raw_hits}/{n} hit")
    print(f"NORMALIZED (expand+Bangla) : {norm_hits}/{n} hit")
    print(f"lift: +{norm_hits - raw_hits} queries recovered by the LLM normalization step")


if __name__ == "__main__":
    main()
