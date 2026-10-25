"""``gsn.parity``: the comparison of a release with the recorded ChromaDB baseline.

Synthetic data only. The baseline, the release's rows and the search are tiny made-up worlds; the store
functions are replaced by fakes (monkeypatch) and the embedder is a stub that gives each query text its
own marker vector. The real baseline and the real PostgreSQL search meet only in the stack: the
integration test of ``store.member_text_hashes`` and the real parity run of Task D4.

The made-up world has five records in two services and three gold queries; ``World.baseline`` holds what
ChromaDB "returned" and ``World.results`` what the release "returns". They start out identical, and each
test breaks exactly one thing.
"""
import copy
import gzip
import json

import pytest

from gsn import parity, store
from gsn.embed import BGE_M3_REVISION, DIMENSIONS
from gsn.gitsource import DictSource
from gsn.hashing import text_hash
from gsn.parity import (
    BASELINE_PATH, TOLERANCE, VECTOR_FLOOR, VECTORS_PATH, ParityError, compare_ranked, compare_vectors,
)
from gsn.retrieval import GROUNDING_FLOOR, SERVICE_MIN_SIM, Hit

COMMIT = "0123456789abcdef0123456789abcdef01234567"
FAKE_MODEL = "fake-sha256-v1"

TEXTS = {"n1": "nid fees", "n2": "nid faq", "n3": "nid eligibility", "b1": "bdris faq", "b2": "bdris correction"}
SERVICE_OF = {"n1": "nid", "n2": "nid", "n3": "nid", "b1": "bdris", "b2": "bdris"}
CATEGORY_OF = {"n1": "fees", "n2": "faq", "n3": "eligibility", "b1": "bdris_faq", "b2": "bdris_correction"}

Q_NID = "how much does an NID card cost"
Q_BDRIS = "how do I correct a birth certificate"
Q_OOS = "what is the weather in Dhaka"


# --- compare_ranked -----------------------------------------------------------------------------


def test_identical_results_pass():
    ranked = [("rec-a", 0.80), ("rec-b", 0.70), ("rec-c", 0.60)]
    diff = compare_ranked(ranked, list(ranked), [0.55, None])
    assert diff.ok and diff.problems == [] and diff.tie_swaps == [] and diff.near_threshold == []


def test_sim_drift_beyond_tolerance_fails():
    assert TOLERANCE == 0.001
    too_far = compare_ranked([("rec-a", 0.8000), ("rec-b", 0.7000)], [("rec-a", 0.8011), ("rec-b", 0.7000)], [])
    assert not too_far.ok and len(too_far.problems) == 1
    assert "rec-a" in too_far.problems[0] and "0.801100" in too_far.problems[0] and "0.800000" in too_far.problems[0]
    close_enough = compare_ranked([("rec-a", 0.8000), ("rec-b", 0.7000)], [("rec-a", 0.8009), ("rec-b", 0.6991)], [])
    assert close_enough.ok and close_enough.problems == []


def test_a_nan_similarity_is_a_problem_not_a_pass():
    assert not compare_ranked([("rec-a", 0.8)], [("rec-a", float("nan"))], []).ok


def test_order_swap_within_tolerance_is_a_tie_swap_not_a_failure():
    diff = compare_ranked(
        [("rec-a", 0.8000), ("rec-b", 0.7995), ("rec-c", 0.5)],
        [("rec-b", 0.7996), ("rec-a", 0.7999), ("rec-c", 0.5)],
        [],
    )
    assert diff.ok and diff.problems == []
    assert len(diff.tie_swaps) == 1
    assert "rec-a" in diff.tie_swaps[0] and "rec-b" in diff.tie_swaps[0] and "rec-c" not in diff.tie_swaps[0]


def test_order_swap_beyond_tolerance_fails():
    diff = compare_ranked([("rec-a", 0.80), ("rec-b", 0.70)], [("rec-b", 0.70), ("rec-a", 0.80)], [])
    assert not diff.ok and diff.tie_swaps == []
    assert len(diff.problems) == 1 and "rec-a" in diff.problems[0] and "rec-b" in diff.problems[0]


def test_a_swap_is_judged_pair_by_pair():
    # a~b and b~c differ by less than 0.001 but a and c do not: reversing all three has two ties and one real swap
    diff = compare_ranked(
        [("rec-a", 0.8008), ("rec-b", 0.8000), ("rec-c", 0.7992)],
        [("rec-c", 0.7992), ("rec-b", 0.8000), ("rec-a", 0.8008)],
        [],
    )
    assert not diff.ok
    assert len(diff.tie_swaps) == 2 and len(diff.problems) == 1
    assert "rec-a" in diff.problems[0] and "rec-c" in diff.problems[0] and "rec-b" not in diff.problems[0]


