"""``gsn.release.build`` with the store replaced by an in-memory fake.

The real store is tested against PostgreSQL in ``tests/integration``; here only the orchestration is
under test: the order of the steps, which versions and vectors are reused, how the gate and the
override decide, and which audit rows are written. The fake keeps what ``create_draft`` was given, so
a second build sees the versions and vectors of the first, as it would in the database.
"""
import dataclasses
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

from gsn import evals, store
from gsn.embed import FakeEmbedder, ModelMissingError
from gsn.gitsource import DictSource
from gsn.hashing import content_hash, text_hash
from gsn.release import build
from gsn.release.fields import fields_hash
from gsn.retrieval import Hit

COMMIT = "0123456789abcdef0123456789abcdef01234567"
TODAY = date(2026, 10, 23)
EMBEDDER = FakeEmbedder()


def _dump(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _state() -> dict:
    """Two live services with three records each; every gold query's text is a record's own text."""
    state = {
        "manifest": {"services": []},
        "thresholds": {"known_failures": {}},
        "registry": {"services": {}},
        "corpora": {},
        "gold": {},
    }
    for sid, name_bn, name_en, field in (("alpha", "আলফা", "Alpha", "fetched_at"), ("beta", "বিটা", "Beta", None)):
        state["manifest"]["services"].append({
            "service_id": sid, "name_bn": name_bn, "name_en": name_en, "status": "live",
            "corpus": f"data/chunks/{sid}.jsonl", "gold": f"{sid}_gold_queries.json", "verified_at_field": field,
        })
        state["registry"]["services"][sid] = {}
        records, queries = [], []
        for n, category in enumerate(("fees", "process", "documents"), start=1):
            record = {
                "id": f"{sid}-{n}", "text": f"{name_en} {category} text number {n}", "heading": f"H{n}",
                "category": category, "lang": "en", "source_url": f"https://example.test/{sid}/{n}", "official": True,
            }
            if field:
                record[field] = f"2026-07-1{n}T10:00:00"
            records.append(record)
            queries.append({
                "id": f"gq_{sid}_{n}", "query": record["text"], "lang": "en",
                "expected_category": category, "expected_id": record["id"],
            })
        queries.append({"id": f"gq_{sid}_oos", "query": "what is the weather like today", "lang": "en",
                        "expected_category": None, "expected_id": None})
        state["corpora"][sid] = records
        state["gold"][sid] = {"top_k": 4, "queries": queries}
    return state


def _source(state: dict, commit: str = COMMIT) -> DictSource:
    files = {
        "data/release_manifest.json": _dump(state["manifest"]),
        "data/release_thresholds.json": _dump(state["thresholds"]),
        "concept_registry.json": _dump(state["registry"]),
    }
    for entry in state["manifest"]["services"]:
        sid = entry["service_id"]
        files[entry["corpus"]] = "".join(_dump(r).decode("utf-8") + "\n" for r in state["corpora"][sid]).encode("utf-8")
        files[entry["gold"]] = _dump(state["gold"][sid])
    return DictSource(commit, files)


class FakeConn:
    """Only ``transaction()`` is used by the build (the verdict and its audit row share one)."""

    autocommit = True

    def __init__(self, log: list[str]) -> None:
        self.log = log

    @contextmanager
    def transaction(self):
        self.log.append("begin")
        try:
            yield
        except BaseException:
            self.log.append("rollback")
            raise
        self.log.append("commit")


class SpyEmbedder(FakeEmbedder):
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.batches: list[list[str]] = []

    def encode(self, texts):
        self.batches.append(list(texts))
        self.log.append(f"encode:{len(texts)}")
        return super().encode(texts)


class World:
    """The fake store plus the inputs; ``store`` functions are replaced for the duration of a test."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.state = _state()
        self.log: list[str] = []
        self.conn = FakeConn(self.log)
        self.embedder = SpyEmbedder(self.log)
        self.record_services: dict[str, str] = {}
        self.service_names: dict[str, tuple[str, str]] = {}
        self.versions: dict[tuple[str, str, str], int] = {}  # (record_id, content_hash, fields_hash) -> version_id
        self.version_keys: list[list[tuple]] = []           # the keys each existing_versions call was given
        self.vectors: dict[str, list[float]] = {}           # text_hash -> vector
        self.drafts: list[store.Draft] = []
        self.gate_results: list[tuple] = []
        self.audits: list[tuple] = []
        self._next_version = 1
        self.fail_audit: str | None = None      # an audit action whose insert fails
        for name in ("stored_record_services", "stored_service_names", "existing_versions", "existing_vectors",
                     "create_draft", "set_gate_result", "search", "audit"):
            monkeypatch.setattr(store, name, getattr(self, name))
        for sid in ("alpha", "beta"):
            monkeypatch.setitem(evals.RULES, sid, evals.RULES["nid"])

    # --- the store interface -------------------------------------------------------------------

    def stored_record_services(self, conn):
        self.log.append("stored_record_services")
        return dict(self.record_services)

    def stored_service_names(self, conn):
        self.log.append("stored_service_names")
        return dict(self.service_names)

    def existing_versions(self, conn, keys, model):
        self.log.append("existing_versions")
        keys = list(keys)
        self.version_keys.append(keys)
        return {rid: self.versions[(rid, ch, fh)] for rid, ch, fh in keys if (rid, ch, fh) in self.versions}

    def existing_vectors(self, conn, text_hashes, model):
        self.log.append("existing_vectors")
        return {h: self.vectors[h] for h in text_hashes if h in self.vectors}

    def create_draft(self, conn, draft):
        self.log.append("create_draft")
        self.drafts.append(draft)
        for v in draft.new_versions:
            self.versions[(v.record_id, v.content_hash, v.fields_hash)] = self._next_version
            self._next_version += 1
            self.vectors[v.text_hash] = v.embedding
            self.record_services[v.record_id] = v.service_id
        for e in draft.services:
            self.service_names[e.service_id] = (e.name_bn, e.name_en)
        return len(self.drafts)

    def set_gate_result(self, conn, release_id, status, gate_report, override_reason):
        self.log.append(f"set_gate_result:{status}")
        self.gate_results.append((release_id, status, gate_report, override_reason))

    def audit(self, conn, actor, action, target, details):
        self.log.append(f"audit:{action}")
        if action == self.fail_audit:
            raise RuntimeError("audit failed")
        self.audits.append((actor, action, target, details))

    def search(self, conn, release_id, service_id, vector, k):
        rows = []
        for record in self.state["corpora"][service_id]:
            sim = sum(a * b for a, b in zip(vector, EMBEDDER.encode([record["text"]])[0]))
            rows.append(Hit(record["id"], service_id, sim, record["text"], record.get("category"), None,
                            record.get("heading"), record["source_url"]))
        rows.sort(key=lambda h: (-h.sim, h.record_id))
        return rows[:k]

    # --- driving it ----------------------------------------------------------------------------

    def build(self, **overrides) -> build.BuildResult:
        kwargs = {"actor": "tester", "note": None, "override": None, "today": TODAY, **overrides}
        return build.build_release(self.conn, _source(self.state), self.embedder, **kwargs)

    def writes(self) -> list[str]:
        return [entry for entry in self.log if entry.startswith(("create_draft", "set_gate_result", "audit:"))]


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


# --- a clean build -------------------------------------------------------------------------------


def test_clean_build_is_validated_and_reports_what_it_did(world):
    result = world.build()
    assert (result.release_id, result.status, result.problems) == (1, "validated", [])
    assert (result.records, result.embedded, result.reused_versions, result.copied_vectors) == (6, 6, 0, 0)
    assert result.services == ["alpha", "beta"] and result.overridden is False
    assert result.commit == COMMIT and result.embedding_model == "fake-sha256-v1"
    assert result.gate is not None and result.gate.passed and result.gate.unexpected_failures == []
    assert result.gate.counts["alpha"]["PASS"] == 4 and result.gate.counts["beta"]["PASS"] == 4


def test_the_steps_run_in_order_and_new_texts_are_embedded_before_the_draft_is_written(world):
    world.build()
    log = world.log
    first_encode = log.index("encode:6")
    assert log.index("stored_record_services") < log.index("existing_versions") < log.index("existing_vectors")
    assert log.index("existing_vectors") < first_encode < log.index("create_draft")
    assert log.index("create_draft") < log.index("audit:release.build") < log.index("set_gate_result:validated")
    assert log.index("set_gate_result:validated") < log.index("audit:release.validate")
    # the gold queries are embedded after the draft exists: one text per call
    assert log[log.index("audit:release.build") + 2] == "encode:1"  # after the draft's "commit"
    assert world.embedder.batches[0] == [r["text"] for sid in ("alpha", "beta") for r in world.state["corpora"][sid]]


def test_the_draft_and_its_build_audit_row_are_one_transaction_and_the_verdict_another(world):
    world.build()
    log = world.log
    assert log.count("begin") == log.count("commit") == 2
    first_begin, first_end = log.index("begin"), log.index("commit")
    second_begin = log.index("begin", first_end)
    # the draft and the audit row that records it commit together: a crash cannot leave a draft unrecorded ...
    assert log[first_begin + 1:first_end] == ["create_draft", "audit:release.build"]
    # ... the verdict and its audit row are another transaction, never the draft's (the member guard holds a
    # FOR SHARE lock on the release row until the draft's transaction ends) ...
    assert log[second_begin + 1:log.index("commit", second_begin)] == [
        "set_gate_result:validated", "audit:release.validate"]
    # ... and nothing runs inside a transaction while the gold queries are embedded and searched
    between = log[first_end + 1:second_begin]
    assert between and all(entry.startswith("encode:") for entry in between)


def test_a_failing_build_audit_row_rolls_the_draft_back(world):
    world.fail_audit = "release.build"
    with pytest.raises(RuntimeError, match="audit failed"):
        world.build()
    assert world.log[-3:] == ["create_draft", "audit:release.build", "rollback"]
    assert "set_gate_result:validated" not in world.log and "commit" not in world.log
    assert world.audits == [] and world.gate_results == []
    assert len(world.embedder.batches) == 1  # only the record texts: no gold query ran on the abandoned draft


def test_the_draft_holds_the_derived_columns_and_provenance(world):
    world.build(actor="alice", note="first build")
    draft = world.drafts[0]
    assert (draft.source_commit, draft.embedding_model, draft.created_by, draft.note) == (
        COMMIT, "fake-sha256-v1", "alice", "first build")
    assert draft.member_version_ids == {} and len(draft.new_versions) == 6
    assert [e.service_id for e in draft.services] == ["alpha", "beta"]
    assert draft.manifest_json == world.state["manifest"] and draft.registry == world.state["registry"]
    assert draft.thresholds_json == world.state["thresholds"]
    assert len(draft.registry_sha256) == 64
    v = next(v for v in draft.new_versions if v.record_id == "alpha-1")
    record = world.state["corpora"]["alpha"][0]
    assert v.record == record and v.embed_text == record["text"] and v.service_id == "alpha"
    assert v.content_hash == content_hash(record) and v.text_hash == text_hash(record["text"])
    assert v.embedding == EMBEDDER.encode([record["text"]])[0]
    assert v.fields.verified_at == date(2026, 7, 11) and v.fields.url_kind == "portal"
    beta = next(v for v in draft.new_versions if v.record_id == "beta-1")
    assert beta.fields.verified_at is None


def test_every_new_version_carries_the_hash_of_its_derived_columns(world):
    world.build()
    for v in world.drafts[0].new_versions:
        assert v.fields_hash == fields_hash(v.fields) and len(v.fields_hash) == 64
    assert len({v.fields_hash for v in world.drafts[0].new_versions}) == 6


def test_existing_versions_are_looked_up_by_record_content_and_derived_columns(world):
    world.build()
    keys = world.version_keys[0]
    assert len(keys) == 6 and all(len(key) == 3 for key in keys)
    by_id = {v.record_id: v for v in world.drafts[0].new_versions}
    # on the second build the very same triples are asked for and found
    world.build()
    assert {key[0]: key for key in world.version_keys[1]} == {
        rid: (rid, v.content_hash, v.fields_hash) for rid, v in by_id.items()}


def test_the_gate_report_keeps_every_verdict(world):
    world.build()
    release_id, status, report, override_reason = world.gate_results[0]
    assert (release_id, status, override_reason) == (1, "validated", None)
    assert report["commit"] == COMMIT and report["embedding_model"] == "fake-sha256-v1"
    assert report["passed"] is True and report["overridden"] is False
    assert report["integrity_problems"] == [] and report["unexpected_failures"] == []
    assert report["counts"]["alpha"]["PASS"] == 4
    assert len(report["verdicts"]) == 8
    first = report["verdicts"][0]
    assert first["query_id"] == "gq_alpha_1" and first["service_id"] == "alpha" and first["verdict"] == "PASS"
    assert first["top"][0] == ["alpha-1", pytest.approx(1.0)]
    assert report["build"] == {"records": 6, "embedded": 6, "reused_versions": 0, "copied_vectors": 0}
    json.dumps(report)  # it must be storable as JSON


def test_audit_rows_name_the_actor_and_the_release(world):
    world.build(actor="alice")
    assert [(a[0], a[1], a[2]) for a in world.audits] == [
        ("alice", "release.build", "release:1"), ("alice", "release.validate", "release:1")]
    build_details, validate_details = world.audits[0][3], world.audits[1][3]
    assert build_details == {"commit": COMMIT, "records": 6, "services": 2, "embedded": 6,
                             "reused_versions": 0, "copied_vectors": 0}
    assert validate_details["unexpected_failures"] == [] and validate_details["counts"]["beta"]["PASS"] == 4


def test_a_connection_that_is_not_in_autocommit_mode_is_refused_before_anything_happens(world):
    world.conn.autocommit = False
    with pytest.raises(ValueError, match="autocommit"):
        world.build()
    assert world.log == []


# --- the model must be there before anything is written ------------------------------------------------


def test_the_embedder_is_asked_if_it_is_available_before_the_draft_is_written(world):
    world.embedder.ensure_available = lambda: world.log.append("ensure_available")
    world.build()
    assert world.log.index("ensure_available") < world.log.index("create_draft")
    assert world.log.index("ensure_available") < world.log.index("encode:6")


def test_a_missing_model_stops_the_build_with_nothing_written(world):
    def missing():
        world.log.append("ensure_available")
        raise ModelMissingError("run: gsn models fetch")

    world.embedder.ensure_available = missing
    with pytest.raises(ModelMissingError):
        world.build()
    assert world.writes() == [] and world.drafts == [] and world.audits == [] and world.gate_results == []
    assert world.embedder.batches == [] and "begin" not in world.log


def test_a_missing_model_stops_a_rebuild_even_when_every_vector_would_be_reused(world):
    world.build()
    assert len(world.drafts) == 1
    writes_before = world.writes()

    def missing():
        raise ModelMissingError("run: gsn models fetch")

    world.embedder.ensure_available = missing
    with pytest.raises(ModelMissingError):
        world.build()
    assert len(world.drafts) == 1 and world.writes() == writes_before  # no second release row, no audit row


def test_an_embedder_without_ensure_available_still_builds(world):
    class Plain:
        model_id = "fake-sha256-v1"

        def encode(self, texts):
            return FakeEmbedder().encode(texts)

    world.embedder = Plain()
    assert world.build().status == "validated"


# --- integrity ----------------------------------------------------------------------------------


def test_integrity_problem_writes_nothing_and_embeds_nothing(world):
    world.state["corpora"]["alpha"][1]["text"] = ""
    result = world.build()
    assert result.status == "integrity_failed" and result.release_id is None
    assert result.gate is None and (result.embedded, result.reused_versions) == (0, 0)
    assert any("alpha-2" in p for p in result.problems)
    assert world.writes() == [] and world.embedder.batches == []
    assert "existing_versions" not in world.log


def test_every_integrity_problem_is_reported(world):
    world.state["corpora"]["alpha"][1]["text"] = ""
    world.state["corpora"]["beta"][0]["source_url"] = ""
    del world.state["registry"]["services"]["beta"]
    assert len(world.build().problems) == 3


def test_unreadable_inputs_are_an_integrity_failure(world):
    source = _source(world.state)
    del source._files["data/release_manifest.json"]
    result = build.build_release(world.conn, source, world.embedder, actor="t", note=None, override=None, today=TODAY)
    assert result.status == "integrity_failed" and result.release_id is None
    assert any("release_manifest.json" in p for p in result.problems)
    assert world.writes() == []


def test_an_unknown_known_failure_id_is_an_integrity_failure(world):
    world.state["thresholds"]["known_failures"] = {"gq_does_not_exist": "typo"}
    result = world.build()
    assert result.status == "integrity_failed" and any("gq_does_not_exist" in p for p in result.problems)
    assert world.writes() == []


def test_a_live_service_without_an_eval_rule_is_an_integrity_failure(world, monkeypatch):
    monkeypatch.delitem(evals.RULES, "beta")
    result = world.build()
    assert result.status == "integrity_failed" and any("beta" in p and "eval" in p for p in result.problems)
    assert world.writes() == []


def test_a_service_that_moved_is_an_integrity_failure(world):
    world.record_services["alpha-1"] = "beta"
    result = world.build()
    assert result.status == "integrity_failed" and any("alpha-1" in p for p in result.problems)
    assert world.writes() == []


def test_the_date_limit_is_the_given_today(world):
    world.state["corpora"]["alpha"][0]["fetched_at"] = "2026-10-24T01:00:00"
    assert world.build(today=date(2026, 10, 23)).status == "integrity_failed"
    assert world.build(today=date(2026, 10, 24)).status == "validated"


# --- reuse --------------------------------------------------------------------------------------


def test_rebuilding_the_same_commit_reuses_every_version_and_embeds_nothing(world):
    first = world.build()
    batches_after_first = len(world.embedder.batches)
    second = world.build()
    assert first.release_id == 1 and second.release_id == 2
    assert (second.embedded, second.reused_versions, second.copied_vectors, second.records) == (0, 6, 0, 6)
    draft = world.drafts[1]
    assert draft.new_versions == [] and sorted(draft.member_version_ids) == sorted(
        r["id"] for sid in ("alpha", "beta") for r in world.state["corpora"][sid])
    # only the gold queries were embedded the second time
    assert all(len(b) == 1 for b in world.embedder.batches[batches_after_first:])


def test_a_changed_provenance_field_is_a_new_version_that_copies_the_vector(world):
    world.build()
    world.state["corpora"]["alpha"][0]["fetched_at"] = "2026-08-01T09:00:00"
    second = world.build()
    assert (second.embedded, second.reused_versions, second.copied_vectors) == (0, 5, 1)
    draft = world.drafts[1]
    (new,) = draft.new_versions
    assert new.record_id == "alpha-1" and new.fields.verified_at == date(2026, 8, 1)
    assert new.embedding == world.drafts[0].new_versions[0].embedding
    assert "alpha-1" not in draft.member_version_ids and len(draft.member_version_ids) == 5


def test_a_changed_manifest_mapping_is_a_new_version_per_dated_record_and_embeds_nothing(world):
    mapping = world.state["manifest"]["services"][0]
    mapping["verified_at_field"] = None          # alpha's records carry fetched_at, but nothing maps it
    first = world.build()
    assert first.status == "validated"
    assert all(v.fields.verified_at is None for v in world.drafts[0].new_versions)

    mapping["verified_at_field"] = "fetched_at"  # now it is mapped: same records, different derived columns
    second = world.build()
    assert second.status == "validated"
    assert (second.embedded, second.reused_versions, second.copied_vectors) == (0, 3, 3)
    draft = world.drafts[1]
    assert sorted(v.record_id for v in draft.new_versions) == ["alpha-1", "alpha-2", "alpha-3"]
    assert sorted(draft.member_version_ids) == ["beta-1", "beta-2", "beta-3"]  # undated service: unchanged
    for new in draft.new_versions:
        old = next(v for v in world.drafts[0].new_versions if v.record_id == new.record_id)
        assert new.content_hash == old.content_hash and new.fields_hash != old.fields_hash
        assert new.fields.verified_at is not None and new.embedding == old.embedding

    third = world.build()  # nothing changed since: everything is reused
    assert (third.embedded, third.reused_versions, third.copied_vectors) == (0, 6, 0)
    assert world.drafts[2].new_versions == []


def test_changed_text_is_embedded_and_only_that_text(world):
    world.build()
    world.state["corpora"]["beta"][2]["text"] = "Beta documents text, revised"
    batches = len(world.embedder.batches)
    second = world.build()
    assert (second.embedded, second.reused_versions, second.copied_vectors) == (1, 5, 0)
    assert world.embedder.batches[batches] == ["Beta documents text, revised"]


def test_an_identical_text_in_a_new_record_copies_the_stored_vector(world):
    world.build()
    world.state["corpora"]["beta"][2]["id"] = "beta-3-moved-id"
    world.state["gold"]["beta"]["queries"][2]["expected_id"] = "beta-3-moved-id"
    second = world.build()
    assert (second.embedded, second.reused_versions, second.copied_vectors) == (0, 5, 1)


def test_a_wrong_number_of_vectors_from_the_embedder_is_refused_before_anything_is_written(world):
    class Broken(FakeEmbedder):
        def encode(self, texts):
            return super().encode(texts)[:-1]

    with pytest.raises(RuntimeError, match="5 vectors for 6 texts"):
        build.build_release(world.conn, _source(world.state), Broken(), actor="t", note=None, override=None,
                            today=TODAY)
    assert world.writes() == []


# --- the gate -----------------------------------------------------------------------------------


def _break_one_gold_query(world):
    """alpha's first query now expects the second record, which is not the best hit; only the best is kept."""
    world.state["gold"]["alpha"]["top_k"] = 1
    world.state["gold"]["alpha"]["queries"][0]["expected_id"] = "alpha-2"


def test_a_failing_gold_query_rejects_the_release(world):
    _break_one_gold_query(world)
    result = world.build()
    assert (result.release_id, result.status, result.overridden) == (1, "rejected", False)
    assert result.gate.unexpected_failures == ["gq_alpha_1"]
    assert world.gate_results[0][1:2] == ("rejected",) and world.gate_results[0][3] is None
    assert [a[1] for a in world.audits] == ["release.build", "release.reject"]
    assert world.audits[1][3]["unexpected_failures"] == ["gq_alpha_1"]
    assert world.audits[1][3]["counts"]["alpha"]["FAIL"] == 1


def test_a_known_failure_does_not_reject(world):
    _break_one_gold_query(world)
    world.state["thresholds"]["known_failures"] = {"gq_alpha_1": "documented reason"}
    result = world.build()
    assert result.status == "validated" and result.overridden is False
    assert result.gate.known_failures_seen == ["gq_alpha_1"] and result.gate.unexpected_failures == []
    assert [a[1] for a in world.audits] == ["release.build", "release.validate"]
    assert world.gate_results[0][3] is None


def test_an_override_validates_a_failing_release_and_records_the_reason(world):
    _break_one_gold_query(world)
    result = world.build(override="owner reviewed gq_alpha_1")
    assert (result.status, result.overridden) == ("validated", True)
    assert result.override_reason == "owner reviewed gq_alpha_1"
    assert result.gate.unexpected_failures == ["gq_alpha_1"]
    assert world.gate_results[0][1] == "validated" and world.gate_results[0][3] == "owner reviewed gq_alpha_1"
    assert [a[1] for a in world.audits] == ["release.build", "release.override"]
    details = world.audits[1][3]
    assert details["override_reason"] == "owner reviewed gq_alpha_1" and details["unexpected_failures"] == ["gq_alpha_1"]
    assert world.gate_results[0][2]["overridden"] is True
    second_begin = world.log.index("begin", world.log.index("commit"))
    assert world.log[second_begin + 1:world.log.index("commit", second_begin)] == [
        "set_gate_result:validated", "audit:release.override"]


def test_an_override_that_is_not_needed_is_not_recorded(world):
    result = world.build(override="just in case")
    assert (result.status, result.overridden) == ("validated", False)
    assert result.override_given_but_not_needed is True
    assert world.gate_results[0][3] is None
    assert [a[1] for a in world.audits] == ["release.build", "release.validate"]


def test_rejected_then_rebuilt_with_the_same_commit_reuses_everything(world):
    _break_one_gold_query(world)
    assert world.build().status == "rejected"
    again = world.build(override="reviewed")
    assert (again.release_id, again.status, again.embedded, again.reused_versions) == (2, "validated", 0, 6)


def test_a_hidden_service_is_a_member_but_its_gold_queries_do_not_run(world):
    world.state["manifest"]["services"][1]["status"] = "hidden"
    del world.state["registry"]["services"]["beta"]
    result = world.build()
    assert result.status == "validated" and result.records == 6 and result.services == ["alpha", "beta"]
    assert list(result.gate.counts) == ["alpha"]
    assert len(world.drafts[0].new_versions) == 6


def test_the_embedding_model_comes_from_the_embedder(world):
    class Other(SpyEmbedder):
        model_id = "other-model-v1"

    other = Other(world.log)
    result = build.build_release(world.conn, _source(world.state), other, actor="t", note=None, override=None,
                                 today=TODAY)
    assert result.embedding_model == "other-model-v1" and world.drafts[0].embedding_model == "other-model-v1"


# --- today in Dhaka -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utc, expected",
    [
        (datetime(2026, 10, 23, 17, 59, tzinfo=timezone.utc), date(2026, 10, 23)),
        (datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc), date(2026, 10, 24)),
        (datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc), date(2026, 10, 23)),
    ],
)
def test_dhaka_today_is_utc_plus_six(utc, expected):
    assert build.dhaka_today(utc) == expected
    assert build.dhaka_today(utc.astimezone(timezone(timedelta(hours=-5)))) == expected  # whatever the zone given


