"""
eval_ereturn_frontdoor.py — targeted eval: runs ereturn_gold_queries_frontdoor.json
against the live e-Return collection to give a definitive, repeatable answer on
the "front-door landing / helpline" coverage question flagged in the project
notes — replacing eyeballed pattern-matching with an actual similarity check.

Adapted directly from eval_bdris.py's pass/fail/warn logic (see that file for
the full rationale comment); the only real differences here are: (1) this
targets the "ereturn" collection instead of "bdris", (2) it checks
"subcategory" metadata instead of "category" since that's the field
embed_ereturn.py actually writes, and (3) the gold set is deliberately small
and scoped to one open question rather than a full-corpus regression suite.

WHAT COUNTS AS PASS/FAIL:
  - expected_subcategory is checked for every query with one set.
  - expected_id, when set, must appear in the top_k results — a
    same-subcategory-but-wrong-record hit is a FAIL.
  - expected_subcategory=null (the front-door probes) pass if NOTHING in the
    top_k clears a baseline similarity (0.55) — i.e. confirms the corpus has
    no strong front-door content, rather than forcing a false match.
  - min_similarity is a SOFT floor — falling short WARNS, not FAILS.

Run (after embed_ereturn.py has already been run once, which it has):
    python eval_ereturn_frontdoor.py
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

import chromadb
from chromadb.utils import embedding_functions

BASE_DIR = Path(__file__).resolve().parent
VECTORDB_DIR = BASE_DIR / "data" / "vectordb"
GOLD_FILE = BASE_DIR / "ereturn_gold_queries_frontdoor.json"
COLLECTION_NAME = "ereturn"
MODEL_NAME = "BAAI/bge-m3"

OUT_OF_SCOPE_CEILING = 0.55  # for expected_subcategory=null probes (front-door)


def load_gold(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"No gold query file at {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def get_collection():
    if not VECTORDB_DIR.exists():
        raise SystemExit(f"No vector DB at {VECTORDB_DIR}\nRun embed_ereturn.py first.")
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)
    client = chromadb.PersistentClient(path=str(VECTORDB_DIR))
    try:
        return client.get_collection(name=COLLECTION_NAME, embedding_function=ef)
    except Exception:
        raise SystemExit(f"Collection '{COLLECTION_NAME}' not found.\nRun embed_ereturn.py first.")


def eval_one(collection, gq: dict, top_k: int) -> dict:
    res = collection.query(query_texts=[gq["query"]], n_results=top_k)
    ids = res["ids"][0]
    metas = res["metadatas"][0]
    dists = res["distances"][0]
    sims = [1 - d for d in dists]

    result = {
        "id": gq["id"], "query": gq["query"], "lang": gq["lang"],
        "top_hits": list(zip(ids, [m.get("subcategory") for m in metas], [round(s, 3) for s in sims])),
        "verdict": None, "reasons": [],
    }

    # --- front-door probe: nothing should clear the ceiling ---
    if gq["expected_subcategory"] is None:
        best_sim = sims[0] if sims else 0.0
        if best_sim < OUT_OF_SCOPE_CEILING:
            result["verdict"] = "PASS"
            result["reasons"].append(
                f"correctly found no strong match (best sim={best_sim:.3f} < {OUT_OF_SCOPE_CEILING}) "
                f"— confirms the front-door/homepage gap"
            )
        else:
            result["verdict"] = "FAIL (gap may be closed)"
            result["reasons"].append(
                f"expected no strong match, but got sim={best_sim:.3f} >= {OUT_OF_SCOPE_CEILING} "
                f"(top hit: {ids[0]} [{metas[0].get('subcategory')}]) — the corpus may already "
                f"cover this better than the project notes assumed; inspect the hit before concluding a gap"
            )
        return result

    # --- subcategory check ---
    subs_hit = {m.get("subcategory") for m in metas}
    sub_ok = gq["expected_subcategory"] in subs_hit
    if not sub_ok:
        result["verdict"] = "FAIL"
        result["reasons"].append(
            f"expected subcategory '{gq['expected_subcategory']}' not in top-{top_k} "
            f"(got: {sorted(subs_hit)})"
        )
        return result

    # --- record check ---
    if gq.get("expected_id"):
        record_ok = gq["expected_id"] in ids
        if not record_ok:
            result["verdict"] = "FAIL"
            result["reasons"].append(
                f"subcategory matched but expected record '{gq['expected_id']}' not in top-{top_k} "
                f"(got ids: {ids})"
            )
            return result
        result["reasons"].append(f"expected record '{gq['expected_id']}' found — confirms this content IS covered")

    result["verdict"] = "PASS"
    min_sim = gq.get("min_similarity")
    if min_sim is not None:
        best_relevant_sim = max(
            (s for i, s in zip(ids, sims) if
             (i == gq.get("expected_id")) or (not gq.get("expected_id"))),
            default=sims[0],
        )
        if best_relevant_sim < min_sim:
            result["verdict"] = "PASS (warn)"
            result["reasons"].append(
                f"similarity {best_relevant_sim:.3f} below expected floor {min_sim} — "
                f"thin coverage for this exact phrasing, not necessarily broken retrieval"
            )
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the e-Return front-door/helpline gap probe.")
    args = ap.parse_args()

    gold = load_gold(GOLD_FILE)
    collection = get_collection()
    top_k = gold.get("top_k", 4)

    print(f"Collection: '{COLLECTION_NAME}' ({collection.count()} chunks)")
    print(f"Gold file : {GOLD_FILE.name} ({len(gold['queries'])} queries, top_k={top_k})")
    print("=" * 78)

    results = []
    for gq in gold["queries"]:
        r = eval_one(collection, gq, top_k)
        results.append(r)
        tag = {"PASS": "✓", "PASS (warn)": "△", "FAIL": "✗", "FAIL (gap may be closed)": "✗"}[r["verdict"]]
        print(f"{tag} [{r['verdict']:24}] {r['id']} ({r['lang']}): {r['query'][:55]}")
        for reason in r["reasons"]:
            print(f"    {reason}")
        print(f"    top hits: {r['top_hits']}")

    passes = sum(1 for r in results if r["verdict"] == "PASS")
    warns = sum(1 for r in results if r["verdict"] == "PASS (warn)")
    fails = sum(1 for r in results if "FAIL" in r["verdict"])
    total = len(results)

    print("=" * 78)
    print(f"Results: {passes} PASS, {warns} PASS-with-warning, {fails} FAIL, out of {total}")
    print()
    print("INTERPRETATION FOR THE FRONT-DOOR/HELPLINE QUESTION:")
    print("  - If gq_er_001/002 PASS: helpline is genuinely covered, not a gap.")
    print("  - If gq_er_003/004/006 PASS (found nothing strong): front-door homepage")
    print("    description IS the real, still-open gap — worth closing with a new chunk.")
    print("  - If gq_er_003/004/006 FAIL (found something strong): the front-door gap")
    print("    may already be closed by existing content — inspect the reported top hit.")
    if fails:
        sys.exit(1)


if __name__ == "__main__":
    main()