def test_different_id_sets_fail():
    diff = compare_ranked([("rec-a", 0.8), ("rec-b", 0.7)], [("rec-a", 0.8), ("rec-c", 0.7)], [])
    assert not diff.ok
    joined = " ".join(diff.problems)
    assert "rec-b" in joined and "rec-c" in joined


def test_a_shorter_or_longer_result_fails():
    assert not compare_ranked([("rec-a", 0.8), ("rec-b", 0.7)], [("rec-a", 0.8)], []).ok
    assert not compare_ranked([("rec-a", 0.8)], [("rec-a", 0.8), ("rec-b", 0.7)], []).ok
    assert compare_ranked([], [], [0.4]).ok


def test_a_repeated_id_is_a_problem():
    assert not compare_ranked([("rec-a", 0.8), ("rec-a", 0.8)], [("rec-a", 0.8), ("rec-a", 0.8)], []).ok


def test_near_threshold_values_are_listed():
    diff = compare_ranked(
        [("rec-a", 0.4005), ("rec-b", 0.9)], [("rec-a", 0.4005), ("rec-b", 0.9)], [SERVICE_MIN_SIM, GROUNDING_FLOOR]
    )
    assert diff.ok
    assert len(diff.near_threshold) == 1
    assert "rec-a" in diff.near_threshold[0] and "0.4" in diff.near_threshold[0]
    assert "rec-b" not in diff.near_threshold[0]


def test_near_threshold_looks_at_the_baseline_value_too_and_skips_missing_thresholds():
    diff = compare_ranked([("rec-a", 0.4004)], [("rec-a", 0.4012)], [None, SERVICE_MIN_SIM])
    assert diff.ok and len(diff.near_threshold) == 1            # the baseline side is within 0.001 of 0.40
    diff = compare_ranked([("rec-a", 0.4012)], [("rec-a", 0.4004)], [None, SERVICE_MIN_SIM])
    assert diff.ok and len(diff.near_threshold) == 1            # and so is the release side
    assert compare_ranked([("rec-a", 0.43)], [("rec-a", 0.43)], [None, SERVICE_MIN_SIM, GROUNDING_FLOOR]
                          ).near_threshold == []


def test_every_threshold_a_value_is_near_is_listed():
    diff = compare_ranked([("rec-a", 0.4100)], [("rec-a", 0.4100)], [0.4095, 0.4105, 0.50])
    assert len(diff.near_threshold) == 2


# --- compare_vectors ----------------------------------------------------------------------------


def _unit(angle_cos):
    """A 3-component unit vector whose cosine with [1, 0, 0] is ``angle_cos``."""
    return [angle_cos, (1 - angle_cos * angle_cos) ** 0.5, 0.0]


def test_vector_check_accepts_identical_and_scaled_vectors():
    assert VECTOR_FLOOR == 0.9999
    same_directions = ({"a": [1.0, 0.0, 0.0], "b": [0.0, 2.0, 0.0]}, {"a": [1.0, 0.0, 0.0], "b": [0.0, 0.5, 0.0]})
    assert compare_vectors(*same_directions) == []
    assert compare_vectors({"a": [1.0, 0.0, 0.0]}, {"a": _unit(0.99995)}) == []


def test_vector_check_reports_missing_and_low_cosine():
    baseline = {"a": [1.0, 0.0, 0.0], "b": [1.0, 0.0, 0.0], "c": [1.0, 0.0, 0.0]}
    current = {"a": [1.0, 0.0, 0.0], "b": _unit(0.9998), "d": [1.0, 0.0, 0.0]}
    problems = compare_vectors(baseline, current)
    assert len(problems) == 3
    by_id = {p.split(":")[0]: p for p in problems}
    assert set(by_id) == {"b", "c", "d"}
    assert "cosine" in by_id["b"] and "0.9998" in by_id["b"]
    assert "baseline" in by_id["d"]                              # in the release only
    assert "release" in by_id["c"]                               # in the baseline only


def test_vector_check_reports_a_wrong_dimension_and_a_zero_vector():
    problems = compare_vectors({"a": [1.0, 0.0, 0.0], "b": [1.0, 0.0, 0.0]}, {"a": [1.0, 0.0], "b": [0.0, 0.0, 0.0]})
    assert len(problems) == 2 and problems[0].startswith("a:") and "dimension" in problems[0]
    assert problems[1].startswith("b:")


# --- the made-up world --------------------------------------------------------------------------