def test_dhaka_today_defaults_to_now():
    assert build.dhaka_today() == datetime.now(timezone(timedelta(hours=6))).date()


# --- the text summary ---------------------------------------------------------------------------


def test_the_first_line_of_the_summary_is_machine_readable(world):
    text = build.format_build_summary(world.build())
    assert text.splitlines()[0] == "release_id=1 status=validated"
    assert "records: 6" in text and "embedded: 6" in text and "alpha:" in text and "beta:" in text


def test_the_summary_of_an_integrity_failure_lists_the_problems(world):
    world.state["corpora"]["alpha"][1]["text"] = ""
    lines = build.format_build_summary(world.build()).splitlines()
    assert lines[0] == "release_id=- status=integrity_failed"
    assert any("alpha-2" in line for line in lines[1:])


def test_the_summary_of_a_rejected_build_names_the_failing_queries(world):
    _break_one_gold_query(world)
    text = build.format_build_summary(world.build())
    assert text.splitlines()[0] == "release_id=1 status=rejected"
    assert "gq_alpha_1" in text and "--override" in text


def test_a_long_list_of_failures_is_cut_short_in_the_summary():
    ids = [f"gq_{n}" for n in range(25)]
    assert build.listed(ids[:3]) == "gq_0, gq_1, gq_2"
    assert build.listed(ids[:10]) == ", ".join(ids[:10])
    cut = build.listed(ids)
    assert cut.startswith(", ".join(ids[:10])) and "gq_10" not in cut and "and 15 more" in cut


def test_the_summary_says_when_an_override_was_given_but_the_gate_passed(world):
    text = build.format_build_summary(world.build(override="just in case"))
    assert "override not needed: the gate passed (no override recorded)" in text
    assert "just in case" not in text
    assert "override not needed" not in build.format_build_summary(world.build())
    assert "override not needed" not in build.format_build_summary(world.build(override=None))


def test_the_publish_hint_is_the_container_command(world):
    text = build.format_build_summary(world.build())
    hint = "docker compose --profile tools run --rm release release publish 1"
    # the fake embedder's releases are refused by publish without the flag, so the hint names it
    assert f"{hint} --allow-fake" in text
    assert "gsn release publish" not in text
    real = dataclasses.replace(world.build(), embedding_model="BAAI/bge-m3@abc")
    real_text = build.format_build_summary(real)
    assert "docker compose --profile tools run --rm release release publish 2" in real_text
    assert "--allow-fake" not in real_text


def test_the_summary_of_an_override_says_so(world):
    _break_one_gold_query(world)
    text = build.format_build_summary(world.build(override="because"))
    assert text.splitlines()[0] == "release_id=1 status=validated"
    assert "override" in text and "because" in text
