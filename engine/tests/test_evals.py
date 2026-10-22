"""Tests for the gold-query verdict rules (port of the legacy ``eval_*.py::eval_one`` functions).

Every branch is exercised once per rule that has it. The expected verdicts come from reading the
legacy scripts, not from running the new code. No database and no model: hits are hand-made.
"""
import ast
import dataclasses
import functools
import random
from pathlib import Path

import pytest

from gsn.evals import (
    RULES,
    GateResult,
    Rule,
    Verdict,
    gate,
    judge,
    run_gold,
)
from gsn.retrieval import Hit

NID = RULES["nid"]
EPASSPORT = RULES["epassport"]
ERETURN = RULES["ereturn"]
BDRIS = RULES["bdris"]


def hit(record_id, sim, category=None, subcategory=None, service_id="svc"):
    return Hit(
        record_id=record_id,
        service_id=service_id,
        sim=sim,
        text=f"text of {record_id}",
        category=category,
        subcategory=subcategory,
        heading=None,
        source_url="https://example.gov.bd/page",
    )


def gq(**fields):
    base = {"id": "gq_1", "query": "a question", "lang": "en"}
    base.update(fields)
    return base


def verdicts_for(rule, query, hits, top_k=4):
    return judge(rule, query, hits, top_k).verdict


# ---------------------------------------------------------------- the rules table ----------------

def test_rules_table():
    assert RULES == {
        "nid": Rule("expected_category", "category", 0.55, True),
        "epassport": Rule("expected_category", "category", 0.55, True),
        "ereturn": Rule("expected_subcategory", "subcategory", 0.55, True),
        "bdris": Rule("expected_category", "category", 0.62, False),
    }


def test_rule_and_verdict_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        NID.ceiling = 1.0
    v = judge(NID, gq(expected_category=None, expected_id=None, min_similarity=None), [], 4)
    with pytest.raises(dataclasses.FrozenInstanceError):
        v.verdict = "FAIL"


def test_verdict_carries_query_id_service_and_top_hits():
    hits = [hit("a", 0.8123456, "faq"), hit("b", 0.5, "fees")]
    v = judge(NID, gq(id="gq_nid_007", expected_category="faq"), hits, 4, service_id="nid")
    assert (v.query_id, v.service_id) == ("gq_nid_007", "nid")
    assert v.top == [("a", 0.8123456), ("b", 0.5)]                  # full precision, best first
    assert isinstance(v.reasons, list) and v.reasons


# ---------------------------------------------------------------- out-of-scope probe -------------

def test_out_of_scope_probe_ceiling_055_vs_bdris_062():
    # no gate field, no expected_id, no min_similarity; best similarity 0.60
    probe = gq(expected_category=None, expected_id=None, min_similarity=None)
    hits = [hit("a", 0.60, "faq"), hit("b", 0.30, "faq")]
    assert verdicts_for(NID, probe, hits) == "FAIL"                 # 0.60 >= 0.55
    assert verdicts_for(EPASSPORT, probe, hits) == "FAIL"
    assert verdicts_for(BDRIS, probe, hits) == "PASS"               # 0.60 < 0.62
    ereturn_probe = gq(expected_subcategory=None, expected_id=None, min_similarity=None)
    assert verdicts_for(ERETURN, ereturn_probe, [hit("a", 0.60, subcategory="online_return")]) == "FAIL"


def test_out_of_scope_ceiling_is_exclusive_on_the_pass_side():
    probe = gq(expected_category=None, expected_id=None, min_similarity=None)
    assert verdicts_for(NID, probe, [hit("a", 0.5499, "faq")]) == "PASS"
    assert verdicts_for(NID, probe, [hit("a", 0.55, "faq")]) == "FAIL"
    assert verdicts_for(BDRIS, probe, [hit("a", 0.6199, "faq")]) == "PASS"
    assert verdicts_for(BDRIS, probe, [hit("a", 0.62, "faq")]) == "FAIL"


def test_out_of_scope_probe_with_missing_keys_counts_as_unset():
    # the gold files omit nothing, but .get() semantics are what the legacy scripts use for these
    assert verdicts_for(NID, gq(expected_category=None), [hit("a", 0.2)]) == "PASS"