def _basis(index):
    vector = [0.0] * DIMENSIONS
    vector[index] = 1.0
    return vector


class QueryEmbedder:
    """Gives each distinct text its own marker vector (component 0 = the text's number), so the fake
    search can tell which query it was asked for. Not unit length: the fake search never needs that."""

    def __init__(self, model_id):
        self.model_id = model_id
        self.texts = []

    def encode(self, texts):
        vectors = []
        for text in texts:
            if text not in self.texts:
                self.texts.append(text)
            vector = [0.0] * DIMENSIONS
            vector[0] = float(self.texts.index(text))
            vectors.append(vector)
        return vectors


def _top(*hits):
    return [{"id": i, "sim": s, "category": CATEGORY_OF[i], "subcategory": None} for i, s in hits]


def _hits(*hits):
    return [{"id": i, "service": SERVICE_OF[i], "sim": s} for i, s in hits]


def _gold(service, gq, top, verdict, top_k=4):
    return {"service": service, "id": gq["id"], "gq": gq, "top_k": top_k, "top": top, "legacy_verdict": verdict,
            "legacy_reasons": ["recorded by the legacy script"]}


def _run(run_id, queries, services, *hits, source="gold"):
    return {"id": run_id, "source": source, "queries": queries, "services": services,
            "hits": _hits(*hits)}


def _baseline():
    return {
        "commit": "f" * 40,
        "environment": {"model_name": "BAAI/bge-m3", "model_revision": BGE_M3_REVISION},
        "documents": {record_id: text_hash(text) for record_id, text in TEXTS.items()},
        "gold": [
            _gold("nid", {"id": "gq_nid_a", "query": Q_NID, "expected_category": "fees", "expected_id": "n1",
                          "min_similarity": 0.6},
                  _top(("n1", 0.80), ("n2", 0.61), ("n3", 0.52)), "PASS"),
            _gold("bdris", {"id": "gq_bdris_a", "query": Q_BDRIS, "expected_category": "bdris_faq",
                            "expected_id": "b1", "min_similarity": None},
                  _top(("b2", 0.70), ("b1", 0.65)), "PASS"),
            _gold("nid", {"id": "gq_nid_oos", "query": Q_OOS, "expected_category": None, "expected_id": None,
                          "min_similarity": None},
                  _top(("n2", 0.50)), "PASS"),
        ],
        "retrieve": [
            _run("nid/gq_nid_a/all", [Q_NID], None, ("n1", 0.80), ("n2", 0.61), ("b1", 0.45), ("b2", 0.30)),
            _run("nid/gq_nid_a/scoped", [Q_NID], ["nid"], ("n1", 0.80), ("n2", 0.61), ("n3", 0.52)),
            _run("rc_01", [Q_NID, Q_BDRIS], ["nid", "bdris"], ("n1", 0.80), ("b2", 0.70), ("b1", 0.65), ("n2", 0.61),
                 source="case"),
        ],
    }


class World:
    def __init__(self, monkeypatch):
        self.baseline = _baseline()
        self.baseline_vectors = {record_id: _basis(i) for i, record_id in enumerate(TEXTS)}
        self.release_vectors = {record_id: list(vector) for record_id, vector in self.baseline_vectors.items()}
        self.member_text_hashes = dict(self.baseline["documents"])
        self.row = {"release_id": 1, "embedding_model": FAKE_MODEL, "source_commit": COMMIT, "status": "validated"}
        self.live = ["bdris", "nid"]
        self.results = {
            (Q_NID, "nid"): [("n1", 0.80), ("n2", 0.61), ("n3", 0.52)],
            (Q_NID, "bdris"): [("b1", 0.45), ("b2", 0.30)],
            (Q_BDRIS, "bdris"): [("b2", 0.70), ("b1", 0.65)],
            (Q_BDRIS, "nid"): [("n2", 0.35), ("n3", 0.30)],
            (Q_OOS, "nid"): [("n2", 0.50)],
        }
        self.embedder = QueryEmbedder(FAKE_MODEL)
        self.searches = []                       # (query text, service, k) in the order they were asked
        monkeypatch.setattr(store, "release_row", lambda conn, rid: self.row if rid == self.row["release_id"] else None)
        monkeypatch.setattr(store, "member_text_hashes", lambda conn, rid: dict(self.member_text_hashes))
        monkeypatch.setattr(store, "stored_vectors", lambda conn, rid: copy.deepcopy(self.release_vectors))
        monkeypatch.setattr(store, "live_services", lambda conn, rid: list(self.live))
        monkeypatch.setattr(store, "search", self._search)

    def _search(self, conn, release_id, service_id, vector, k):
        query = self.embedder.texts[int(vector[0])]
        self.searches.append((query, service_id, k))
        return [
            Hit(i, SERVICE_OF[i], sim, TEXTS[i], CATEGORY_OF[i], None, None, "https://example.test/" + i)
            for i, sim in self.results.get((query, service_id), [])[:k]
        ]

    def files(self):
        return {
            BASELINE_PATH: json.dumps(self.baseline, ensure_ascii=False).encode("utf-8"),
            VECTORS_PATH: gzip.compress(json.dumps(self.baseline_vectors).encode("utf-8")),
        }

    def source(self):
        return DictSource(COMMIT, self.files())

    def run(self, source=None):
        return parity.run_parity(object(), 1, source or self.source(), self.embedder)


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def _entry(report, section, entry_id):
    return next(e for e in report[section] if e["id"] == entry_id)


