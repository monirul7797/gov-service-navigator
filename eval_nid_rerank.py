"""
eval_nid_rerank.py — the NID gold eval, run through two-stage retrieval.

Same gold set and same PASS/FAIL/WARN/OBSERVE semantics as eval_nid.py, but the
candidate ordering now comes from rerank_retrieval.retrieve() (dense recall -> cross-
encoder rerank) instead of raw dense top-k. This lets us compare, query by query,
whether reranking fixes the "right record recalled but mis-ordered" failures.

KEY DIAGNOSTIC
    For every query that names an expected_id, the report prints that record's position
    in the dense candidate pool BEFORE reranking and its position AFTER. That single line
    tells us which world we're in:
      - "dense#7 -> rerank#2"  => dense recalled it, rerank promoted it. Reranking works.
      - "dense#none"           => dense never recalled it within candidate_n. Reranking
                                  CANNOT help; this needs a lexical/BM25 hybrid or a wider
                                  candidate_n, not a better reranker.

SCORE SCALES (kept distinct on purpose)
    - Category/record pass-fail uses the RERANKED top-k order.
    - The min_similarity soft floor and the out-of-scope ceiling stay on DENSE cosine
      similarity, because those thresholds were calibrated on that scale and still mean
      "how much real dense coverage exists" — the reranker only reorders, it doesn't
      create coverage.

Run (after embed_nid.py; downloads bge-reranker-v2-m3 on first run, ~600MB):
    python eval_nid_rerank.py
    python eval_nid_rerank.py --candidate-n 50   # try a wider recall pool
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys

from rerank_retrieval import (
    dense_candidates, rerank, get_client,
    DEFAULT_CANDIDATE_N,
)

BASE_DIR = Path(__file__).resolve().parent
GOLD_FILE = BASE_DIR / "nid_gold_queries.json"
COLLECTION = "nid"
OUT_OF_SCOPE_CEILING = 0.55


def load_gold(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"No gold query file at {path}")
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _rank_of(records: list[dict], rec_id: str, key: str) -> str:
    for r in records:
        if r["id"] == rec_id:
            return f"#{r[key]}"
    return "none"


def eval_one(gq: dict, top_k: int, candidate_n: int) -> dict:
    # stage 1: wide dense recall (kept, so we can report pre-rerank position)
    cands = dense_candidates(COLLECTION, gq["query"], candidate_n)
    # stage 2: rerank the whole pool
    ranked = rerank(gq["query"], cands)
    final = ranked[:top_k]

    ids = [c["id"] for c in final]
    metas_cats = [c["category"] for c in final]
    dense_sims_final = [c["dense_sim"] for c in final]
    best_dense_sim = max((c["dense_sim"] for c in cands), default=0.0)

    result = {
        "id": gq["id"], "query": gq["query"], "lang": gq["lang"],
        "top_hits": [(c["id"], c["category"], round(c["rerank_score"], 3), round(c["dense_sim"], 3)) for c in final],
        "verdict": None, "reasons": [],
    }

    # diagnostic: where did the expected record move?
    eid = gq.get("expected_id")
    if eid:
        moved = f"expected {eid}: dense {_rank_of(cands, eid, 'dense_rank')} -> rerank {_rank_of(ranked, eid, 'rerank_rank')}"
        result["reasons"].append(moved)

    # --- out-of-scope probe (no category, no id, no min_similarity) ---
    if gq["expected_category"] is None and gq.get("expected_id") is None and gq.get("min_similarity") is None:
        if best_dense_sim < OUT_OF_SCOPE_CEILING:
            result["verdict"] = "PASS"
            result["reasons"].append(f"correctly found no strong match (best dense sim={best_dense_sim:.3f} < {OUT_OF_SCOPE_CEILING})")
        else:
            result["verdict"] = "FAIL"
            result["reasons"].append(f"expected no strong match, but best dense sim={best_dense_sim:.3f} >= {OUT_OF_SCOPE_CEILING}")
        return result

    # --- observational probe (no category/id but has a min_similarity) ---
    if gq["expected_category"] is None and gq.get("expected_id") is None:
        result["verdict"] = "OBSERVE"
        top = final[0] if final else None
        result["reasons"].append(
            f"observational only — top rerank id={top['id'] if top else 'none'} "
            f"(dense_sim={top['dense_sim']:.3f})" if top else "observational only — no hits")
        return result

    # --- category check (on reranked top-k) ---
    cats_hit = set(metas_cats)
    if gq["expected_category"] not in cats_hit:
        result["verdict"] = "FAIL"
        result["reasons"].append(
            f"expected category '{gq['expected_category']}' not in reranked top-{top_k} (got: {sorted(c for c in cats_hit if c)})"
        )
        return result

    # --- record check (on reranked top-k) ---
    if gq.get("expected_id"):
        if gq["expected_id"] not in ids:
            result["verdict"] = "FAIL"
            result["reasons"].append(
                f"category matched but expected record '{gq['expected_id']}' not in reranked top-{top_k} (got ids: {ids})"
            )
            return result
        result["reasons"].append(f"expected record '{gq['expected_id']}' found in reranked top-{top_k}")
    else:
        result["reasons"].append("category-level match only (expected_id not set) — OK")

    result["verdict"] = "PASS"

    # --- min_similarity soft floor, on DENSE sim of the chosen record(s) ---
    min_sim = gq.get("min_similarity")
    if min_sim is not None:
        if gq.get("expected_id"):
            rel = [s for i, s in zip(ids, dense_sims_final) if i == gq["expected_id"]]
            best_relevant = max(rel) if rel else (dense_sims_final[0] if dense_sims_final else 0.0)
        else:
            best_relevant = dense_sims_final[0] if dense_sims_final else 0.0
        if best_relevant < min_sim:
            result["verdict"] = "PASS (warn)"
            result["reasons"].append(
                f"dense similarity {best_relevant:.3f} below floor {min_sim} — thin coverage, not broken retrieval"
            )
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the NID gold eval through two-stage rerank retrieval.")
    ap.add_argument("--candidate-n", type=int, default=DEFAULT_CANDIDATE_N,
                    help="dense recall depth before reranking (default %(default)s)")
    ap.add_argument("--fail-fast", action="store_true")
    args = ap.parse_args()

    gold = load_gold(GOLD_FILE)
    top_k = gold.get("top_k", 4)
    collection = get_client().get_collection(COLLECTION)

    print("loading models: BAAI/bge-m3 (embed) + BAAI/bge-reranker-v2-m3 (rerank)...")
    print(f"Collection: '{COLLECTION}' ({collection.count()} chunks)")
    print(f"Gold file : {GOLD_FILE.name} ({len(gold['queries'])} queries)")
    print(f"Retrieval : dense recall {args.candidate_n} -> cross-encoder rerank -> top-{top_k}")
    print("=" * 78)

    results = []
    for gq in gold["queries"]:
        r = eval_one(gq, top_k, args.candidate_n)
        results.append(r)
        tag = {"PASS": "\u2713", "PASS (warn)": "\u25b3", "FAIL": "\u2717", "OBSERVE": "\u25cb"}[r["verdict"]]
        print(f"{tag} [{r['verdict']:11}] {r['id']} ({r['lang']}): {r['query'][:55]}")
        for reason in r["reasons"]:
            print(f"    {reason}")
        if r["verdict"] in ("FAIL", "OBSERVE"):
            print(f"    top hits (id, cat, rerank_score, dense_sim): {r['top_hits']}")
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
