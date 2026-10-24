"""Tests for the multi-service retrieval selection (port of answer.retrieve(), ``queries=`` path).

No database and no model: ``search`` is a closure over hand-made hits, the encoder maps each query
string to a vector chosen here (the fake search ignores the vector).
"""
import ast
import dataclasses
import random
from pathlib import Path

import pytest

from gsn.retrieval import (
    CANDIDATE_N,
    GROUNDING_FLOOR,
    MAX_CONTEXT,
    PER_SERVICE_K,
    SCOPED_TOP_K,
    SERVICE_MIN_SIM,
    Hit,
    grounded,
    retrieve,
)


def hit(record_id, service_id, sim, **overrides):
    fields = dict(
        record_id=record_id,
        service_id=service_id,
        sim=sim,
        text=f"text of {record_id}",
        category="faq",
        subcategory=None,
        heading=f"heading of {record_id}",
        source_url="https://example.gov.bd/page",
    )
    fields.update(overrides)
    return Hit(**fields)


class FakeEncoder:
    """Maps each query string to a vector of its own; records every encode() call."""

    def __init__(self):
        self.calls = []

    def encode(self, texts):
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]


class RecordingSearch:
    """``search(service_id, vector, k)`` answered from ``results[(service_id, vector[0])]`` (the
    vector's first component is the query's length, see FakeEncoder), falling back to
    ``results[service_id]``. Every call is recorded as ``(service_id, first component, k)``."""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def __call__(self, service_id, vector, k):
        self.calls.append((service_id, vector[0], k))
        found = self.results.get((service_id, vector[0]), self.results.get(service_id, []))
        return list(found)[:k]


def test_constants_are_the_legacy_values():
    assert (CANDIDATE_N, PER_SERVICE_K, SERVICE_MIN_SIM) == (8, 2, 0.40)
    assert (MAX_CONTEXT, SCOPED_TOP_K, GROUNDING_FLOOR) == (6, 8, 0.42)


def test_hit_is_a_frozen_dataclass_with_the_agreed_fields():
    assert [f.name for f in dataclasses.fields(Hit)] == [
        "record_id", "service_id", "sim", "text", "category", "subcategory", "heading", "source_url",
    ]
    h = hit("a", "nid", 0.5)
    with pytest.raises(dataclasses.FrozenInstanceError):
        h.sim = 0.9


def test_single_scoped_service_uses_scoped_limits():
    # 12 candidates requested, 8 kept
    nid = [hit(f"nid-{i:02d}", "nid", 0.90 - i * 0.01) for i in range(12)]
    search = RecordingSearch({"nid": nid, "bdris": [hit("bdris-1", "bdris", 0.95)]})
    out = retrieve(search, FakeEncoder(), ["birth certificate"], ["nid", "bdris"], services=["nid"])
    assert {call[0] for call in search.calls} == {"nid"}           # only the scoped collection is searched
    assert {call[2] for call in search.calls} == {max(CANDIDATE_N, SCOPED_TOP_K + 4)} == {12}
    assert [h.record_id for h in out] == [f"nid-{i:02d}" for i in range(8)]


def test_several_scoped_services_use_the_normal_limits():
    nid = [hit(f"nid-{i}", "nid", 0.90 - i * 0.01) for i in range(5)]
    bdris = [hit(f"bdris-{i}", "bdris", 0.80 - i * 0.01) for i in range(5)]
    search = RecordingSearch({"nid": nid, "bdris": bdris, "epassport": [hit("ep-1", "epassport", 0.99)]})
    out = retrieve(search, FakeEncoder(), ["q"], ["nid", "bdris", "epassport"], services=["nid", "bdris"])
    assert {call[0] for call in search.calls} == {"nid", "bdris"}
    assert {call[2] for call in search.calls} == {CANDIDATE_N}
    assert [h.record_id for h in out] == ["nid-0", "nid-1", "bdris-0", "bdris-1"]   # 2 per service


def test_no_scope_searches_every_live_service_with_normal_limits():
    results = {s: [hit(f"{s}-{i}", s, 0.90 - i * 0.01) for i in range(5)] for s in ("nid", "bdris")}
    search = RecordingSearch(results)
    out = retrieve(search, FakeEncoder(), ["q"], ["nid", "bdris"])
    assert {call[0] for call in search.calls} == {"nid", "bdris"}
    assert {call[2] for call in search.calls} == {CANDIDATE_N}
    assert len(out) == 4                                            # PER_SERVICE_K per service