# --- run_parity: the passing run ----------------------------------------------------------------


def test_a_release_that_returns_the_baseline_results_passes(world):
    report = world.run()
    assert report["ok"] is True and report["problems"] == []
    assert report["vectors"] == []
    assert [e["id"] for e in report["gold"]] == ["gq_nid_a", "gq_bdris_a", "gq_nid_oos"]
    assert [e["id"] for e in report["retrieve"]] == ["nid/gq_nid_a/all", "nid/gq_nid_a/scoped", "rc_01"]
    for entry in report["gold"]:
        assert entry["verdict_expected"] == entry["verdict_actual"] == "PASS"
        assert entry["diff"]["ok"] is True and entry["ok"] is True
    assert all(e["ok"] and e["diff"]["ok"] for e in report["retrieve"])
    assert report["tie_swaps"] == [] and report["near_threshold"] == []
    assert report["summary"] == {
        "documents": {"compared": 5, "problems": 0},
        "vectors": {"compared": 5, "failing": 0},
        "gold": {"compared": 3, "verdict_mismatches": 0, "ranked_problems": 0},
        "retrieve": {"compared": 3, "problems": 0},
        "tie_swaps": 0,
        "near_threshold": 0,
    }
    assert (report["release_id"], report["source_commit"], report["embedding_model"]) == (1, COMMIT, FAKE_MODEL)
    assert report["baseline_commit"] == "f" * 40


def test_the_report_is_plain_json_with_sorted_keys_and_does_not_change_between_runs(world):
    first = parity.format_report(world.run())
    second = parity.format_report(world.run())
    assert first == second
    parsed = json.loads(first)
    assert first == json.dumps(parsed, indent=2, sort_keys=True, ensure_ascii=False)
    assert set(parsed) >= {"ok", "vectors", "gold", "retrieve", "tie_swaps", "near_threshold", "problems", "summary"}


# --- run_parity: what is searched ---------------------------------------------------------------


GOLD_SEARCHES = [(Q_NID, "nid", 4), (Q_BDRIS, "bdris", 4), (Q_OOS, "nid", 4)]     # the world's gold, k = top_k


def test_gold_queries_are_run_with_the_recorded_query_and_top_k(world):
    world.baseline["gold"][0]["top_k"] = 2               # the baseline entry is the frozen truth, not a gold file
    report = world.run()
    assert world.searches[:3] == [(Q_NID, "nid", 2), (Q_BDRIS, "bdris", 4), (Q_OOS, "nid", 4)]
    assert report["gold"][0]["ok"] is True               # the expected top is cut to the recorded top_k as well
    assert [e["diff"]["problems"] for e in report["gold"]] == [[], [], []]


def test_retrieve_runs_use_their_recorded_queries_and_services(world):
    world.baseline["retrieve"] = [r for r in world.baseline["retrieve"] if r["id"] == "nid/gq_nid_a/scoped"]
    report = world.run()
    assert [e["id"] for e in report["retrieve"]] == ["nid/gq_nid_a/scoped"] and report["ok"] is True
    # scoped to one service: only that service is searched, with the wider candidate count of that case
    assert world.searches == GOLD_SEARCHES + [(Q_NID, "nid", 12)]

    world.searches.clear()
    world.baseline["retrieve"] = [r for r in _baseline()["retrieve"] if r["id"] == "rc_01"]
    report = world.run()
    assert [e["id"] for e in report["retrieve"]] == ["rc_01"] and report["ok"] is True
    # every query in every service of the case, in the order retrieve() asks
    assert world.searches == GOLD_SEARCHES + [
        (Q_NID, "nid", 8), (Q_NID, "bdris", 8), (Q_BDRIS, "nid", 8), (Q_BDRIS, "bdris", 8)]


