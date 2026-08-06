"""
embed_ereturn.py — Phase 2: embed the e-Return corpus into ChromaDB.

Mirrors embed_nid.py / embed_epassport.py.
Source : data/chunks/ereturn.jsonl   (81 bilingual FAQ records)
Target : data/vectordb  -> collection "ereturn"
Model  : BAAI/bge-m3 (multilingual, handles Bangla + English cross-lingual)
"""

from pathlib import Path
import json

import chromadb
from chromadb.utils import embedding_functions

# ---------------------------------------------------------------- config
BASE_DIR = Path(__file__).resolve().parent

CHUNKS_FILE = BASE_DIR / "data" / "chunks" / "ereturn.jsonl"
VECTORDB_DIR = BASE_DIR / "data" / "vectordb"
COLLECTION_NAME = "ereturn"
MODEL_NAME = "BAAI/bge-m3"
# bge-m3 is a ~2GB model; long records (the 51-item mandatory-filing list, the
# 33-item tax-free-income list) spike memory hard. 8 keeps peak usage sane.
# Drop to 4 if the process still dies without a traceback.
BATCH_SIZE = 8
# ------------------------------------------------------------------------


def load_chunks(path: Path) -> list[dict]:
    """Read the JSONL corpus, skipping blank lines."""
    records = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise SystemExit(f"Bad JSON on line {line_no} of {path}: {e}")
    return records


def validate(records: list[dict]) -> None:
    """Fail loudly on duplicate ids or missing required fields."""
    required = {"id", "text", "category", "subcategory", "heading",
                "lang", "source_url", "official"}
    ids = [r["id"] for r in records]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise SystemExit(f"Duplicate ids found: {sorted(dupes)}")
    for r in records:
        missing = required - set(r)
        if missing:
            raise SystemExit(f"Record {r.get('id', '<no id>')} missing fields: {missing}")


def to_metadata(r: dict) -> dict:
    """
    Chroma metadata values must be str/int/float/bool.
    Keep the retrieval-useful fields; the embedded text lives separately.
    """
    md = {
        "category": r["category"],
        "subcategory": r["subcategory"],
        "heading": r["heading"],
        "lang": r["lang"],
        "source_url": r["source_url"],
        "official": bool(r["official"]),
        "question_bn": r.get("question_bn", ""),
        "question_en": r.get("question_en", ""),
    }
    # optional flags (only present on some records)
    if "tax_year_stated" in r:
        md["tax_year_stated"] = r["tax_year_stated"]
    if "volatile" in r:
        md["volatile"] = bool(r["volatile"])
    if "question_reconstructed" in r:
        md["question_reconstructed"] = bool(r["question_reconstructed"])
    return md


def main() -> None:
    print(f"Loading  : {CHUNKS_FILE}")
    records = load_chunks(CHUNKS_FILE)
    validate(records)
    print(f"Records  : {len(records)}")
    by_sub: dict[str, int] = {}
    for r in records:
        by_sub[r["subcategory"]] = by_sub.get(r["subcategory"], 0) + 1
    for k, v in sorted(by_sub.items()):
        print(f"  - {k}: {v}")

    print(f"Model    : {MODEL_NAME}  (first run downloads ~2GB)")
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)

    VECTORDB_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(VECTORDB_DIR))

    # rebuild the collection from scratch so re-runs are idempotent
    try:
        client.delete_collection(COLLECTION_NAME)
        print(f"Dropped existing collection '{COLLECTION_NAME}'")
    except Exception:
        pass

    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    ids = [r["id"] for r in records]
    documents = [r["text"] for r in records]
    metadatas = [to_metadata(r) for r in records]

    for i in range(0, len(records), BATCH_SIZE):
        collection.add(
            ids=ids[i:i + BATCH_SIZE],
            documents=documents[i:i + BATCH_SIZE],
            metadatas=metadatas[i:i + BATCH_SIZE],
        )
        # flush=True so progress survives an abrupt kill (e.g. OOM)
        print(f"  embedded {min(i + BATCH_SIZE, len(records))}/{len(records)}", flush=True)

    # Fail loudly on a short collection. Without this, an interrupted run leaves
    # a partial collection that queries happily and silently returns bad results.
    final_count = collection.count()
    if final_count != len(records):
        raise SystemExit(
            f"\nFAILED: collection holds {final_count} records but the corpus has "
            f"{len(records)}.\nThe embedding run did not complete — do NOT query this "
            f"collection.\nRe-run this script (it rebuilds from scratch); if it dies "
            f"again with no traceback, lower BATCH_SIZE."
        )

    print(f"\nDone. Collection '{COLLECTION_NAME}' now holds {final_count} records.")
    print(f"Vector DB: {VECTORDB_DIR}")


if __name__ == "__main__":
    main()
