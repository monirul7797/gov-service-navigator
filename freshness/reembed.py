"""
freshness/reembed.py — Chroma upsert for human-approved records (Part A §A7).

Reads freshness/approved_for_reembed.json (written only by review.py after a
human accepts a diff AND manually edits the service's data/chunks/<svc>.jsonl).
For each approved record id it re-reads the .jsonl entry and performs a Chroma
UPSERT of that single chunk by its stable id — never a corpus-wide re-embed.
`upsert` (not `add`) makes this idempotent: re-running is safe.

Approved entries are cleared once re-embedded.

Safety rails (§A1 / Part C):
  * the ONLY data/vectordb write path in freshness/, and only for ids that
    passed the review gate
  * requires --execute; without it the run is a dry-run printout
  * a record id missing from the .jsonl aborts the run loudly (stale gate
    entry) rather than silently skipping

Usage (from anywhere):
    python freshness/reembed.py --list       # show approved queue
    python freshness/reembed.py              # dry run
    python freshness/reembed.py --execute    # actually upsert
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fetch  # noqa: E402  (repo-root constant + corpus paths)

import chromadb                 # noqa: E402
from sentence_transformers import SentenceTransformer   # noqa: E402

REPO_ROOT = fetch.REPO_ROOT
FRESHNESS_DIR = os.path.join(REPO_ROOT, "freshness")
APPROVED_PATH = os.path.join(FRESHNESS_DIR, "approved_for_reembed.json")
DB_DIR = os.path.join(REPO_ROOT, "data", "vectordb")
MODEL_NAME = "BAAI/bge-m3"   # same model as every embed_<svc>.py — no exceptions

CORPUS_PATHS = {svc: f"{REPO_ROOT}/data/chunks/{svc}.jsonl" for svc in fetch.CORPUS_PATHS}


def load_approved() -> list[dict]:
    if not os.path.exists(APPROVED_PATH):
        return []
    with open(APPROVED_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_approved(approved: list[dict]) -> None:
    with open(APPROVED_PATH, "w", encoding="utf-8") as f:
        json.dump(approved, f, ensure_ascii=False, indent=2)


def read_corpus(svc: str) -> dict[str, dict]:
    records = {}
    with open(CORPUS_PATHS[svc], encoding="utf-8") as f:
        for line in f:
            if line.strip():
                r = json.loads(line)
                records[r["id"]] = r
    return records


# --- per-service metadata builders, mirroring embed_<svc>.py -----------------
# (Chroma metadata values must be str/int/float/bool — no None, no lists.)

def metadata_nid(r: dict) -> dict:
    return {
        "category": r["category"],
        "heading": r["heading"],
        "lang": r["lang"],
        "source_url": r["source_url"],
        "official": r["official"],
    }


def metadata_epassport(r: dict) -> dict:
    return {
        "category": r["category"],
        "subcategory": r.get("subcategory", r["category"]),
        "heading": r["heading"],
        "lang": r["lang"],
        "source_url": r["source_url"],
        "official": r["official"],
    }


def _faq_metadata(r: dict) -> dict:
    return {
        "category": r["category"],
        "subcategory": r["subcategory"],
        "heading": r["heading"],
        "lang": r["lang"],
        "source_url": r["source_url"],
        "official": bool(r["official"]),
        "question_bn": r.get("question_bn", ""),
        "question_en": r.get("question_en", ""),
    }


def metadata_ereturn(r: dict) -> dict:
    return _faq_metadata(r)


def metadata_bdris(r: dict) -> dict:
    md = _faq_metadata(r)
    # BDRIS-specific optional fields (only on some records, per embed_bdris.py)
    if "rule_no" in r:
        md["rule_no"] = str(r["rule_no"])
    if "stage" in r:
        md["stage"] = int(r["stage"])
    if "source_file" in r:
        md["source_file"] = r["source_file"]
    if "group_topic" in r:
        md["group_topic"] = r["group_topic"]
    if r.get("source_url_unverified"):
        md["source_url_unverified"] = True
    return md


METADATA_BUILDERS = {
    "nid": metadata_nid,
    "epassport": metadata_epassport,
    "ereturn": metadata_ereturn,
    "bdris": metadata_bdris,
}


def main() -> None:
    ap = argparse.ArgumentParser(description="Chroma upsert of human-approved corpus fixes")
    ap.add_argument("--execute", action="store_true",
                    help="actually upsert into data/vectordb (default: dry run)")
    ap.add_argument("--list", action="store_true", help="show the approved queue and exit")
    args = ap.parse_args()

    approved = load_approved()
    if args.list:
        print(json.dumps(approved, indent=2))
        return
    if not approved:
        print("No approved records. Run python freshness/review.py after a "
              "freshness check finds drift.")
        return

    # Group by service so the model and client load exactly once.
    by_service: dict[str, list[dict]] = {}
    for entry in approved:
        by_service.setdefault(entry["service"], []).append(entry)

    if not args.execute:
        print("DRY RUN — pass --execute to upsert. Would re-embed:")
        for svc, entries in by_service.items():
            for e in entries:
                print(f"  [{svc}] {e['record_id']}  ({e.get('source_url', '')})")
        return

    print(f"loading model: {MODEL_NAME} …")
    model = SentenceTransformer(MODEL_NAME)
    client = chromadb.PersistentClient(path=DB_DIR)

    still_pending: list[dict] = []
    for svc, entries in by_service.items():
        corpus = read_corpus(svc)
        coll = client.get_collection(svc)
        for entry in entries:
            rid = entry["record_id"]
            rec = corpus.get(rid)
            if rec is None:
                # Stale gate entry (id not in corpus) — abort loudly, keep the
                # entry so a human can look at it. Never guess a replacement.
                raise SystemExit(f"record {rid!r} not found in {CORPUS_PATHS[svc]} — "
                                 f"approved entry is stale; fix the corpus or the gate file.")
            text = rec["text"]
            vector = model.encode([text], normalize_embeddings=True)[0]
            coll.upsert(
                ids=[rid],
                documents=[text],
                embeddings=[vector.tolist()],
                metadatas=[METADATA_BUILDERS[svc](rec)],
            )
            got = coll.get(ids=[rid], include=["documents"])
            assert got["documents"][0] == text, f"upsert verify failed for {rid}"
            print(f"✓ upserted [{svc}] {rid} (collection now {coll.count()} chunks)")

    # Clear the entries we just re-embedded; keep any not attempted (none today,
    # since every service in the gate file was processed above).
    save_approved(still_pending if still_pending else [])
    print("approved queue cleared.")


if __name__ == "__main__":
    main()