def test_one_live_service_without_a_scope_keeps_the_normal_limits():
    # Legacy: the scoped limits apply only when the CALLER narrowed the search to one service
    # (only_service / services); a lone collection searched unscoped keeps 2 per service, max 6.
    nid = [hit(f"nid-{i}", "nid", 0.90 - i * 0.01) for i in range(10)]
    search = RecordingSearch({"nid": nid})
    out = retrieve(search, FakeEncoder(), ["q"], ["nid"])
    assert {call[2] for call in search.calls} == {CANDIDATE_N}
    assert [h.record_id for h in out] == ["nid-0", "nid-1"]


def test_service_below_min_sim_contributes_nothing():
    # best 0.399 -> excluded; 0.40 -> included
    search = RecordingSearch({
        "nid": [hit("nid-1", "nid", 0.399), hit("nid-2", "nid", 0.30)],
        "bdris": [hit("bdris-1", "bdris", 0.40), hit("bdris-2", "bdris", 0.10)],
    })
    out = retrieve(search, FakeEncoder(), ["q"], ["nid", "bdris"])
    # the floor applies to a service's BEST hit only: the qualifying service keeps its weaker hit too
    assert [h.record_id for h in out] == ["bdris-1", "bdris-2"]


def test_service_with_no_hits_contributes_nothing_and_nothing_crashes():
    search = RecordingSearch({"nid": []})
    assert retrieve(search, FakeEncoder(), ["q"], ["nid", "bdris"]) == []


def test_best_sim_across_queries_wins_and_duplicates_merge():
    # Two queries (lengths 1 and 2 -> first vector components 1.0 and 2.0) return the same record
    # with different similarities: one hit comes out, carrying the better similarity.
    search = RecordingSearch({
        ("nid", 1.0): [hit("nid-a", "nid", 0.50), hit("nid-b", "nid", 0.45)],
        ("nid", 2.0): [hit("nid-a", "nid", 0.70), hit("nid-c", "nid", 0.48)],
    })
    out = retrieve(search, FakeEncoder(), ["a", "bb"], ["nid"])
    assert [(h.record_id, h.sim) for h in out] == [("nid-a", 0.70), ("nid-c", 0.48)]
    # and the better-scoring copy is the one kept, whichever query produced it first
    search = RecordingSearch({
        ("nid", 1.0): [hit("nid-a", "nid", 0.70)],
        ("nid", 2.0): [hit("nid-a", "nid", 0.50)],
    })
    out = retrieve(search, FakeEncoder(), ["a", "bb"], ["nid"])
    assert [(h.record_id, h.sim) for h in out] == [("nid-a", 0.70)]


def test_a_worse_duplicate_does_not_replace_the_kept_copy():
    first = hit("nid-a", "nid", 0.70, text="from the stronger query")
    second = hit("nid-a", "nid", 0.50, text="from the weaker query")
    search = RecordingSearch({("nid", 1.0): [first], ("nid", 2.0): [second]})
    out = retrieve(search, FakeEncoder(), ["a", "bb"], ["nid"])
    assert out == [first]


def test_max_context_cut_and_tie_break_by_record_id():
    # Four services, two qualifying hits each (8 candidates) -> cut to MAX_CONTEXT = 6.
    # Equal similarities are ordered by record id, whatever order the services were searched in.
    results = {
        "s4": [hit("z-1", "s4", 0.60), hit("z-2", "s4", 0.60)],
        "s3": [hit("y-1", "s3", 0.60), hit("y-2", "s3", 0.50)],
        "s2": [hit("x-1", "s2", 0.60), hit("x-2", "s2", 0.50)],
        "s1": [hit("w-1", "s1", 0.55), hit("w-2", "s1", 0.45)],
    }
    out = retrieve(RecordingSearch(results), FakeEncoder(), ["q"], ["s4", "s3", "s2", "s1"])
    assert [h.record_id for h in out] == ["x-1", "y-1", "z-1", "z-2", "w-1", "x-2"]
    assert len(out) == MAX_CONTEXT


def test_within_a_service_ties_are_broken_by_record_id_before_the_per_service_cut():
    results = {"nid": [hit("nid-c", "nid", 0.5), hit("nid-a", "nid", 0.5), hit("nid-b", "nid", 0.5)]}
    out = retrieve(RecordingSearch(results), FakeEncoder(), ["q"], ["nid", "bdris"])
    assert [h.record_id for h in out] == ["nid-a", "nid-b"]


