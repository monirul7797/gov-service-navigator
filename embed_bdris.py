"""
embed_bdris.py — Phase 2: embed the BDRIS corpus into ChromaDB.

Mirrors embed_ereturn.py's batched pattern (not the older embed_nid.py /
embed_epassport.py single-shot pattern) — batched writes survive an interrupted
run with partial progress intact, and a final count-check refuses to leave a
short collection queryable. BDRIS's manual_walkthrough and rules2018 records run
long (full rule text, multi-figure walkthrough stages), the same memory-spike
risk profile e-Return hit at batch 32/81 — so this pattern matters here too, not
just cosmetically.

Source : data/chunks/bdris.jsonl   (built by build_bdris_corpus.py — run that
         FIRST if this file doesn't exist yet or looks stale)
Target : data/vectordb  -> collection "bdris"
Model  : BAAI/bge-m3 (multilingual, handles Bangla + English cross-lingual)
"""

from pathlib import Path
import json

import chromadb
from chromadb.utils import embedding_functions

# ---------------------------------------------------------------- config
BASE_DIR = Path(__file__).resolve().parent

CHUNKS_FILE = BASE_DIR / "data" / "chunks" / "bdris.jsonl"
VECTORDB_DIR = BASE_DIR / "data" / "vectordb"
COLLECTION_NAME = "bdris"
MODEL_NAME = "BAAI/bge-m3"
# Same reasoning as e-Return: bge-m3 is a ~2GB model, and BDRIS's longest records
# (rules2018 Rule 15's multi-branch text, manual_walkthrough's stage summaries)
# are comparably dense to e-Return's flagged long records. 8 keeps peak usage
# sane; drop to 4 if the process still dies without a traceback.
BATCH_SIZE = 8
# ------------------------------------------------------------------------


def load_chunks(path: Path) -> list[dict]:
    """Read the JSONL corpus, skipping blank lines."""
    if not path.exists():
        raise SystemExit(
            f"No corpus at {path}\n"
            f"Run build_bdris_corpus.py first to normalize the 6 category "
            f"files into bdris.jsonl."
        )
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
    # BDRIS-specific optional fields (only present on some records, per category)
    if "rule_no" in r:
        md["rule_no"] = str(r["rule_no"])       # keep as string; some rule_nos aren't purely numeric
    if "stage" in r:
        md["stage"] = int(r["stage"])
    if "source_file" in r:
        md["source_file"] = r["source_file"]
    if "group_topic" in r:
        md["group_topic"] = r["group_topic"]
    if r.get("source_url_unverified"):
        md["source_url_unverified"] = True
    return md


def main() -> None:
    print(f"Loading  : {CHUNKS_FILE}")
    records = load_chunks(CHUNKS_FILE)
    validate(records)
    print(f"Records  : {len(records)}")
    by_cat: dict[str, int] = {}
    for r in records:
        by_cat[r["category"]] = by_cat.get(r["category"], 0) + 1
    for k, v in sorted(by_cat.items(), key=lambda x: -x[1]):
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
