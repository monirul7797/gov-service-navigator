"""
query_nid.py — Phase 2, step 2: ask questions, get the most relevant NID chunks.

This is the part to show your professor. It embeds a question with the SAME model
used in embed_nid.py, then asks ChromaDB for the nearest chunks by cosine similarity.
The DEMO_QUERIES below deliberately mix English, Bangla, and "Banglish" to show the
retriever matches by *meaning*, across languages and scripts — not by keywords.

It also includes a TRANSLITERATION EXPERIMENT (added below): it runs the two known
Banglish failure cases (gq_003, gq_010) both as raw romanized text AND as hand-
transliterated Bangla script, and reports HIT/MISS against the expected record. This
tests the *retrieval half* of the "normalize Banglish -> Bangla before embedding" idea:
if the transliterated form retrieves the right record, the approach is viable and the
only remaining risk is transliteration quality (the LLM half). If it misses even when
hand-transliterated, transliteration is not the fix and we save building the pre-pass.

Run (after embed_nid.py):
    python query_nid.py            # demo queries, then the transliteration experiment, then interactive
"""
from __future__ import annotations

import textwrap

import chromadb
from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-m3"     # MUST match the model used in embed_nid.py
DB_DIR = "data/vectordb"
COLLECTION = "nid"
TOP_K = 4

IS_E5 = "e5" in MODEL_NAME.lower()


def as_query(text: str) -> str:
    return f"query: {text}" if IS_E5 else text


# English / Bangla / Banglish — chosen to show cross-lingual + cross-script matching
DEMO_QUERIES = [
    "What documents do I need to correct my name on the NID?",
    "হারিয়ে যাওয়া আইডি কার্ড আবার কিভাবে পাবো?",          # lost card (Bangla)
    "notun voter registration er fee koto?",                # fees (Banglish)
    "How is the fee calculated with VAT?",
    "ঠিকানা পরিবর্তন করতে চাই",                              # address change (Bangla)
]


# --- TRANSLITERATION EXPERIMENT -------------------------------------------------
# Each entry: (label, romanized_query, transliterated_bangla_query, expected_record_id)
# The transliterations are natural/domain-aware, NOT reverse-engineered from the
# target records. gq_010's corpus record happens to use phonetic "রেজিস্ট্রেশন", so a
# faithful transliteration matches it; gq_003 is the harder case (record says
# "জাতীয় পরিচয়পত্র", query says "card"/"কার্ড") and is a fair stress test.
TRANSLIT_EXPERIMENT = [
    (
        "gq_003 reissue fee",
        "amar card hariye gese, notun card pete koto taka lagbe first time?",
        "আমার কার্ড হারিয়ে গেছে, নতুন কার্ড পেতে কত টাকা লাগবে প্রথম বার?",
        "9260a976f7fcb9e9",
    ),
    (
        "gq_010 duplicate registration",
        "eksathe duibar registration hoye gese amar, ekhon ki hobe?",
        "একসাথে দুইবার রেজিস্ট্রেশন হয়ে গেছে আমার, এখন কি হবে?",
        "50d66a66a22cd5a0",
    ),
]


def show(res) -> None:
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    for rank, (doc, md, dist) in enumerate(zip(docs, metas, dists), 1):
        sim = 1 - dist                      # cosine distance -> similarity
        snippet = textwrap.shorten(doc.replace("\n", " "), width=140)
        print(f"  {rank}. sim={sim:.3f}  [{md['category']}]  ({md['lang']})")
        print(f"     {md['heading']}")
        print(f"     {snippet}")
        print(f"     ↳ {md['source_url']}")
    print()


def main() -> None:
    model = SentenceTransformer(MODEL_NAME)
    client = chromadb.PersistentClient(path=DB_DIR)
    coll = client.get_collection(COLLECTION)
    print(f"loaded collection '{COLLECTION}' with {coll.count()} chunks\n")

    def query(q: str):
        emb = model.encode([as_query(q)], normalize_embeddings=True)
        return coll.query(
            query_embeddings=[emb[0].tolist()], n_results=TOP_K,
            include=["documents", "metadatas", "distances"],
        )

    def run(q: str):
        print(f"Q: {q}")
        show(query(q))

    def probe(q: str, expected_id: str):
        """Return (found, rank, sim) for expected_id in the top-K of query q."""
        res = query(q)
        ids = res["ids"][0]
        sims = [1 - d for d in res["distances"][0]]
        if expected_id in ids:
            r = ids.index(expected_id)
            return True, r + 1, sims[r]
        return False, None, sims[0] if sims else 0.0

    print("=" * 78)
    print("DEMO QUERIES  (English · Bangla · Banglish → same knowledge base)")
    print("=" * 78)
    for q in DEMO_QUERIES:
        run(q)

    # --- transliteration A/B experiment -----------------------------------------
    print("=" * 78)
    print("TRANSLITERATION EXPERIMENT  (romanized Banglish  vs  transliterated Bangla)")
    print("Does converting Banglish -> Bangla script recover the right record?")
    print("=" * 78)
    for label, romanized, translit, expected_id in TRANSLIT_EXPERIMENT:
        print(f"\n{label}   (expected record: {expected_id})")
        f_rom, rank_rom, sim_rom = probe(romanized, expected_id)
        f_tr, rank_tr, sim_tr = probe(translit, expected_id)

        def fmt(found, rank, sim):
            return (f"HIT  at rank {rank}, sim={sim:.3f}" if found
                    else f"MISS (expected not in top-{TOP_K}; best sim={sim:.3f})")
        print(f"  romanized      : {fmt(f_rom, rank_rom, sim_rom)}")
        print(f"    {romanized}")
        print(f"  transliterated : {fmt(f_tr, rank_tr, sim_tr)}")
        print(f"    {translit}")
        if f_tr and not f_rom:
            print("  => transliteration RECOVERED this query (viable: fix the front-end, retrieval works)")
        elif f_tr and f_rom:
            print("  => both already work (this pair no longer needs transliteration)")
        elif not f_tr and not f_rom:
            print("  => transliteration did NOT recover it — retrieval issue is deeper than script")
        else:
            print("  => romanized worked but transliteration didn't — unexpected, worth inspecting")

    # interactive
    print("\n" + "=" * 78)
    print("Ask your own (blank line to quit). Try a Bangla question and an English one.")
    print("=" * 78)
    while True:
        try:
            q = input("\nquestion> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not q:
            break
        run(q)


if __name__ == "__main__":
    main()
