"""
eval_epassport.py — gold query evaluation for the e-Passport collection.

Adapted from eval_bdris.py's pass/fail/warn logic (see that file for full
rationale) and eval_nid.py's single-shot SentenceTransformer pattern, matching
the original embed_epassport.py you shared earlier this session (category/
subcategory/heading/lang/source_url/official metadata, COLLECTION = "epassport").

WHAT COUNTS AS PASS/FAIL:
  - expected_category checked for every query with one set.
  - expected_id, when set, must appear in top_k — a same-category-wrong-record
    hit is a FAIL. This matters a lot here: e-Passport's fee records are
    page-count/validity/delivery-tier specific, so a wrong match gives a
    factually wrong BDT/USD figure, not just a stylistically different answer.
  - expected_category=null + expected_id=null + a real min_similarity is an
    "observational" probe: reported but doesn't gate pass/fail.
  - expected_category=null + min_similarity=null is the out-of-scope probe:
    passes if nothing clears OUT_OF_SCOPE_CEILING.
  - min_similarity is a soft floor — WARN, not FAIL.

One record in this gold set (epassport-notice-dhaka-cantonment-jurisdiction)
is already flagged source_url_unverified: true in the corpus itself — gq_ep_010
targets it specifically to check it's at least retrievable, independent of
the separate open question of whether its source URL is confirmed.

Run (after embed_epassport.py):
    python eval_epassport.py
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

import chromadb
from sentence_transformers import SentenceTransformer

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "data" / "vectordb"
GOLD_FILE = BASE_DIR / "epassport_gold_queries.json"
COLLECTION = "epassport"
MODEL_NAME = "BAAI/bge-m3"

OUT_OF_SCOPE_CEILING = 0.55
IS_E5 = "e5" in MODEL_NAME.lower()


def as_query(text: str) -> str:
    return f"query: {text}" if IS_E5 else text


def load_gold(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"No gold query file at {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_collection():
    if not DB_DIR.exists():
        raise SystemExit(f"No vector DB at {DB_DIR}\nRun embed_epassport.py first.")
    client = chromadb.PersistentClient(path=str(DB_DIR))
    try:
        return client.get_collection(COLLECTION)
    except Exception:
        raise SystemExit(f"Collection '{COLLECTION}' not found.\nRun embed_epassport.py first.")


def eval_one(model, collection, gq: dict, top_k: int) -> dict:
    emb = model.encode([as_query(gq["query"])], normalize_embeddings=True)
    res = collection.query(
        query_embeddings=[emb[0].tolist()], n_results=top_k,
        include=["documents", "metadatas", "distances"],
    )
    ids = res["ids"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    sims = [1 - d for d in dists]

    result = {
        "id": gq["id"], "query": gq["query"], "lang": gq["lang"],
        "top_hits": list(zip(ids, [m.get("category") for m in metas], [round(s, 3) for s in sims])),
        "verdict": None, "reasons": [],
    }

    # --- out-of-scope probe ---
    if gq["expected_category"] is None and gq.get("expected_id") is None and gq.get("min_similarity") is None:
        best_sim = sims[0] if sims else 0.0
        if best_sim < OUT_OF_SCOPE_CEILING:
            result["verdict"] = "PASS"
            result["reasons"].append(f"correctly found no strong match (best sim={best_sim:.3f} < {OUT_OF_SCOPE_CEILING})")
        else:
            result["verdict"] = "FAIL"
            result["reasons"].append(
                f"expected no strong match, but got sim={best_sim:.3f} >= {OUT_OF_SCOPE_CEILING} "
                f"(top hit: {ids[0]} [{metas[0].get('category')}])"
            )
        return result

    # --- observational probe ---
    if gq["expected_category"] is None and gq.get("expected_id") is None:
        result["verdict"] = "OBSERVE"
        top_sim = sims[0] if sims else 0.0
        result["reasons"].append(f"observational only, no pass/fail gate — top sim={top_sim:.3f}, top id={ids[0] if ids else 'none'}")
        return result

    # --- category check ---
    cats_hit = {m.get("category") for m in metas}
    if gq["expected_category"] not in cats_hit:
        result["verdict"] = "FAIL"
        result["reasons"].append(
            f"expected category '{gq['expected_category']}' not in top-{top_k} (got: {sorted(cats_hit)})"
        )
        return result

    # --- record check ---
    if gq.get("expected_id"):
        if gq["expected_id"] not in ids:
            result["verdict"] = "FAIL"
            result["reasons"].append(
                f"category matched but expected record '{gq['expected_id']}' not in top-{top_k} (got ids: {ids})"
            )
            return result
        result["reasons"].append(f"expected record '{gq['expected_id']}' found")
    else:
        result["reasons"].append("category-level match only (expected_id not set) — OK")

    result["verdict"] = "PASS"
    min_sim = gq.get("min_similarity")
    if min_sim is not None:
        best_relevant_sim = max(
            (s for i, s in zip(ids, sims) if (i == gq.get("expected_id")) or (not gq.get("expected_id"))),
            default=sims[0],
        )
        if best_relevant_sim < min_sim:
            result["verdict"] = "PASS (warn)"
            result["reasons"].append(
                f"similarity {best_relevant_sim:.3f} below expected floor {min_sim} — likely thin coverage, not broken retrieval"
            )
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the e-Passport gold query eval.")
    ap.add_argument("--fail-fast", action="store_true")
    args = ap.parse_args()

    gold = load_gold(GOLD_FILE)
    print(f"loading model: {MODEL_NAME}...")
    model = SentenceTransformer(MODEL_NAME)
    collection = get_collection()
    top_k = gold.get("top_k", 4)

    print(f"Collection: '{COLLECTION}' ({collection.count()} chunks)")
    print(f"Gold file : {GOLD_FILE.name} ({len(gold['queries'])} queries, top_k={top_k})")
    print("=" * 78)

    results = []
    for gq in gold["queries"]:
        r = eval_one(model, collection, gq, top_k)
        results.append(r)
        tag = {"PASS": "✓", "PASS (warn)": "△", "FAIL": "✗", "OBSERVE": "○"}[r["verdict"]]
        print(f"{tag} [{r['verdict']:11}] {r['id']} ({r['lang']}): {r['query'][:55]}")
        for reason in r["reasons"]:
            print(f"    {reason}")
        if r["verdict"] in ("FAIL", "OBSERVE"):
            print(f"    top hits: {r['top_hits']}")
        if args.fail_fast and r["verdict"] == "FAIL":
            print("\n--fail-fast: stopping at first FAIL.")
            break

    passes = sum(1 for r in results if r["verdict"] == "PASS")
    warns = sum(1 for r in results if r["verdict"] == "PASS (warn)")
    fails = sum(1 for r in results if r["verdict"] == "FAIL")
    observed = sum(1 for r in results if r["verdict"] == "OBSERVE")
    total = len(results)

    print("=" * 78)
    print(f"Results: {passes} PASS, {warns} PASS-with-warning, {fails} FAIL, {observed} OBSERVE, out of {total}")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