def test_the_releases_own_live_services_are_searched(world):
    world.live = ["nid"]                                  # bdris is not live in this release
    world.baseline["gold"] = [e for e in world.baseline["gold"] if e["service"] == "nid"]
    world.baseline["retrieve"] = [_baseline()["retrieve"][0]]
    report = world.run()
    assert {service for _, service, _ in world.searches} == {"nid"}
    assert report["ok"] is False                          # the baseline selected bdris records too
    problem = report["retrieve"][0]["diff"]["problems"][0]
    assert "b1" in problem and "b2" in problem            # the two records of the service that is not served
    assert [e["ok"] for e in report["gold"]] == [True, True]


# --- run_parity: the checks that run first ------------------------------------------------------


def test_text_hash_mismatch_with_baseline_documents_fails_first(world):
    world.baseline["documents"]["n1"] = text_hash("some other text")
    report = world.run()
    assert report["ok"] is False
    assert "text_hash" in report["problems"][0] and "n1" in report["problems"][0]
    assert report["summary"]["documents"] == {"compared": 5, "problems": 1}
    # the rest still ran, so the report is complete
    assert len(report["gold"]) == 3 and len(report["retrieve"]) == 3
    assert report["summary"]["gold"]["compared"] == 3


def test_record_ids_that_differ_from_the_baseline_documents_fail(world):
    del world.member_text_hashes["b2"]
    world.member_text_hashes["x9"] = text_hash("a new record")
    report = world.run()
    assert report["ok"] is False
    joined = " ".join(report["problems"])
    assert "b2" in joined and "x9" in joined
    assert report["summary"]["documents"]["problems"] == 2


def test_a_long_list_of_differing_records_is_cut_short(world):
    world.baseline["documents"] = {f"r{i:03d}": text_hash(str(i)) for i in range(100)}
    report = world.run()
    line = report["problems"][0]
    assert len(line) < 1000 and "more" in line


def test_a_failing_vector_check_fails_the_run_but_everything_else_still_runs(world):
    world.release_vectors["b1"] = _basis(7)               # points somewhere else
    report = world.run()
    assert report["ok"] is False
    assert len(report["vectors"]) == 1 and report["vectors"][0].startswith("b1")
    assert report["summary"]["vectors"] == {"compared": 5, "failing": 1}
    assert any(p.startswith("vectors") for p in report["problems"])
    assert len(report["gold"]) == 3 and all(e["ok"] for e in report["gold"])


def test_missing_and_extra_vectors_are_failures(world):
    del world.release_vectors["n3"]
    world.release_vectors["x9"] = _basis(9)
    report = world.run()
    assert report["ok"] is False and report["summary"]["vectors"]["failing"] == 2


# --- run_parity: verdicts and ranked comparison -------------------------------------------------


def test_verdict_mismatch_fails_even_if_hits_match(world):
    world.baseline["gold"][0]["legacy_verdict"] = "FAIL"      # the legacy script said FAIL; the hits give PASS
    report = world.run()
    entry = _entry(report, "gold", "gq_nid_a")
    assert (entry["verdict_expected"], entry["verdict_actual"]) == ("FAIL", "PASS")
    assert entry["diff"]["ok"] is True                         # the hits themselves are identical
    assert entry["ok"] is False and report["ok"] is False
    assert report["summary"]["gold"] == {"compared": 3, "verdict_mismatches": 1, "ranked_problems": 0}
    assert any(p.startswith("gold") for p in report["problems"])


def test_a_changed_gold_result_changes_the_verdict_and_the_ranked_diff(world):
    world.results[(Q_NID, "nid")] = [("n2", 0.61), ("n3", 0.52)]      # n1 (the expected record) is gone
    report = world.run()
    entry = _entry(report, "gold", "gq_nid_a")
    assert (entry["verdict_expected"], entry["verdict_actual"]) == ("PASS", "FAIL")
    assert entry["diff"]["ok"] is False and "n1" in " ".join(entry["diff"]["problems"])
    assert report["summary"]["gold"]["verdict_mismatches"] == 1
    assert report["summary"]["gold"]["ranked_problems"] == 1


def test_a_similarity_drift_in_a_retrieve_run_fails(world):
    world.results[(Q_NID, "nid")][1] = ("n2", 0.6125)
    report = world.run()
    assert report["ok"] is False
    assert not _entry(report, "retrieve", "nid/gq_nid_a/all")["ok"]
    assert report["summary"]["retrieve"]["problems"] == 3          # every run selects n2: all, scoped and rc_01
    assert not _entry(report, "gold", "gq_nid_a")["diff"]["ok"]    # and so does the gold query