def test_out_of_scope_probe_uses_the_first_hit_not_the_maximum():
    # legacy: sims[0], relying on the database's best-first order
    probe = gq(expected_category=None, expected_id=None, min_similarity=None)
    assert verdicts_for(NID, probe, [hit("a", 0.30), hit("b", 0.90)]) == "PASS"


def test_empty_hit_list_does_not_crash():
    probe = gq(expected_category=None, expected_id=None, min_similarity=None)
    for rule in (NID, EPASSPORT, BDRIS):
        v = judge(rule, probe, [], 4)
        assert v.verdict == "PASS" and v.top == []                  # best_sim = 0.0
    ereturn_probe = gq(expected_subcategory=None, expected_id=None, min_similarity=None)
    assert verdicts_for(ERETURN, ereturn_probe, []) == "PASS"
    # a gated query with nothing retrieved is a plain FAIL
    assert verdicts_for(NID, gq(expected_category="faq"), []) == "FAIL"
    assert verdicts_for(BDRIS, gq(expected_category="faq"), []) == "FAIL"
    assert verdicts_for(ERETURN, gq(expected_subcategory="online_return"), []) == "FAIL"
    assert verdicts_for(NID, gq(expected_category=None, expected_id=None, min_similarity=0.5), []) == "OBSERVE"


# ---------------------------------------------------------------- observational probe ------------

def test_observe_branch_exists_except_bdris():
    # min_similarity set, no category and no id: nid/epassport/ereturn OBSERVE; bdris -> out-of-scope probe
    observe = gq(expected_category=None, expected_id=None, min_similarity=0.5)
    low = [hit("a", 0.30, "faq")]
    high = [hit("a", 0.90, "faq")]
    for rule in (NID, EPASSPORT):
        assert verdicts_for(rule, observe, low) == "OBSERVE"
        assert verdicts_for(rule, observe, high) == "OBSERVE"       # never fails, whatever the similarity
    ereturn_observe = gq(expected_subcategory=None, expected_id=None, min_similarity=0.5)
    assert verdicts_for(ERETURN, ereturn_observe, high) == "OBSERVE"
    assert verdicts_for(BDRIS, observe, low) == "PASS"              # 0.30 < 0.62
    assert verdicts_for(BDRIS, observe, high) == "FAIL"             # 0.90 >= 0.62


def test_bdris_treats_any_null_category_as_out_of_scope_even_with_an_id():
    q = gq(expected_category=None, expected_id="bdris-x", min_similarity=0.5)
    assert verdicts_for(BDRIS, q, [hit("bdris-x", 0.30, "faq")]) == "PASS"
    assert verdicts_for(BDRIS, q, [hit("bdris-x", 0.70, "faq")]) == "FAIL"


def test_observe_reason_names_the_top_hit():
    observe = gq(expected_category=None, expected_id=None, min_similarity=0.5)
    v = judge(NID, observe, [hit("rec-1", 0.4321, "faq")], 4)
    assert v.verdict == "OBSERVE" and "rec-1" in " ".join(v.reasons)


# ---------------------------------------------------------------- gate-field check ---------------

def test_gate_field_must_appear_in_the_top_k_metadata():
    hits = [hit("a", 0.9, "fees"), hit("b", 0.8, "faq")]
    assert verdicts_for(NID, gq(expected_category="faq"), hits) == "PASS"
    assert verdicts_for(NID, gq(expected_category="contacts"), hits) == "FAIL"
    assert verdicts_for(EPASSPORT, gq(expected_category="faq"), hits) == "PASS"
    assert verdicts_for(EPASSPORT, gq(expected_category="contacts"), hits) == "FAIL"
    assert verdicts_for(BDRIS, gq(expected_category="faq"), hits) == "PASS"
    assert verdicts_for(BDRIS, gq(expected_category="contacts"), hits) == "FAIL"


def test_only_the_top_k_hits_are_judged():
    hits = [hit("a", 0.9, "fees"), hit("b", 0.8, "fees"), hit("c", 0.7, "faq")]
    assert verdicts_for(NID, gq(expected_category="faq"), hits, top_k=3) == "PASS"
    assert verdicts_for(NID, gq(expected_category="faq"), hits, top_k=2) == "FAIL"
    # and the top list in the verdict is cut the same way
    assert [r for r, _ in judge(NID, gq(expected_category="fees"), hits, 2).top] == ["a", "b"]


