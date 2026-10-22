"""Replay of the recorded ChromaDB baseline through ``gsn.evals.judge``.

``tools/m1/export_chroma_baseline.py`` records, for every gold query, the top-k hits the legacy code
returned (ids, unrounded similarities, category / subcategory metadata) and the verdict the legacy
script's own ``eval_one`` gave. ``judge`` is a port of those four ``eval_one`` functions, so feeding it
the recorded hits must give the recorded verdict for every query. This is what makes the later parity
comparison meaningful: a verdict can differ between the two systems only because the *hits* differ.

* The synthetic baseline below covers every rule; its legacy verdicts were worked out by hand from the
  legacy scripts (``eval_nid.py``, ``eval_epassport.py``, ``eval_ereturn.py``, ``eval_bdris.py``).
* The real baseline (``docs/reports/m1-parity/chroma_baseline.json``) is replayed too once it exists.
"""
import json
from pathlib import Path

import pytest

from gsn.evals import RULES, judge
from gsn.retrieval import Hit

REPO_ROOT = Path(__file__).resolve().parents[2]
PARITY_DIR = REPO_ROOT / "docs" / "reports" / "m1-parity"
BASELINE_FILE = PARITY_DIR / "chroma_baseline.json"
CASES_FILE = PARITY_DIR / "retrieve_cases.json"
SERVICES = ("nid", "epassport", "ereturn", "bdris")


def replay(baseline: dict) -> list[tuple[str, str, str, str]]:
    """``(service, query id, recorded legacy verdict, replayed verdict)`` for every gold entry."""
    rows = []
    for entry in baseline["gold"]:
        service = entry["service"]
        hits = [
            Hit(h["id"], service, h["sim"], "", h["category"], h["subcategory"], None, "")
            for h in entry["top"]
        ]
        verdict = judge(RULES[service], entry["gq"], hits, entry["top_k"], service_id=service)
        assert verdict.query_id == entry["id"] == entry["gq"]["id"]
        assert verdict.top == [(h["id"], h["sim"]) for h in entry["top"][: entry["top_k"]]]
        rows.append((service, entry["id"], entry["legacy_verdict"], verdict.verdict))
    return rows


def assert_replay_matches(baseline: dict) -> None:
    for entry in baseline["gold"]:
        if entry["service"] == "ereturn":
            # judge() assumes the subcategory is stored; the legacy script skipped its gate otherwise
            assert entry["subcategory_available"] is True, entry["id"]
    mismatches = [row for row in replay(baseline) if row[2] != row[3]]
    assert not mismatches, "replayed verdict differs from the legacy verdict (service, query, legacy, replay): " + str(
        mismatches
    )


def _top(*hits):
    """(id, sim, category, subcategory) tuples -> the recorded ``top`` list."""
    return [{"id": i, "sim": s, "category": c, "subcategory": sub} for i, s, c, sub in hits]


def _entry(service, gq, top, verdict, *, top_k=4, **extra):
    gq = {"id": gq.pop("id"), "query": "a question", "lang": "en", **gq}
    entry = {
        "service": service,
        "id": gq["id"],
        "gq": gq,
        "top_k": top_k,
        "top": top,
        "legacy_verdict": verdict,
        "legacy_reasons": ["recorded by the legacy script"],
    }
    entry.update(extra)
    return entry


