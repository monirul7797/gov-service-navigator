"""
query_epassport.py — Phase 2, step 2: ask questions, get the most relevant e-Passport chunks.

This is the part to show your professor. It embeds a question with the SAME model
used in embed_epassport.py, then asks ChromaDB for the nearest chunks by cosine
similarity. The DEMO_QUERIES below deliberately mix English, Bangla, and "Banglish"
to show the retriever matches by *meaning*, across languages and scripts — not by
keywords.

Run (after embed_epassport.py):
    python query_epassport.py      # runs the demo queries, then an interactive prompt
"""
from __future__ import annotations

import textwrap

import chromadb
from sentence_transformers import SentenceTransformer

MODEL_NAME = "BAAI/bge-m3"     # MUST match the model used in embed_epassport.py
DB_DIR = "data/vectordb"
COLLECTION = "epassport"
TOP_K = 4

IS_E5 = "e5" in MODEL_NAME.lower()


def as_query(text: str) -> str:
    return f"query: {text}" if IS_E5 else text


# English / Bangla / Banglish — chosen to show cross-lingual + cross-script matching
DEMO_QUERIES = [
    "How can I check the status of my passport application?",
    "পাসপোর্ট আবেদনের পাসওয়ার্ড ভুলে গেছি কি করবো?",        # forgot password (Bangla)
    "express delivery te upgrade kora jabe ki?",             # express upgrade (Banglish)
    "Can I cancel or reschedule my appointment?",
    "ই-মেইল অ্যাড্রেস ছাড়া কি অনলাইনে আবেদন করা যায়?",       # no-email apply (Bangla)
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

    def run(q: str):
        emb = model.encode([as_query(q)], normalize_embeddings=True)
        res = coll.query(
            query_embeddings=[emb[0].tolist()], n_results=TOP_K,
            include=["documents", "metadatas", "distances"],
        )
        print(f"Q: {q}")
        show(res)

    print("=" * 78)
    print("DEMO QUERIES  (English · Bangla · Banglish → same knowledge base)")
    print("=" * 78)
    for q in DEMO_QUERIES:
        run(q)

    # interactive
    print("=" * 78)
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