def test_ereturn_gates_on_subcategory():
    hits = [hit("a", 0.9, "faq", "income_tax_general"), hit("b", 0.8, "faq", "online_return")]
    # category is "faq" on every e-Return record: the category axis would carry no signal
    assert verdicts_for(ERETURN, gq(expected_subcategory="online_return"), hits) == "PASS"
    assert verdicts_for(ERETURN, gq(expected_subcategory="income_tax_general"), hits) == "PASS"
    assert verdicts_for(ERETURN, gq(expected_subcategory="something_else"), hits) == "FAIL"
    # an expected_category key on an e-Return query is ignored: it is not the gate field
    assert verdicts_for(ERETURN, gq(expected_subcategory="something_else", expected_category="faq"), hits) == "FAIL"


def test_gate_field_check_comes_before_the_record_check():
    # wrong category AND wrong record: the verdict reason is the category one
    hits = [hit("a", 0.9, "fees")]
    v = judge(NID, gq(expected_category="faq", expected_id="zzz"), hits, 4)
    assert v.verdict == "FAIL"
    assert "category" in " ".join(v.reasons) and "zzz" not in " ".join(v.reasons)


def test_unset_gate_with_an_expected_id_fails_for_the_category_rules_but_not_for_ereturn():
    # Legacy quirk: eval_nid/eval_epassport run the category check unconditionally, so
    # expected_category=null with an expected_id looks for None among the categories and FAILS;
    # eval_ereturn only runs its gate when expected_subcategory is set, then checks the record.
    hits = [hit("rec-1", 0.9, "faq", "online_return")]
    assert verdicts_for(NID, gq(expected_category=None, expected_id="rec-1"), hits) == "FAIL"
    assert verdicts_for(EPASSPORT, gq(expected_category=None, expected_id="rec-1"), hits) == "FAIL"
    assert verdicts_for(ERETURN, gq(expected_subcategory=None, expected_id="rec-1"), hits) == "PASS"
    assert verdicts_for(ERETURN, gq(expected_subcategory=None, expected_id="nope"), hits) == "FAIL"
    # ... and the quirk's other face: a hit whose category metadata is missing "satisfies" a null gate
    assert verdicts_for(NID, gq(expected_category=None, expected_id="rec-1"), [hit("rec-1", 0.9, None)]) == "PASS"


# ---------------------------------------------------------------- record check -------------------

def test_expected_id_must_be_in_top_k():
    hits = [hit("a", 0.9, "faq"), hit("b", 0.8, "faq"), hit("c", 0.7, "faq")]
    assert verdicts_for(NID, gq(expected_category="faq", expected_id="c"), hits, top_k=3) == "PASS"
    assert verdicts_for(NID, gq(expected_category="faq", expected_id="c"), hits, top_k=2) == "FAIL"
    assert verdicts_for(NID, gq(expected_category="faq", expected_id="zzz"), hits) == "FAIL"
    assert verdicts_for(BDRIS, gq(expected_category="faq", expected_id="zzz"), hits) == "FAIL"
    assert verdicts_for(
        ERETURN, gq(expected_subcategory="online_return", expected_id="zzz"),
        [hit("a", 0.9, "faq", "online_return")]) == "FAIL"


def test_empty_expected_id_means_category_level_match_only():
    hits = [hit("a", 0.9, "faq")]
    assert verdicts_for(NID, gq(expected_category="faq", expected_id=""), hits) == "PASS"
    assert verdicts_for(NID, gq(expected_category="faq", expected_id=None), hits) == "PASS"


# ---------------------------------------------------------------- soft min_similarity -------------