def test_a_retrieve_hit_that_belongs_to_another_service_than_recorded_fails(world):
    world.baseline["retrieve"][0]["hits"][1]["service"] = "bdris"      # n2 recorded under the wrong service
    report = world.run()
    problems = _entry(report, "retrieve", "nid/gq_nid_a/all")["diff"]["problems"]
    assert len(problems) == 1 and "n2" in problems[0] and "service" in problems[0]
    assert report["ok"] is False


def test_a_retrieve_run_with_other_records_than_recorded_fails(world):
    world.baseline["retrieve"][0]["hits"].pop()
    report = world.run()
    assert not _entry(report, "retrieve", "nid/gq_nid_a/all")["ok"] and report["ok"] is False


def test_a_tie_swap_is_reported_but_does_not_fail_the_run(world):
    # ChromaDB ranked b2 (0.7000) just above b1 (0.6995); the release has b1 (0.7001) just above b2
    world.baseline["gold"][1]["top"] = _top(("b2", 0.7000), ("b1", 0.6995))
    world.baseline["retrieve"][2]["hits"] = _hits(("n1", 0.80), ("b2", 0.7000), ("b1", 0.6995), ("n2", 0.61))
    world.results[(Q_BDRIS, "bdris")] = [("b1", 0.7001), ("b2", 0.7000)]
    report = world.run()
    assert report["ok"] is True and report["problems"] == []
    assert len(report["tie_swaps"]) == 2 and report["summary"]["tie_swaps"] == 2
    assert any("gold bdris/gq_bdris_a" in line for line in report["tie_swaps"])
    assert any("retrieve rc_01" in line for line in report["tie_swaps"])
    assert len(_entry(report, "gold", "gq_bdris_a")["diff"]["tie_swaps"]) == 1


def test_decisions_near_a_threshold_are_listed_with_their_query(world):
    world.results[(Q_NID, "bdris")][0] = ("b1", 0.4005)                 # service threshold 0.40
    world.baseline["retrieve"][0]["hits"][2]["sim"] = 0.4005
    world.results[(Q_OOS, "nid")] = [("n2", 0.5495)]                    # out-of-scope ceiling 0.55
    world.baseline["gold"][2]["top"] = _top(("n2", 0.5495))
    report = world.run()
    assert report["ok"] is True
    assert report["summary"]["near_threshold"] == len(report["near_threshold"]) == 2
    assert any(line.startswith("retrieve nid/gq_nid_a/all:") and "b1" in line for line in report["near_threshold"])
    assert any(line.startswith("gold nid/gq_nid_oos:") and "n2" in line and "0.55" in line
               for line in report["near_threshold"])


def test_retrieve_thresholds_are_the_service_minimum_and_the_grounding_floor(world):
    world.results[(Q_NID, "bdris")][0] = ("b1", 0.4195)                 # grounding floor 0.42
    world.baseline["retrieve"][0]["hits"][2]["sim"] = 0.4195
    report = world.run()
    assert report["ok"] is True
    assert len(report["near_threshold"]) == 1
    line = report["near_threshold"][0]
    assert line.startswith("retrieve nid/gq_nid_a/all:") and "0.42" in line


def test_gold_thresholds_are_the_services_ceiling_and_the_queries_min_similarity(world):
    # nid ceiling 0.55, bdris ceiling 0.62; min_similarity 0.6 of gq_nid_a
    world.results[(Q_NID, "nid")][1] = ("n2", 0.6005)
    world.baseline["gold"][0]["top"][1]["sim"] = 0.6005
    world.results[(Q_BDRIS, "bdris")] = [("b2", 0.6195), ("b1", 0.65)]
    world.baseline["gold"][1]["top"] = _top(("b2", 0.6195), ("b1", 0.65))
    report = world.run()
    lines = report["near_threshold"]
    assert all(line.startswith("gold ") for line in lines)
    assert any("gq_nid_a" in line and "n2" in line and "0.6" in line for line in lines)
    assert any("gq_bdris_a" in line and "b2" in line and "0.62" in line for line in lines)
    assert len(lines) == 2


# --- run_parity: refusals ----------------------------------------------------------------------


def test_a_release_that_does_not_exist_is_refused(world):
    with pytest.raises(ParityError, match="release 99 does not exist"):
        parity.run_parity(object(), 99, world.source(), world.embedder)
    assert world.searches == []


def test_an_embedder_that_is_not_the_releases_model_is_refused(world):
    world.row["embedding_model"] = "BAAI/bge-m3@" + BGE_M3_REVISION
    with pytest.raises(ParityError) as excinfo:
        world.run()
    assert FAKE_MODEL in str(excinfo.value) and BGE_M3_REVISION in str(excinfo.value)
    assert world.searches == []