def test_unknown_or_hidden_services_are_ignored():
    # "visa" is not live (it may exist in the database but is hidden); "nope" does not exist at all.
    results = {"nid": [hit("nid-1", "nid", 0.7)], "bdris": [hit("bdris-1", "bdris", 0.6)],
               "visa": [hit("visa-1", "visa", 0.99)]}
    search = RecordingSearch(results)
    out = retrieve(search, FakeEncoder(), ["q"], ["nid", "bdris"], services=["visa", "nope", "nid"])
    assert {call[0] for call in search.calls} == {"nid"}            # one live service left -> scoped
    assert {call[2] for call in search.calls} == {12}
    assert [h.record_id for h in out] == ["nid-1"]


def test_only_unknown_services_requested_falls_back_to_every_live_service():
    results = {"nid": [hit("nid-1", "nid", 0.7)], "bdris": [hit("bdris-1", "bdris", 0.6)]}
    search = RecordingSearch(results)
    out = retrieve(search, FakeEncoder(), ["q"], ["nid", "bdris"], services=["visa", "nope"])
    assert {call[0] for call in search.calls} == {"nid", "bdris"}
    assert {call[2] for call in search.calls} == {CANDIDATE_N}
    assert [h.record_id for h in out] == ["nid-1", "bdris-1"]


def test_empty_or_none_services_means_every_live_service():
    results = {"nid": [hit("nid-1", "nid", 0.7)], "bdris": [hit("bdris-1", "bdris", 0.6)]}
    for services in (None, []):
        search = RecordingSearch(results)
        out = retrieve(search, FakeEncoder(), ["q"], ["nid", "bdris"], services=services)
        assert {call[0] for call in search.calls} == {"nid", "bdris"}
        assert len(out) == 2


def test_requested_services_are_deduplicated():
    nid = [hit(f"nid-{i}", "nid", 0.9 - i * 0.01) for i in range(10)]
    search = RecordingSearch({"nid": nid})
    out = retrieve(search, FakeEncoder(), ["q"], ["nid", "bdris"], services=["nid", "nid"])
    assert {call[2] for call in search.calls} == {12}               # still exactly one service
    assert len(out) == SCOPED_TOP_K


def test_no_live_services_returns_nothing():
    assert retrieve(RecordingSearch({}), FakeEncoder(), ["q"], []) == []


def test_blank_and_duplicate_queries_are_dropped():
    encoder = FakeEncoder()
    search = RecordingSearch({"nid": [hit("nid-1", "nid", 0.7)]})
    retrieve(search, encoder, ["  alpha ", "", "   ", "alpha", "beta", "alpha  ", None], ["nid"])
    assert encoder.calls == [["alpha"], ["beta"]]                    # stripped, de-duplicated, in order
    assert len(search.calls) == 2


def test_no_usable_query_means_no_search_and_no_hits():
    encoder = FakeEncoder()
    search = RecordingSearch({"nid": [hit("nid-1", "nid", 0.7)]})
    assert retrieve(search, encoder, ["", "  "], ["nid"]) == []
    assert encoder.calls == [] and search.calls == []


def test_each_query_is_embedded_once_and_searched_in_every_scoped_service():
    encoder = FakeEncoder()
    search = RecordingSearch({})
    retrieve(search, encoder, ["a", "bb"], ["nid", "bdris"])
    assert encoder.calls == [["a"], ["bb"]]
    assert sorted(search.calls) == [("bdris", 1.0, 8), ("bdris", 2.0, 8), ("nid", 1.0, 8), ("nid", 2.0, 8)]


def test_grounded_floor_is_inclusive():
    assert grounded([hit("a", "nid", 0.42)]) is True
    assert grounded([hit("a", "nid", 0.4199)]) is False
    assert grounded([hit("a", "nid", 0.9)]) is True


def test_grounded_is_false_without_hits():
    assert grounded([]) is False


# ---------------------------------------------------------------- differential vs. answer.retrieve()

# ``retrieve`` must select exactly the records the legacy function selects. The legacy function is
# lifted out of answer.py without importing it (answer.py imports chromadb, sentence_transformers
# and the LLM backends, which the host test environment lacks) and run against fake collections.
# Similarities are all distinct within a case: on exact ties the legacy order is the insertion order,
# the port's is the record id (a deliberate, deterministic difference).

REPO = Path(__file__).resolve().parents[2]
_LEGACY_NAMES = {"CANDIDATE_N", "PER_SERVICE_K", "SERVICE_MIN_SIM", "MAX_CONTEXT", "SCOPED_TOP_K",
                 "MODEL_NAME", "IS_E5"}