def test_min_similarity_is_a_soft_warn():
    # best relevant similarity below the floor -> "PASS (warn)", never FAIL
    hits = [hit("a", 0.45, "faq")]
    q = gq(expected_category="faq", expected_id="a", min_similarity=0.5)
    for rule in (NID, EPASSPORT, BDRIS):
        assert verdicts_for(rule, q, hits) == "PASS (warn)"
    assert verdicts_for(ERETURN, gq(expected_subcategory="s", expected_id="a", min_similarity=0.5),
                        [hit("a", 0.45, "faq", "s")]) == "PASS (warn)"
    # at the floor exactly, and above it: plain PASS
    assert verdicts_for(NID, gq(expected_category="faq", expected_id="a", min_similarity=0.45), hits) == "PASS"
    assert verdicts_for(NID, gq(expected_category="faq", expected_id="a", min_similarity=0.40), hits) == "PASS"


def test_min_similarity_uses_the_expected_record_when_an_id_is_set():
    hits = [hit("other", 0.90, "faq"), hit("target", 0.45, "faq")]
    q = gq(expected_category="faq", expected_id="target", min_similarity=0.5)
    assert verdicts_for(NID, q, hits) == "PASS (warn)"              # the 0.90 of another record is irrelevant


def test_min_similarity_uses_the_best_of_all_hits_when_no_id_is_set():
    hits = [hit("a", 0.30, "faq"), hit("b", 0.70, "fees")]
    q = gq(expected_category="faq", expected_id=None, min_similarity=0.5)
    assert verdicts_for(NID, q, hits) == "PASS"                     # 0.70 among all hits clears 0.5
    q = gq(expected_category="faq", expected_id=None, min_similarity=0.8)
    assert verdicts_for(NID, q, hits) == "PASS (warn)"


def test_min_similarity_unset_never_warns():
    assert verdicts_for(NID, gq(expected_category="faq", min_similarity=None), [hit("a", 0.01, "faq")]) == "PASS"


def test_min_similarity_with_no_hits_cannot_crash():
    # Reachable only for e-Return (null subcategory, empty-string id, nothing retrieved); the legacy
    # script would raise IndexError on sims[0] there. Here the best similarity is taken as 0.0.
    q = gq(expected_subcategory=None, expected_id="", min_similarity=0.5)
    assert verdicts_for(ERETURN, q, []) == "PASS (warn)"


# ---------------------------------------------------------------- run_gold -----------------------

class FakeEncoder:
    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 0.5] for t in texts]


def test_run_gold_embeds_each_query_once_and_searches_with_top_k():
    encoder = FakeEncoder()
    calls = []

    def search(service_id, vector, k):
        calls.append((service_id, vector, k))
        return [hit("rec-1", 0.9, "faq", service_id=service_id)]

    gold = {"top_k": 3, "queries": [
        gq(id="q1", query="aa", expected_category="faq", expected_id="rec-1"),
        gq(id="q2", query="bbbb", expected_category="contacts"),
    ]}
    out = run_gold(search, encoder, "nid", gold)
    assert [(v.query_id, v.service_id, v.verdict) for v in out] == [("q1", "nid", "PASS"), ("q2", "nid", "FAIL")]
    assert encoder.calls == [["aa"], ["bbbb"]]
    assert calls == [("nid", [2.0, 0.5], 3), ("nid", [4.0, 0.5], 3)]


def test_run_gold_top_k_defaults_to_four():
    seen = []

    def search(service_id, vector, k):
        seen.append(k)
        return []

    run_gold(search, FakeEncoder(), "bdris", {"queries": [gq(expected_category=None)]})
    assert seen == [4]


def test_run_gold_uses_the_services_own_rule():
    probe = gq(expected_category=None, expected_id=None, min_similarity=None)

    def search(service_id, vector, k):
        return [hit("a", 0.60, "faq", service_id=service_id)]

    assert run_gold(search, FakeEncoder(), "nid", {"queries": [probe]})[0].verdict == "FAIL"
    assert run_gold(search, FakeEncoder(), "bdris", {"queries": [probe]})[0].verdict == "PASS"


def test_run_gold_for_a_service_without_a_rule_is_a_clear_error():
    with pytest.raises(ValueError, match="visa"):
        run_gold(lambda s, v, k: [], FakeEncoder(), "visa", {"queries": []})


# ---------------------------------------------------------------- gate ---------------------------

def verdict(query_id, service_id, kind):
    return Verdict(query_id=query_id, service_id=service_id, verdict=kind, reasons=["r"], top=[])