def test_a_baseline_recorded_with_another_model_revision_is_refused(world):
    world.baseline["environment"]["model_revision"] = "0" * 40
    with pytest.raises(ParityError, match="revision") as excinfo:
        world.run()
    assert BGE_M3_REVISION in str(excinfo.value) and "0" * 40 in str(excinfo.value)
    assert world.searches == []


def test_a_baseline_without_a_recorded_model_revision_is_refused(world):
    del world.baseline["environment"]["model_revision"]
    with pytest.raises(ParityError, match="revision"):
        world.run()


def test_a_commit_without_the_baseline_files_is_refused(world):
    with pytest.raises(ParityError, match="chroma_baseline.json") as excinfo:
        world.run(DictSource(COMMIT, {}))
    assert COMMIT in str(excinfo.value)
    files = world.files()
    with pytest.raises(ParityError, match="chroma_vectors.json.gz"):
        world.run(DictSource(COMMIT, {BASELINE_PATH: files[BASELINE_PATH]}))
    assert world.searches == []


@pytest.mark.parametrize(
    "breakage", ["not json", "[]", "no gold", "no environment", "bad service", "list service", "bad vectors"]
)
def test_a_baseline_that_cannot_be_read_is_refused(world, breakage):
    files = world.files()
    baseline = dict(world.baseline)
    if breakage == "not json":
        files[BASELINE_PATH] = b"this is not json"
    elif breakage == "[]":
        files[BASELINE_PATH] = b"[]"
    elif breakage == "no gold":
        del baseline["gold"]
        files[BASELINE_PATH] = json.dumps(baseline).encode("utf-8")
    elif breakage == "no environment":
        del baseline["environment"]
        files[BASELINE_PATH] = json.dumps(baseline).encode("utf-8")
    elif breakage in ("bad service", "list service"):
        baseline["gold"] = [dict(baseline["gold"][0], service="visa" if breakage == "bad service" else ["nid"])]
        files[BASELINE_PATH] = json.dumps(baseline).encode("utf-8")
    else:
        files[VECTORS_PATH] = b"this is not gzip"
    with pytest.raises(ParityError):
        world.run(DictSource(COMMIT, files))
    assert world.searches == []


def test_without_a_source_the_releases_commit_is_opened_from_the_repository(world, monkeypatch):
    opened = []
    monkeypatch.setattr(parity, "open_repository", lambda ref: opened.append(ref) or world.source())
    report = parity.run_parity(object(), 1, None, world.embedder)
    assert opened == [COMMIT] and report["ok"] is True


def test_a_parity_error_is_a_command_error_so_the_cli_maps_it_to_exit_2():
    from gsn.release.commands import CommandError

    assert issubclass(ParityError, CommandError)


# --- store.member_text_hashes (host check with a fake connection; the SQL runs in tests/integration) -------------


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, params))

    def fetchall(self):
        return self.conn.rows


class _Conn:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def cursor(self, row_factory=None):
        return _Cursor(self)


def test_member_text_hashes_maps_each_member_to_the_text_hash_of_its_version():
    conn = _Conn([("a", "hash-a"), ("b", "hash-b")])
    assert store.member_text_hashes(conn, 7) == {"a": "hash-a", "b": "hash-b"}
    sql, params = conn.executed[0]
    assert params == (7,)
    assert "text_hash" in sql and "release_members" in sql and "record_versions" in sql
    assert store.member_text_hashes(_Conn([]), 7) == {}


# --- run_parity: a baseline that could make the gate pass vacuously ------------------------------


@pytest.mark.parametrize("section", ["gold", "retrieve"])
def test_a_baseline_with_no_gold_queries_or_no_retrieve_runs_is_refused(world, section):
    world.baseline[section] = []
    with pytest.raises(ParityError, match=f"'{section}' is empty"):
        world.run()
    assert world.searches == []


def test_a_gold_query_listed_twice_is_refused(world):
    world.baseline["gold"].append(dict(world.baseline["gold"][0]))
    with pytest.raises(ParityError, match="nid/gq_nid_a"):
        world.run()
    assert world.searches == []


def test_the_same_query_id_in_two_services_is_not_a_repeat(world):
    world.baseline["gold"].append(dict(world.baseline["gold"][1], service="nid"))     # nid/gq_bdris_a
    assert len(world.run()["gold"]) == 4


