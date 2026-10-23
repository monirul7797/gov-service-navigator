"""The parts of ``gsn.store`` that need no database: vector text and argument checks.

Everything that talks to PostgreSQL is tested against the real roles in ``tests/integration/test_store.py``.
Here a bare ``object()`` stands in for the connection: a check that runs before any SQL must raise
without touching it (``AttributeError`` would mean the code reached for the connection too early).
"""
from datetime import date

import pytest

from gsn import store
from gsn.embed import FakeEmbedder
from gsn.hashing import content_hash, text_hash
from gsn.release.fields import VersionFields
from gsn.release.inputs import ServiceEntry

COMMIT = "0123456789abcdef0123456789abcdef01234567"


def make_version(record_id, service_id="svc"):
    record = {"id": record_id, "text": f"text of {record_id}", "source_url": "https://example.test/x"}
    fields = VersionFields(
        category=None, subcategory=None, heading=None, lang="en", source_url=record["source_url"],
        url_kind="portal", official=True, source_verified=True, verified_at=date(2026, 7, 12),
    )
    return store.NewVersion(
        record_id=record_id, service_id=service_id, record=record, embed_text=record["text"],
        content_hash=content_hash(record), text_hash=text_hash(record["text"]), fields=fields,
        embedding=FakeEmbedder().encode([record["text"]])[0],
    )


def make_draft(*, member_version_ids=None, new_versions=()):
    entry = ServiceEntry("svc", "সেবা", "Service", "live", "data/chunks/svc.jsonl", "svc_gold.json", None)
    return store.Draft(
        source_commit=COMMIT, manifest_json={"services": []}, registry={"services": {}}, registry_sha256="f" * 64,
        thresholds_json={"known_failures": {}}, embedding_model="fake-sha256-v1", note=None, created_by="tester",
        services=[entry], member_version_ids=dict(member_version_ids or {}), new_versions=list(new_versions),
    )


def test_vector_text_round_trips_exactly_and_has_pgvector_syntax():
    vector = FakeEmbedder().encode(["passport"])[0]
    text = store.vector_to_text(vector)
    assert text.startswith("[") and text.endswith("]") and " " not in text
    assert text.count(",") == 1023
    assert store.vector_from_text(text) == vector  # repr(float) round-trips every double


def test_vector_text_uses_repr_of_each_component():
    assert store.vector_to_text([0.5] * 1023 + [-1e-05]) == "[" + ",".join(["0.5"] * 1023 + ["-1e-05"]) + "]"


@pytest.mark.parametrize("bad", [[0.0] * 1023, [0.0] * 1025, [], [float("nan")] + [0.0] * 1023,
                                 [float("inf")] + [0.0] * 1023])
def test_vector_to_text_refuses_wrong_dimension_and_non_finite_components(bad):
    with pytest.raises(ValueError):
        store.vector_to_text(bad)


def test_vector_from_text_reads_pgvector_output():
    assert store.vector_from_text("[1,2.5,-3e-05]") == [1.0, 2.5, -3e-05]
    assert store.vector_from_text("[0.03125]") == [0.03125]


def test_a_record_in_both_member_sets_is_refused_before_any_sql():
    draft = make_draft(member_version_ids={"rec_a": 7}, new_versions=[make_version("rec_a"), make_version("rec_b")])
    with pytest.raises(store.StoreError, match="rec_a"):
        store.create_draft(object(), draft)


def test_search_refuses_a_negative_k_before_any_sql():
    with pytest.raises(ValueError, match="k"):
        store.search(object(), 1, "svc", [0.0] * 1024, -1)


def test_set_gate_result_accepts_only_a_verdict_status():
    for status in ("draft", "active", "", "VALIDATED"):
        with pytest.raises(ValueError, match="status"):
            store.set_gate_result(object(), 1, status, {}, None)