def test_gate_passes_when_nothing_fails():
    result = gate([verdict("a", "nid", "PASS"), verdict("b", "nid", "PASS (warn)"),
                   verdict("c", "nid", "OBSERVE")], {})
    assert result == GateResult(True, [], [], result.counts)


def test_gate_blocks_on_an_unlisted_failure():
    result = gate([verdict("a", "nid", "PASS"), verdict("b", "nid", "FAIL")], {})
    assert result.passed is False
    assert result.unexpected_failures == ["b"] and result.known_failures_seen == []


def test_gate_ignores_known_failures_and_warns_observes():
    verdicts = [
        verdict("known", "nid", "FAIL"),
        verdict("warned", "nid", "PASS (warn)"),
        verdict("observed", "epassport", "OBSERVE"),
        verdict("fine", "bdris", "PASS"),
    ]
    result = gate(verdicts, {"known": "Banglish query embedded raw", "never_run": "stale entry"})
    assert result.passed is True
    assert result.known_failures_seen == ["known"]                  # only those that actually failed
    assert result.unexpected_failures == []
    # a known failure that now passes is simply not reported; a known entry never turns a PASS into trouble
    assert gate([verdict("known", "nid", "PASS")], {"known": "x"}).known_failures_seen == []


def test_gate_known_and_unexpected_failures_are_separated_in_order():
    verdicts = [verdict("u1", "nid", "FAIL"), verdict("k1", "nid", "FAIL"),
                verdict("u2", "bdris", "FAIL"), verdict("k2", "bdris", "FAIL")]
    result = gate(verdicts, {"k1": "r1", "k2": "r2"})
    assert result.passed is False
    assert result.unexpected_failures == ["u1", "u2"]
    assert result.known_failures_seen == ["k1", "k2"]


def test_gate_counts_per_service():
    verdicts = [
        verdict("n1", "nid", "PASS"), verdict("n2", "nid", "PASS"), verdict("n3", "nid", "FAIL"),
        verdict("n4", "nid", "OBSERVE"),
        verdict("b1", "bdris", "PASS (warn)"), verdict("b2", "bdris", "PASS"),
    ]
    result = gate(verdicts, {"n3": "known"})
    assert result.counts["nid"] == {"PASS": 2, "PASS (warn)": 0, "OBSERVE": 1, "FAIL": 1}
    assert result.counts["bdris"] == {"PASS": 1, "PASS (warn)": 1, "OBSERVE": 0, "FAIL": 0}
    assert set(result.counts) == {"nid", "bdris"}                   # services with no verdicts are absent


def test_gate_accepts_any_iterable_and_empty_input():
    assert gate(iter([]), {}) == GateResult(True, [], [], {})
    assert gate((v for v in [verdict("a", "nid", "PASS")]), {}).counts == {
        "nid": {"PASS": 1, "PASS (warn)": 0, "OBSERVE": 0, "FAIL": 0}}


def test_gate_does_not_mutate_its_inputs():
    known = {"k": "reason"}
    verdicts = [verdict("k", "nid", "FAIL")]
    gate(verdicts, known)
    assert known == {"k": "reason"} and len(verdicts) == 1


# ---------------------------------------------------------------- differential vs. the legacy scripts

# ``judge`` must reach the same verdict as each legacy ``eval_one`` for every combination of gold
# query and hits. The legacy functions are lifted out of the root scripts without importing them
# (those scripts import chromadb and sentence_transformers, which the host test environment lacks)
# and run against a fake collection. Cases on which a legacy script itself raises (it cannot handle
# a null category next to a string one when sorting, and indexes sims[0] of an empty list in one
# unreachable corner) are skipped.

REPO = Path(__file__).resolve().parents[2]
LEGACY = {
    "nid": ("eval_nid.py", "category"),
    "epassport": ("eval_epassport.py", "category"),
    "ereturn": ("eval_ereturn.py", "subcategory"),
    "bdris": ("eval_bdris.py", "category"),
}
_LIFTED_CONSTANTS = {"OUT_OF_SCOPE_CEILING", "MODEL_NAME", "IS_E5"}