def test_a_retrieve_run_listed_twice_is_refused(world):
    world.baseline["retrieve"].append(dict(world.baseline["retrieve"][2]))
    with pytest.raises(ParityError, match="rc_01"):
        world.run()
    assert world.searches == []


def test_a_live_service_without_gold_queries_in_the_baseline_is_refused(world):
    world.live = ["bdris", "ereturn", "nid"]
    with pytest.raises(ParityError) as excinfo:
        world.run()
    assert "ereturn" in str(excinfo.value) and "bdris" not in str(excinfo.value)
    assert world.searches == []


def test_gold_queries_of_a_service_the_release_does_not_serve_are_no_reason_to_refuse(world):
    world.live = ["nid"]
    assert world.run()["summary"]["gold"]["compared"] == 3


# --- run_parity: the source must be the release's commit -----------------------------------------


def test_a_source_at_another_commit_than_the_releases_is_refused(world):
    other = "f" * 40
    with pytest.raises(ParityError) as excinfo:
        world.run(DictSource(other, world.files()))
    assert other in str(excinfo.value) and COMMIT in str(excinfo.value)
    assert world.searches == []


# --- run_parity: a service excluded just below the service minimum -------------------------------


def test_a_service_excluded_just_below_the_service_minimum_is_listed(world):
    world.results[(Q_NID, "bdris")] = [("b1", 0.3995), ("b2", 0.30)]      # bdris misses 0.40 by 0.0005
    world.baseline["retrieve"][0]["hits"] = _hits(("n1", 0.80), ("n2", 0.61))
    report = world.run()
    assert report["ok"] is True
    assert len(report["near_threshold"]) == 1
    line = report["near_threshold"][0]
    assert line.startswith("retrieve nid/gq_nid_a/all:") and "bdris" in line and "0.399500" in line
    assert "baseline" in line and "not recorded" in line
    assert report["summary"]["near_threshold"] == 1


def test_a_service_excluded_far_below_the_service_minimum_is_not_listed(world):
    world.results[(Q_NID, "bdris")] = [("b1", 0.3980), ("b2", 0.30)]
    world.baseline["retrieve"][0]["hits"] = _hits(("n1", 0.80), ("n2", 0.61))
    report = world.run()
    assert report["ok"] is True and report["near_threshold"] == []


def test_the_best_similarity_of_a_service_is_taken_across_all_queries_of_the_run(world):
    q_a, q_b = "first question of a run", "second question of a run"
    world.results.update({
        (q_a, "nid"): [("n1", 0.80)], (q_a, "bdris"): [("b1", 0.30)],
        (q_b, "nid"): [("n1", 0.79)], (q_b, "bdris"): [("b2", 0.3995)],
    })
    world.baseline["retrieve"] = [_run("multi", [q_a, q_b], None, ("n1", 0.80))]
    report = world.run()
    assert report["ok"] is True
    assert len(report["near_threshold"]) == 1
    assert report["near_threshold"][0].startswith("retrieve multi:") and "0.399500" in report["near_threshold"][0]


def test_a_service_in_the_selected_context_gets_no_extra_line(world):
    world.results[(Q_NID, "bdris")][0] = ("b1", 0.4005)                 # selected: only the hit itself is listed
    world.baseline["retrieve"][0]["hits"][2]["sim"] = 0.4005
    report = world.run()
    assert len(report["near_threshold"]) == 1 and "not recorded" not in report["near_threshold"][0]


# --- compare_ranked and run_parity: record id differences show their similarities ----------------


def test_id_set_problems_show_the_similarities_of_the_records_involved():
    diff = compare_ranked([("rec-a", 0.8), ("rec-b", 0.5000)], [("rec-a", 0.8), ("rec-c", 0.4999)], [])
    assert not diff.ok
    missing = next(p for p in diff.problems if "rec-b" in p)
    unexpected = next(p for p in diff.problems if "rec-c" in p)
    assert "rec-b (baseline similarity 0.500000)" in missing
    assert "rec-c (similarity 0.499900)" in unexpected


def test_a_boundary_tie_at_the_end_of_a_gold_top_k_can_be_told_from_a_real_miss(world):
    world.baseline["gold"][0]["top_k"] = 2
    world.baseline["gold"][0]["top"] = _top(("n1", 0.80), ("n2", 0.6100))
    world.results[(Q_NID, "nid")] = [("n1", 0.80), ("n3", 0.6095), ("n2", 0.6090)]
    report = world.run()
    problems = _entry(report, "gold", "gq_nid_a")["diff"]["problems"]
    assert any("n2" in p and "0.610000" in p for p in problems)
    assert any("n3" in p and "0.609500" in p for p in problems)
    assert report["ok"] is False
