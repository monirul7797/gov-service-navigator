"""
embed_nid.py — Phase 2, step 1: turn nid.jsonl into a searchable vector store.

WHAT THIS DOES (the mental model):
  Every chunk of text is converted into a list of numbers (a "vector" / "embedding")
  by a neural network. The network is trained so that texts with *similar meaning*
  land close together in this number-space — even across languages. So the Bangla
  chunk "নতুন ভোটার নিবন্ধনের যোগ্যতা" and the English query "who can register as a
  voter" end up near each other, and we can find one using the other.

  We store those vectors in ChromaDB (a small on-disk vector database). Later,
  query_nid.py embeds a question the same way and asks Chroma for the nearest chunks.

WHY THIS MODEL:
  We use a *sentence-transformer* (BAAI/bge-m3), not a plain encoder like BanglaBERT.
  BanglaBERT understands Bangla but was not trained to make similar sentences have
  similar vectors, so it retrieves poorly. BGE-M3 is trained exactly for semantic
  search and is strong at multilingual + cross-lingual (Bangla ↔ English ↔ Banglish).

Run:
    pip install sentence-transformers chromadb
    python embed_nid.py
"""
from __future__ import annotations

import json
import os

import chromadb
from sentence_transformers import SentenceTransformer

# ---- config (top-of-file, per the project convention) -----------------------
# BGE-M3 is the recommended default. If your laptop is tight on RAM/time today,
# switch to a lighter model — retrieval is slightly weaker but still cross-lingual:
#     "intfloat/multilingual-e5-small"   (~0.47 GB, fastest)
#     "intfloat/multilingual-e5-base"    (~1.1  GB, middle)
#     "BAAI/bge-m3"                      (~2.2  GB, best — default)
MODEL_NAME = "BAAI/bge-m3"

CHUNKS_PATH = "data/chunks/nid.jsonl"
DB_DIR = "data/vectordb"          # Chroma persists here; embed once, query forever
COLLECTION = "nid"
BATCH_SIZE = 16

# E5-family models REQUIRE a task prefix ("passage:" for documents, "query:" for
# questions). BGE-M3 does not. This flag keeps the code correct for either choice.
IS_E5 = "e5" in MODEL_NAME.lower()


def as_passage(text: str) -> str:
    return f"passage: {text}" if IS_E5 else text


def to_metadata_nid(r: dict) -> dict:
    """Canonical NID embedding metadata (Freshness V2 §11: reembed must match
    the full embed path exactly, so both import THIS function)."""
    return {
        "category": r["category"],
        "heading": r["heading"],
        "lang": r["lang"],
        "source_url": r["source_url"],
        "official": r["official"],
    }


def load_chunks(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def main() -> None:
    rows = load_chunks(CHUNKS_PATH)
    print(f"loaded {len(rows)} chunks from {CHUNKS_PATH}")

    # 1) Load the embedding model (first run downloads the weights — one time).
    print(f"loading model: {MODEL_NAME} (first run downloads weights, be patient)…")
    model = SentenceTransformer(MODEL_NAME)

    # 2) Embed every chunk's text. normalize_embeddings=True makes each vector unit
    #    length, so cosine similarity == dot product and scores are comparable.
    texts = [as_passage(r["text"]) for r in rows]
    embeddings = model.encode(
        texts, batch_size=BATCH_SIZE,
        show_progress_bar=True, normalize_embeddings=True,
    )
    print(f"embedded {len(embeddings)} vectors, dim = {len(embeddings[0])}")

    # 3) Store in ChromaDB. We keep useful metadata next to each vector so results
    #    can be filtered (by category/lang) and can show their official source.
    os.makedirs(DB_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=DB_DIR)
    # rebuild cleanly so re-running doesn't duplicate
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    coll = client.create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})

    coll.add(
        ids=[r["id"] for r in rows],
        documents=[r["text"] for r in rows],          # store the ORIGINAL text
        embeddings=[v.tolist() for v in embeddings],
        metadatas=[to_metadata_nid(r) for r in rows],
    )
    print(f"\n✓ stored {coll.count()} chunks in ChromaDB at ./{DB_DIR}")
    print("  next: python query_nid.py")


if __name__ == "__main__":
    main()