def _synthetic_baseline() -> dict:
    nid_top = _top(("n1", 0.80123456789, "fees", None), ("n2", 0.61, "faq", None), ("n3", 0.52, "eligibility", None))
    gold = [
        # --- nid: category gate, record gate, out-of-scope ceiling 0.55, observational probe
        _entry("nid", {"id": "gq_nid_pass", "expected_category": "fees", "expected_id": "n1", "min_similarity": 0.6},
               nid_top, "PASS"),
        _entry("nid", {"id": "gq_nid_record_missing", "expected_category": "fees", "expected_id": "n9"},
               nid_top, "FAIL"),
        _entry("nid", {"id": "gq_nid_category_missing", "expected_category": "corrections"}, nid_top, "FAIL"),
        _entry("nid", {"id": "gq_nid_oos_pass", "expected_category": None, "expected_id": None, "min_similarity": None},
               _top(("n2", 0.5499999, "faq", None)), "PASS"),
        _entry("nid", {"id": "gq_nid_oos_fail", "expected_category": None, "expected_id": None, "min_similarity": None},
               _top(("n2", 0.55, "faq", None)), "FAIL"),
        _entry("nid", {"id": "gq_nid_observe", "expected_category": None, "expected_id": None, "min_similarity": 0.5},
               nid_top, "OBSERVE"),
        # --- epassport: soft similarity floor downgrades PASS, category-level match
        _entry("epassport", {"id": "gq_ep_warn", "expected_category": "fees-payment", "expected_id": None,
                             "min_similarity": 0.9},
               _top(("e1", 0.7, "fees-payment", "s"), ("e2", 0.6, "notices", "s")), "PASS (warn)"),
        _entry("epassport", {"id": "gq_ep_pass", "expected_category": "notices", "expected_id": "e2",
                             "min_similarity": 0.5},
               _top(("e1", 0.7, "fees-payment", "s"), ("e2", 0.6, "notices", "s")), "PASS"),
        _entry("epassport", {"id": "gq_ep_expected_id_below_floor", "expected_category": "notices",
                             "expected_id": "e2", "min_similarity": 0.65},
               _top(("e1", 0.7, "fees-payment", "s"), ("e2", 0.6, "notices", "s")), "PASS (warn)"),
        # --- ereturn: the gate is the subcategory (every category is "faq")
        _entry("ereturn", {"id": "gq_er_pass", "expected_subcategory": "online_return", "expected_id": "r2"},
               _top(("r1", 0.8, "faq", "income_tax_general"), ("r2", 0.7, "faq", "online_return")), "PASS",
               subcategory_available=True),
        _entry("ereturn", {"id": "gq_er_subcategory_missing", "expected_subcategory": "income_tax_general",
                           "expected_id": "r1"},
               _top(("r2", 0.8, "faq", "online_return"), ("r3", 0.7, "faq", "online_return")), "FAIL",
               subcategory_available=True),
        _entry("ereturn", {"id": "gq_er_oos", "expected_subcategory": None, "expected_id": None,
                           "min_similarity": None},
               _top(("r1", 0.4, "faq", "income_tax_general")), "PASS", subcategory_available=True),
        _entry("ereturn", {"id": "gq_er_observe", "expected_subcategory": None, "expected_id": None,
                           "min_similarity": 0.4},
               _top(("r1", 0.9, "faq", "income_tax_general")), "OBSERVE", subcategory_available=True),
        # --- bdris: out-of-scope ceiling 0.62, and any null category is out-of-scope (no observe probe)
        _entry("bdris", {"id": "gq_oos_between_ceilings", "expected_category": None, "expected_id": None,
                         "min_similarity": None},
               _top(("b1", 0.60, "bdris_faq", "x")), "PASS"),
        _entry("bdris", {"id": "gq_oos_fail", "expected_category": None, "expected_id": None,
                         "min_similarity": None},
               _top(("b1", 0.63, "bdris_faq", "x")), "FAIL"),
        _entry("bdris", {"id": "gq_null_category_with_id", "expected_category": None, "expected_id": "b1",
                         "min_similarity": 0.5},
               _top(("b1", 0.9, "bdris_faq", "x")), "FAIL"),
        _entry("bdris", {"id": "gq_bdris_pass", "expected_category": "bdris_faq", "expected_id": "b1"},
               _top(("b2", 0.7, "bdris_correction", "x"), ("b1", 0.65, "bdris_faq", "x")), "PASS"),
        _entry("bdris", {"id": "gq_bdris_record_missing", "expected_category": "bdris_faq", "expected_id": "b9"},
               _top(("b1", 0.65, "bdris_faq", "x")), "FAIL"),
    ]
    return {"commit": "0" * 40, "environment": {}, "documents": {}, "gold": gold, "retrieve": []}


