"""
query_bdris.py — Phase 2: query the BDRIS collection in ChromaDB.

Mirrors query_ereturn.py (partial-collection guard, low-similarity note,
--sub category filtering) rather than the older query_nid.py / query_epassport.py
pattern, to match embed_bdris.py's e-Return-based approach.

Reads  : data/vectordb -> collection "bdris"  (built by embed_bdris.py)
Model  : BAAI/bge-m3 (same model as embedding — must match)

Usage:
    python query_bdris.py                                  # interactive REPL
    python query_bdris.py "কিভাবে জন্ম নিবন্ধন করব?"
    python query_bdris.py "how do I correct my parents' names?" --n 3
    python query_bdris.py "fee" --sub bdris_rules2018
"""

from pathlib import Path
import argparse

import chromadb
from chromadb.utils import embedding_functions

# ---------------------------------------------------------------- config
BASE_DIR = Path(__file__).resolve().parent

VECTORDB_DIR = BASE_DIR / "data" / "vectordb"
CHUNKS_FILE = BASE_DIR / "data" / "chunks" / "bdris.jsonl"
COLLECTION_NAME = "bdris"
MODEL_NAME = "BAAI/bge-m3"
DEFAULT_N_RESULTS = 5
SNIPPET_CHARS = 400

# BDRIS's 6 categories, for --sub filtering
CATEGORIES = [
    "bdris_faq", "bdris_application_process", "bdris_manual_walkthrough",
    "bdris_correction", "bdris_login_instructions", "bdris_rules2018",
]

# Demo probes span English, Bangla, and Banglish, and touch every category —
# a quick sanity check that the whole corpus is actually reachable, not just
# whichever category was embedded first.
DEMO_QUERIES = [
    "How do I register a birth online?",                          # manual_walkthrough
    "পিতা মাতার নাম সংশোধন কিভাবে করব?",                          # correction
    "amar login attempt fail hoye geche, koto khon wait korbo?",  # login_instructions (Banglish)
    "What documents do I need to correct birth registration info?",  # faq
    "জন্ম নিবন্ধনের ফি কত?",                                       # rules2018 (fees)
    "possible duplicate dile ki korbo?",                            # faq (Banglish)
]
# ------------------------------------------------------------------------


def get_collection():
    """Open the persistent collection, with a clear error if it isn't built yet."""
    if not VECTORDB_DIR.exists():
        raise SystemExit(
            f"No vector DB at {VECTORDB_DIR}\nRun build_bdris_corpus.py then embed_bdris.py first."
        )
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)
    client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
    try:
        collection = client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
    except Exception:
        raise SystemExit(
            f"Collection '{COLLECTION_NAME}' not found in {VECTORDB_DIR}\n"
            f"Run embed_bdris.py first."
        )
    _warn_if_partial(collection)
    return collection


def _warn_if_partial(collection) -> None:
    """
    Compare the collection against the source corpus. A partial collection
    (from an interrupted embed run) queries fine but silently returns bad
    retrieval, so refuse rather than mislead.
    """
    if not CHUNKS_FILE.exists():
        return  # corpus not alongside; nothing to compare against
    expected = sum(1 for line in CHUNKS_FILE.open(encoding="utf-8") if line.strip())
    actual = collection.count()
    if actual != expected:
        raise SystemExit(
            f"Collection '{COLLECTION_NAME}' holds {actual} records but "
            f"{CHUNKS_FILE.name} has {expected}.\n"
            f"The embedding run did not complete — results would be missing "
            f"{expected - actual} records.\nRe-run embed_bdris.py before querying."
        )


def search(collection, query: str, n_results: int = DEFAULT_N_RESULTS,
           subcategory: str | None = None) -> None:
    """Run one similarity search and print the hits."""
    where = {"subcategory": subcategory} if subcategory else None

    res = collection.query(
        query_texts=[query],
        n_results=n_results,
        where=where,
    )

    ids = res["ids"][0]
    docs = res["documents"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]

    if not ids:
        print("No results.")
        return

    print(f"\nQuery: {query}")
    if subcategory:
        print(f"Filter: subcategory = {subcategory}")
    print("=" * 78)

    for rank, (id_, doc, md, dist) in enumerate(zip(ids, docs, metas, dists), 1):
        sim = 1 - dist  # cosine distance -> similarity
        print(f"\n[{rank}] similarity {sim:.4f}   id: {id_}")
        print(f"    category    : {md.get('category')}")
        print(f"    question(bn): {md.get('question_bn', '')}")
        print(f"    question(en): {md.get('question_en', '')}")
        if md.get("rule_no"):
            print(f"    rule_no     : {md.get('rule_no')}")
        if md.get("stage") is not None and md.get("stage") != "":
            print(f"    stage       : {md.get('stage')}")
        if md.get("source_url_unverified"):
            print(f"    ⚠ note      : source_url unverified")
        print(f"    source      : {md.get('source_url')}")
        snippet = doc[:SNIPPET_CHARS].replace("\n", " ")
        ellipsis = "..." if len(doc) > SNIPPET_CHARS else ""
        print(f"    text        : {snippet}{ellipsis}")

    top_sim = 1 - dists[0]
    if top_sim < 0.70:
        print(
            f"\nNote: top similarity {top_sim:.4f} is low — this usually means the "
            f"corpus lacks coverage for this question, not that retrieval is broken."
        )
    print()


def run_demo(collection, n_results: int = 4) -> None:
    """Run the canned probes so a fresh collection can be sanity-checked at a glance."""
    print("=" * 78)
    print("DEMO QUERIES  (English · Bangla · Banglish → same knowledge base)")
    print("=" * 78)
    for q in DEMO_QUERIES:
        res = collection.query(query_texts=[q], n_results=n_results)
        print(f"\nQ: {q}")
        for rank, (id_, doc, md, dist) in enumerate(
            zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]), 1
        ):
            sim = 1 - dist
            print(f"  {rank}. sim={sim:.3f}  [{md.get('category')}]  ({md.get('lang')})")
            print(f"     {md.get('question_bn') or md.get('question_en') or ''}")
            snippet = doc[:120].replace("\n", " ")
            print(f"     {snippet}[...]")
            print(f"     ↳ {md.get('source_url')}")
    print("\n" + "=" * 78)


def repl(collection) -> None:
    """Interactive loop; blank line or 'exit' quits."""
    print("Ask your own (blank line to quit). Try a Bangla question and an English one.")
    print("=" * 78)
    while True:
        try:
            q = input("\nquery> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q or q.lower() in {"exit", "quit"}:
            break
        search(collection, q)


def main() -> None:
    ap = argparse.ArgumentParser(description="Query the BDRIS ChromaDB collection.")
    ap.add_argument("query", nargs="*", help="query text (omit for demo + interactive mode)")
    ap.add_argument("--n", type=int, default=DEFAULT_N_RESULTS, help="number of results")
    ap.add_argument("--sub", choices=CATEGORIES, help="filter by category")
    ap.add_argument("--no-demo", action="store_true", help="skip the demo probes")
    args = ap.parse_args()

    collection = get_collection()

    if args.query:
        search(collection, " ".join(args.query), n_results=args.n, subcategory=args.sub)
        return

    print(f"loaded collection '{COLLECTION_NAME}' with {collection.count()} chunks")
    if not args.no_demo:
        run_demo(collection)
    repl(collection)


if __name__ == "__main__":
    main()