@functools.cache
def _legacy_eval_one(filename):
    tree = ast.parse((REPO / filename).read_text(encoding="utf-8"), filename=filename)
    wanted = [
        node for node in tree.body
        if (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in _LIFTED_CONSTANTS for t in node.targets))
        or (isinstance(node, ast.FunctionDef) and node.name in {"as_query", "eval_one"})
    ]
    namespace: dict = {}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), filename, "exec"), namespace)
    return namespace["eval_one"]


class _Vector(list):
    def tolist(self):
        return list(self)


class _LegacyModel:
    def encode(self, texts, normalize_embeddings=True):
        return [_Vector([0.0]) for _ in texts]


class _LegacyCollection:
    """Answers any query with the same hits, in the shape of a Chroma query result."""

    def __init__(self, hits, distances):
        self.hits = hits
        self.distances = distances

    def query(self, **_kwargs):
        return {
            "ids": [[h.record_id for h in self.hits]],
            "documents": [[h.text for h in self.hits]],
            "metadatas": [[{"category": h.category, "subcategory": h.subcategory} for h in self.hits]],
            "distances": [list(self.distances)],
        }


def _legacy_verdict(service_id, filename, gq, hits, distances, top_k):
    eval_one = _legacy_eval_one(filename)
    collection = _LegacyCollection(hits, distances)
    if service_id == "bdris":
        return eval_one(collection, gq, top_k)
    if service_id == "ereturn":
        return eval_one(_LegacyModel(), collection, gq, top_k, True)
    return eval_one(_LegacyModel(), collection, gq, top_k)


def _random_case(rng, rule, meta_field):
    categories = ["c1", "c2", "c3"]
    # Chroma hands back distances and the legacy scripts compute sim = 1 - distance, so the hits
    # carry exactly that float. The pool puts similarities on both sides of both ceilings
    # (0.55, 0.62) and of the floors used below, boundaries included.
    distance_pool = [0.90, 0.70, 0.55, 0.50, 0.4501, 0.45, 0.40, 0.3801, 0.38, 0.30, 0.10]
    n_hits = rng.choice([0, 1, 2, 3, 4, 4, 4])
    distances = sorted(rng.choice(distance_pool) for _ in range(n_hits))
    hits = [
        Hit(record_id=f"r{i}", service_id="svc", sim=1 - distance, text="t",
            category=rng.choice(categories) if meta_field == "category" else "faq",
            subcategory=rng.choice(categories) if meta_field == "subcategory" else None,
            heading=None, source_url="https://x.test")
        for i, distance in enumerate(distances)
    ]
    gold_query = {
        "id": "gq", "query": "q", "lang": "en",
        rule.gate_field: rng.choice([None, None, "c1", "c2", "c9"]),
        "expected_id": rng.choice([None, None, "", "r0", "r1", "r3", "zzz"]),
        "min_similarity": rng.choice([None, None, 0.30, 0.50, 0.55, 0.60, 0.95]),
    }
    return gold_query, hits, distances


@pytest.mark.parametrize("service_id", list(LEGACY))
def test_judge_matches_the_legacy_eval_one_on_random_cases(service_id):
    filename, meta_field = LEGACY[service_id]
    rule = RULES[service_id]
    rng = random.Random(f"gsn-evals-{service_id}")
    compared = 0
    kinds = set()
    for _ in range(1500):
        query, hits, distances = _random_case(rng, rule, meta_field)
        try:
            legacy = _legacy_verdict(service_id, filename, query, hits, distances, 4)
        except (IndexError, TypeError):
            continue
        ours = judge(rule, query, hits, 4)
        assert ours.verdict == legacy["verdict"], (query, [(h.record_id, h.category, h.subcategory, h.sim) for h in hits])
        assert [r for r, _ in ours.top] == [i for i, _c, _s in legacy["top_hits"]]
        compared += 1
        kinds.add(ours.verdict)
    assert compared > 1000                       # the skips really are corners, not the bulk
    expected_kinds = {"PASS", "PASS (warn)", "FAIL"} | ({"OBSERVE"} if rule.observe else set())
    assert kinds == expected_kinds               # every verdict kind this rule can give was exercised