# ------------------------------------------------------------------ synthetic baseline

def test_the_synthetic_baseline_covers_every_rule_and_every_verdict_kind():
    baseline = _synthetic_baseline()
    assert {e["service"] for e in baseline["gold"]} == set(SERVICES)
    assert {e["legacy_verdict"] for e in baseline["gold"]} == {"PASS", "PASS (warn)", "FAIL", "OBSERVE"}


def test_judge_reproduces_the_hand_derived_legacy_verdicts():
    assert_replay_matches(_synthetic_baseline())


def test_replay_notices_a_verdict_that_differs():
    baseline = _synthetic_baseline()
    baseline["gold"][0]["legacy_verdict"] = "FAIL"      # the legacy script said PASS in the fixture
    with pytest.raises(AssertionError, match="gq_nid_pass"):
        assert_replay_matches(baseline)


def test_replay_requires_the_ereturn_subcategory_flag():
    baseline = _synthetic_baseline()
    for entry in baseline["gold"]:
        if entry["service"] == "ereturn":
            entry["subcategory_available"] = False
    with pytest.raises(AssertionError):
        assert_replay_matches(baseline)


# ------------------------------------------------------------------ the real baseline

real_baseline = pytest.mark.skipif(
    not BASELINE_FILE.exists(),
    reason=(
        "docs/reports/m1-parity/chroma_baseline.json does not exist yet: run "
        "`python tools/m1/export_chroma_baseline.py --out docs/reports/m1-parity` with the global "
        "Python (it needs chromadb and the bge-m3 model) - Task D1, step 4"
    ),
)


def _load_baseline() -> dict:
    return json.loads(BASELINE_FILE.read_text(encoding="utf-8"))


def _gold_by_service() -> dict[str, dict[str, dict]]:
    out = {}
    for service in SERVICES:
        gold = json.loads((REPO_ROOT / f"{service}_gold_queries.json").read_text(encoding="utf-8"))
        out[service] = {gq["id"]: gq for gq in gold["queries"]}
    return out


@real_baseline
def test_every_recorded_legacy_verdict_is_reproduced_by_judge():
    assert_replay_matches(_load_baseline())


@real_baseline
def test_the_baseline_covers_every_gold_query_exactly_once_with_the_current_gold_file_content():
    baseline = _load_baseline()
    gold = _gold_by_service()
    recorded = [(e["service"], e["id"]) for e in baseline["gold"]]
    assert len(recorded) == len(set(recorded))
    assert set(recorded) == {(s, i) for s, queries in gold.items() for i in queries}
    for entry in baseline["gold"]:
        assert entry["gq"] == gold[entry["service"]][entry["id"]], (
            f"{entry['id']}: the gold file changed since the baseline was exported; export it again"
        )


@real_baseline
def test_the_baseline_has_a_retrieve_run_per_gold_query_in_both_scopes_plus_every_case():
    baseline = _load_baseline()
    cases = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    runs = {r["id"]: r for r in baseline["retrieve"]}
    assert len(runs) == len(baseline["retrieve"])
    for entry in baseline["gold"]:
        everywhere = runs[f"{entry['service']}/{entry['id']}/all"]
        scoped = runs[f"{entry['service']}/{entry['id']}/scoped"]
        assert everywhere["queries"] == scoped["queries"] == [entry["gq"]["query"]]
        assert everywhere["services"] is None and scoped["services"] == [entry["service"]]
    for case in cases:
        run = runs[case["id"]]
        assert (run["queries"], run["services"]) == (case["queries"], case["services"])
    assert len(runs) == 2 * len(baseline["gold"]) + len(cases)


@real_baseline
def test_every_recorded_hit_id_is_a_known_document():
    baseline = _load_baseline()
    documents = baseline["documents"]
    assert len(baseline["commit"]) == 40
    for entry in baseline["gold"]:
        for hit in entry["top"]:
            assert hit["id"] in documents, (entry["id"], hit["id"])
    for run in baseline["retrieve"]:
        for hit in run["hits"]:
            assert hit["id"] in documents, (run["id"], hit["id"])