ALL_SERVICES = ["nid", "bdris", "epassport", "ereturn"]


class _NormalizedQuery:
    def __init__(self, raw, translated, kind, queries):
        self.queries = queries


def _legacy_retrieve():
    tree = ast.parse((REPO / "answer.py").read_text(encoding="utf-8"), filename="answer.py")
    wanted = [
        node for node in tree.body
        if (isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id in _LEGACY_NAMES for t in node.targets))
        or (isinstance(node, ast.FunctionDef) and node.name in {"as_query", "retrieve"})
    ]
    assert len(wanted) == len(_LEGACY_NAMES) + 2, "answer.py no longer defines what the test lifts"
    namespace = {"NormalizedQuery": _NormalizedQuery,
                 "normalize": lambda *a, **k: pytest.fail("the queries= path must not call normalize()")}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), "answer.py", "exec"), namespace)
    return namespace["retrieve"]


class _LegacyVector(list):
    def tolist(self):
        return list(self)


class _LegacyModel:
    def __init__(self, index_of):
        self.index_of = index_of

    def encode(self, texts, normalize_embeddings=True):
        return [_LegacyVector([float(self.index_of[t])]) for t in texts]


class _LegacyCollection:
    def __init__(self, service, table):
        self.service, self.table = service, table

    def query(self, query_embeddings, n_results, include):
        rows = self.table.get((self.service, int(query_embeddings[0][0])), [])[:n_results]
        return {"ids": [[h.record_id for h, _d in rows]],
                "documents": [[h.text for h, _d in rows]],
                "metadatas": [[{"category": h.category, "heading": h.heading, "source_url": h.source_url}
                               for h, _d in rows]],
                "distances": [[d for _h, d in rows]]}


class _IndexEncoder:
    def __init__(self, index_of):
        self.index_of = index_of

    def encode(self, texts):
        return [[float(self.index_of[t])] for t in texts]


def _random_retrieval_case(rng):
    live = [s for s in ALL_SERVICES if rng.random() < 0.7] or ["nid"]
    services = rng.choice([None, [], rng.sample(ALL_SERVICES + ["visa"], rng.randint(1, 3)),
                           [rng.choice(live)], [rng.choice(live), "visa"]])
    queries = [rng.choice(["alpha", "beta", "gamma", " alpha ", "", "  "]) for _ in range(rng.randint(0, 4))]
    index_of = {"alpha": 1, "beta": 2, "gamma": 3}
    distances = iter(rng.sample(range(100, 950), 4 * 3 * 12))
    table = {}
    for service in ALL_SERVICES:
        for query_index in index_of.values():
            n = rng.choice([0, 3, 8, 12, 12])
            ds = sorted(next(distances) / 1000 for _ in range(n))
            ids = rng.sample(range(14), n)
            table[(service, query_index)] = [
                (Hit(record_id=f"{service}-{i}", service_id=service, sim=1 - d, text=f"t{service}{i}",
                     category="faq", subcategory=None, heading=f"h{i}", source_url=f"https://x.test/{i}"), d)
                for i, d in zip(ids, ds)]
    return live, services, queries, index_of, table


def test_retrieve_matches_the_legacy_retrieve_on_random_cases():
    legacy_retrieve = _legacy_retrieve()
    rng = random.Random("gsn-retrieve")
    non_trivial = scoped = 0
    for _ in range(600):
        live, services, queries, index_of, table = _random_retrieval_case(rng)
        colls = {s: _LegacyCollection(s, table) for s in live}
        _norm, legacy = legacy_retrieve("question", None, _LegacyModel(index_of), colls,
                                        queries=queries, services=services)

        def search(service_id, vector, k, table=table):
            return [h for h, _d in table.get((service_id, int(vector[0])), [])[:k]]

        ours = retrieve(search, _IndexEncoder(index_of), queries, live, services)
        assert [(h.record_id, h.service_id, h.sim) for h in ours] == \
               [(h["id"], h["service"], h["sim"]) for h in legacy], (live, services, queries)
        assert [(h.text, h.category, h.heading, h.source_url) for h in ours] == \
               [(h["text"], h["category"], h["heading"], h["source_url"]) for h in legacy]
        non_trivial += bool(ours)
        scoped += len(ours) > MAX_CONTEXT
    assert non_trivial > 300                     # most cases select something
    assert scoped > 20                           # and the scoped (8-record) path is well exercised
