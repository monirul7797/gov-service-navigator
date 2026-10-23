"""``gsn.store``: every SQL statement the release CLI runs, tested as the real ``gsn_worker`` role.

The stack tests share one database across runs, so every test makes its own service and record ids
(a random suffix) and never assumes an empty database. Embeddings come from ``FakeEmbedder`` (the
tests then also exercise the text round trip of vectors), or are written out by hand where a test
needs a known cosine similarity. ``release_helpers.py`` holds the helpers shared with the schema tests.
"""
from datetime import date

import pytest
from release_helpers import COMMIT, MODEL, add_record, audit_rows, new_actor, new_tag

from gsn import store
from gsn.embed import FakeEmbedder
from gsn.hashing import HASH_VERSION, content_hash, text_hash
from gsn.release.fields import VersionFields
from gsn.release.inputs import ServiceEntry

EMBEDDER = FakeEmbedder()
UNKNOWN_ID = 2**62  # no identity column will ever reach it


# --- builders ----------------------------------------------------------------------------------


def entry(service_id, status="live"):
    return ServiceEntry(
        service_id, f"সেবা {service_id}", f"Service {service_id}", status,
        f"data/chunks/{service_id}.jsonl", f"{service_id}_gold.json", None,
    )


def new_version(service_id, record_id, text, *, embedding=None, **extra):
    """A ``NewVersion`` of ``record_id``; ``extra`` record fields change its content hash."""
    record = {
        "id": record_id, "text": text, "source_url": f"https://example.test/{record_id}",
        "category": "process_steps", **extra,
    }
    fields = VersionFields(
        category="process_steps", subcategory="sub", heading=f"Heading {record_id}", lang="en",
        source_url=record["source_url"], url_kind="portal", official=True, source_verified=True,
        verified_at=date(2026, 7, 12),
    )
    return store.NewVersion(
        record_id=record_id, service_id=service_id, record=record, embed_text=text,
        content_hash=content_hash(record), text_hash=text_hash(text), fields=fields,
        embedding=embedding if embedding is not None else EMBEDDER.encode([text])[0],
    )


def make_draft(tag, services, new_versions, member_version_ids=None, *, note=None):
    return store.Draft(
        source_commit=COMMIT,
        manifest_json={"services": [{"service_id": s.service_id} for s in services]},
        registry={"services": {s.service_id: {"names": ["a", "b"]} for s in services}},
        registry_sha256="f" * 64,
        thresholds_json={"known_failures": {}},
        embedding_model=MODEL,
        note=note or f"store test {tag}",
        created_by="itest-store",
        services=list(services),
        member_version_ids=dict(member_version_ids or {}),
        new_versions=list(new_versions),
    )


def member_versions(conn, release_id):
    """record_id -> version_id of a release's members."""
    return dict(conn.execute(
        "SELECT record_id, version_id FROM release_members WHERE release_id = %s", (release_id,)
    ).fetchall())


def count(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def nothing_left_behind(conn, *, note, service_ids, record_ids):
    """A refused draft must not have created a release, a service, a record or a version."""
    assert count(conn, "SELECT count(*) FROM releases WHERE note = %s", note) == 0
    assert count(conn, "SELECT count(*) FROM services WHERE service_id = ANY(%s)", list(service_ids)) == 0
    assert count(conn, "SELECT count(*) FROM records WHERE record_id = ANY(%s)", list(record_ids)) == 0
    assert count(conn, "SELECT count(*) FROM record_versions WHERE record_id = ANY(%s)", list(record_ids)) == 0


def validated_release(conn, tag, suffix):
    """A tiny validated release (its own service), built and judged through the store."""
    sid = f"svc_{tag}_{suffix}"
    release_id = store.create_draft(
        conn, make_draft(tag, [entry(sid)], [new_version(sid, f"rec_{tag}_{suffix}", f"text {suffix} {tag}")])
    )
    store.set_gate_result(conn, release_id, "validated", {"verdict": "validated"}, None)
    return release_id


def basis(*components):
    """A 1024-dimension vector with the given leading components (unit length if they are)."""
    vector = [0.0] * 1024
    vector[: len(components)] = components
    return vector


# --- create_draft ------------------------------------------------------------------------------


def test_create_draft_inserts_everything_in_one_transaction(connect):
    worker = connect("worker")
    tag = new_tag()
    sid = f"svc_{tag}"
    versions = [new_version(sid, f"rec_{tag}_{i}", f"text {i} of {tag}", fee=100 + i) for i in range(3)]
    release_id = store.create_draft(worker, make_draft(tag, [entry(sid)], versions))
    assert isinstance(release_id, int)

    assert worker.execute("SELECT name_bn, name_en FROM services WHERE service_id = %s", (sid,)).fetchone() == (
        f"সেবা {sid}", f"Service {sid}")
    assert sorted(r[0] for r in worker.execute("SELECT record_id FROM records WHERE service_id = %s", (sid,))) == [
        v.record_id for v in versions]

    for version in versions:
        row = worker.execute(
            "SELECT content_hash, text_hash, hash_version, record, embed_text, category, subcategory, heading, "
            "lang, source_url, url_kind, official, source_verified, verified_at, embedding_model, source_commit, "
            "created_by, change_note, embedding::text FROM record_versions WHERE record_id = %s",
            (version.record_id,),
        ).fetchall()
        assert len(row) == 1
        (chash, thash, hver, record, text, category, subcategory, heading, lang, url, kind, official, verified,
         verified_at, model, commit, created_by, change_note, embedding) = row[0]
        assert (chash, thash, hver) == (version.content_hash, version.text_hash, HASH_VERSION)
        assert record == version.record and text == version.embed_text
        assert (category, subcategory, heading, lang) == ("process_steps", "sub", f"Heading {version.record_id}", "en")
        assert (url, kind, official, verified, verified_at) == (version.fields.source_url, "portal", True, True,
                                                                date(2026, 7, 12))
        assert (model, commit, created_by, change_note) == (MODEL, COMMIT, "itest-store", None)
        assert store.vector_from_text(embedding) == pytest.approx(version.embedding, abs=1e-7)

    assert worker.execute(
        "SELECT status, source_commit, manifest, registry, registry_sha256, thresholds, gate_report, "
        "embedding_model, note, override_reason, created_by FROM releases WHERE release_id = %s",
        (release_id,),
    ).fetchone() == (
        "draft", COMMIT, {"services": [{"service_id": sid}]}, {"services": {sid: {"names": ["a", "b"]}}}, "f" * 64,
        {"known_failures": {}}, None, MODEL, f"store test {tag}", None, "itest-store")
    assert worker.execute(
        "SELECT service_id, status FROM release_services WHERE release_id = %s", (release_id,)
    ).fetchall() == [(sid, "live")]
    assert set(member_versions(worker, release_id)) == {v.record_id for v in versions}
    assert worker.execute(  # each member is the version just created for that record
        "SELECT count(*) FROM release_members m JOIN record_versions v ON v.version_id = m.version_id "
        "WHERE m.release_id = %s AND v.content_hash = ANY(%s)", (release_id, [v.content_hash for v in versions]),
    ).fetchone()[0] == 3

    # A failure part-way: the second version of one record is a second member of it, which the
    # database refuses only after the service, the records, the versions, the release and its
    # services have been inserted. None of that may survive.
    tag = new_tag()
    sid = f"svc_{tag}"
    clash = [new_version(sid, f"rec_{tag}_x", "first text", n=1), new_version(sid, f"rec_{tag}_x", "second text", n=2),
             new_version(sid, f"rec_{tag}_y", "other text")]
    with pytest.raises(store.StoreError, match="release_members"):
        store.create_draft(worker, make_draft(tag, [entry(sid)], clash))
    nothing_left_behind(worker, note=f"store test {tag}", service_ids=[sid],
                        record_ids=[f"rec_{tag}_x", f"rec_{tag}_y"])
    assert count(worker, "SELECT 1") == 1  # the connection is still usable


def test_a_reused_member_that_does_not_fit_leaves_nothing_behind(connect):
    worker = connect("worker")
    tag = new_tag()
    old_sid = f"svc_{tag}_old"
    old = [new_version(old_sid, f"rec_{tag}_1", "one"), new_version(old_sid, f"rec_{tag}_2", "two")]
    old_release = store.create_draft(worker, make_draft(tag, [entry(old_sid)], old))
    versions = member_versions(worker, old_release)

    sid = f"svc_{tag}_new"
    fresh = new_version(sid, f"rec_{tag}_fresh", "fresh text")
    mismatched = {f"rec_{tag}_1": versions[f"rec_{tag}_2"]}  # a version of another record
    with pytest.raises(store.StoreError, match="foreign key"):
        store.create_draft(worker, make_draft(tag, [entry(old_sid), entry(sid)], [fresh], mismatched,
                                              note=f"mismatch {tag}"))
    nothing_left_behind(worker, note=f"mismatch {tag}", service_ids=[sid], record_ids=[fresh.record_id])


def test_a_record_in_both_member_sets_is_refused(connect):
    worker = connect("worker")
    tag = new_tag()
    sid = f"svc_{tag}"
    version = new_version(sid, f"rec_{tag}", "text")
    with pytest.raises(store.StoreError, match=f"rec_{tag}"):
        store.create_draft(worker, make_draft(tag, [entry(sid)], [version], {f"rec_{tag}": 1}))
    nothing_left_behind(worker, note=f"store test {tag}", service_ids=[sid], record_ids=[version.record_id])


def test_create_draft_reuses_services_records_and_versions(connect):
    worker = connect("worker")
    tag = new_tag()
    sid = f"svc_{tag}"
    r0, r1, r2 = (f"rec_{tag}_{i}" for i in range(3))
    first = store.create_draft(worker, make_draft(tag, [entry(sid)], [
        new_version(sid, r0, "zero text"), new_version(sid, r1, "one text")], note="first"))
    first_members = member_versions(worker, first)

    # A later build: r0 unchanged (reused), r1 edited (a new version), r2 new. The service and r0, r1
    # already exist, which `ON CONFLICT DO NOTHING` must accept for the restricted worker role.
    second = store.create_draft(
        worker,
        make_draft(tag, [entry(sid)],
                   [new_version(sid, r1, "one text", fee=250), new_version(sid, r2, "two text")],
                   {r0: first_members[r0]}, note="second"),
    )
    second_members = member_versions(worker, second)
    assert second != first
    assert second_members[r0] == first_members[r0]            # reused, not copied
    assert second_members[r1] != first_members[r1]            # an edited record gets a new version
    assert set(second_members) == {r0, r1, r2}
    assert member_versions(worker, first) == first_members    # the first release is untouched
    assert count(worker, "SELECT count(*) FROM services WHERE service_id = %s", sid) == 1
    assert count(worker, "SELECT count(*) FROM records WHERE service_id = %s", sid) == 3
    assert count(worker, "SELECT count(*) FROM record_versions WHERE record_id = %s", r0) == 1
    assert count(worker, "SELECT count(*) FROM record_versions WHERE record_id = %s", r1) == 2


def test_create_draft_records_each_service_with_its_status(connect):
    worker = connect("worker")
    tag = new_tag()
    live, hidden = f"svc_{tag}_live", f"svc_{tag}_hidden"
    release_id = store.create_draft(worker, make_draft(
        tag, [entry(live), entry(hidden, "hidden")],
        [new_version(live, f"rec_{tag}_l", "live text"), new_version(hidden, f"rec_{tag}_h", "hidden text")]))
    assert dict(worker.execute(
        "SELECT service_id, status FROM release_services WHERE release_id = %s", (release_id,)).fetchall()
    ) == {live: "live", hidden: "hidden"}


def test_create_draft_refuses_a_record_that_belongs_to_another_service(connect):
    """`ON CONFLICT DO NOTHING` must not hide that a stored record sits under a different service."""
    worker = connect("worker")
    tag = new_tag()
    s1, s2 = f"svc_{tag}_1", f"svc_{tag}_2"
    store.create_draft(worker, make_draft(tag, [entry(s1)], [new_version(s1, f"rec_{tag}", "text")], note="setup"))
    moved = new_version(s2, f"rec_{tag}", "text", moved=True)
    with pytest.raises(store.StoreError, match=f"rec_{tag}"):
        store.create_draft(worker, make_draft(tag, [entry(s1), entry(s2)], [moved], note=f"moved {tag}"))
    assert count(worker, "SELECT count(*) FROM releases WHERE note = %s", f"moved {tag}") == 0
    assert count(worker, "SELECT count(*) FROM services WHERE service_id = %s", s2) == 0
    assert count(worker, "SELECT count(*) FROM record_versions WHERE record_id = %s", f"rec_{tag}") == 1


def test_create_draft_refuses_a_member_of_a_service_the_release_does_not_list(connect):
    worker = connect("worker")
    tag = new_tag()
    s1, s2 = f"svc_{tag}_1", f"svc_{tag}_2"
    other = store.create_draft(worker, make_draft(tag, [entry(s2)], [new_version(s2, f"rec_{tag}_2", "other")],
                                                  note="setup"))
    reused = member_versions(worker, other)
    with pytest.raises(store.StoreError, match=s2):
        store.create_draft(worker, make_draft(tag, [entry(s1)], [new_version(s1, f"rec_{tag}_1", "mine")], reused,
                                              note=f"unlisted {tag}"))
    nothing_left_behind(worker, note=f"unlisted {tag}", service_ids=[s1], record_ids=[f"rec_{tag}_1"])


# --- set_gate_result ---------------------------------------------------------------------------


def test_set_gate_result_judges_a_draft_once(connect):
    worker = connect("worker")
    tag = new_tag()
    report = {"verdict": "validated", "services": {"x": {"PASS": 3}}, "failures": []}

    def draft_release(suffix):
        sid = f"svc_{tag}_{suffix}"
        version = new_version(sid, f"rec_{tag}_{suffix}", suffix)
        return store.create_draft(worker, make_draft(tag, [entry(sid)], [version]))

    verdict = "SELECT status, gate_report, override_reason FROM releases WHERE release_id = %s"
    accepted = draft_release("a")
    store.set_gate_result(worker, accepted, "validated", report, None)
    assert worker.execute(verdict, (accepted,)).fetchone() == ("validated", report, None)
    with pytest.raises(store.StoreError, match="frozen"):  # judged once, then frozen
        store.set_gate_result(worker, accepted, "rejected", {}, None)
    assert worker.execute(verdict, (accepted,)).fetchone() == ("validated", report, None)

    overridden = draft_release("b")
    store.set_gate_result(worker, overridden, "validated", report, "owner accepts the Banglish failures")
    assert worker.execute(verdict, (overridden,)).fetchone() == (
        "validated", report, "owner accepts the Banglish failures")

    rejected = draft_release("c")
    store.set_gate_result(worker, rejected, "rejected", report, None)
    assert worker.execute(verdict, (rejected,)).fetchone() == ("rejected", report, None)

    with pytest.raises(store.StoreError, match=f"release {UNKNOWN_ID} does not exist"):
        store.set_gate_result(worker, UNKNOWN_ID, "validated", report, None)


def test_a_draft_can_be_judged_right_after_it_is_created_on_the_same_connection(connect):
    """Separate transactions (see `set_gate_result`): creating and judging back to back never deadlocks."""
    worker = connect("worker")
    tag = new_tag()
    for _ in range(3):
        validated_release(worker, tag, new_tag())


# --- existing_versions / existing_vectors ------------------------------------------------------


def test_existing_versions_and_vectors_are_found_by_hash_and_model(connect):
    worker = connect("worker")
    tag = new_tag()
    sid = f"svc_{tag}"
    versions = [new_version(sid, f"rec_{tag}_{i}", f"text {i} of {tag}") for i in range(2)]
    release_id = store.create_draft(worker, make_draft(tag, [entry(sid)], versions))
    stored = member_versions(worker, release_id)
    v0, v1 = versions

    keys = [(v0.record_id, v0.content_hash), (v1.record_id, v1.content_hash)]
    assert store.existing_versions(worker, keys, MODEL) == stored
    assert store.existing_versions(worker, iter(keys), MODEL) == stored        # any iterable
    assert store.existing_versions(worker, keys[:1], MODEL) == {v0.record_id: stored[v0.record_id]}
    assert store.existing_versions(worker, [(v1.record_id, v0.content_hash)], MODEL) == {}  # hash of another record
    assert store.existing_versions(worker, [(v0.record_id, "0" * 64)], MODEL) == {}          # edited content
    assert store.existing_versions(worker, [("rec_unknown_" + tag, v0.content_hash)], MODEL) == {}
    assert store.existing_versions(worker, keys, "another-model") == {}                      # other model
    assert store.existing_versions(worker, [], MODEL) == {}
    with pytest.raises(ValueError, match=v0.record_id):  # one record, two contents: ambiguous, not "last wins"
        store.existing_versions(worker, [(v0.record_id, v0.content_hash), (v0.record_id, v1.content_hash)], MODEL)

    thashes = [v0.text_hash, v1.text_hash, "0" * 64]
    vectors = store.existing_vectors(worker, thashes, MODEL)
    assert set(vectors) == {v0.text_hash, v1.text_hash}
    assert vectors[v0.text_hash] == pytest.approx(v0.embedding, abs=1e-7)
    assert vectors[v1.text_hash] == pytest.approx(v1.embedding, abs=1e-7)
    assert store.existing_vectors(worker, iter(thashes), MODEL) == vectors
    assert store.existing_vectors(worker, thashes, "another-model") == {}
    assert store.existing_vectors(worker, [], MODEL) == {}

    # Same text under another model is a different vector: each model finds its own.
    other = [0.03125] * 1024  # exactly representable in float32
    worker.execute(
        "INSERT INTO record_versions (record_id, content_hash, text_hash, hash_version, record, embed_text, "
        "source_url, url_kind, source_verified, embedding, embedding_model, source_commit, created_by) "
        "VALUES (%s, %s, %s, 1, '{}'::jsonb, 'x', 'https://example.test/', 'info', true, %s::vector, "
        "'another-model', %s, 'itest-store')",
        (v0.record_id, v0.content_hash, v0.text_hash, store.vector_to_text(other), COMMIT),
    )
    assert store.existing_vectors(worker, [v0.text_hash], "another-model") == {v0.text_hash: other}
    assert store.existing_vectors(worker, [v0.text_hash], MODEL)[v0.text_hash] == pytest.approx(v0.embedding, abs=1e-7)
    assert store.existing_versions(worker, keys[:1], "another-model").keys() == {v0.record_id}
    assert store.existing_versions(worker, keys[:1], MODEL) == {v0.record_id: stored[v0.record_id]}


# --- search ------------------------------------------------------------------------------------


def test_search_orders_by_distance_then_record_id(connect):
    worker = connect("worker")
    tag = new_tag()
    sid = f"svc_{tag}"
    same = f"identical text {tag}"
    # Three records share one text (so one vector, so one similarity). They are inserted in the
    # opposite order of their ids, so physical or version order cannot produce the right answer.
    versions = [new_version(sid, f"rec_{tag}_z", same), new_version(sid, f"rec_{tag}_a", same),
                new_version(sid, f"rec_{tag}_m", same),
                new_version(sid, f"rec_{tag}_far", f"something else entirely {tag}")]
    release_id = store.create_draft(worker, make_draft(tag, [entry(sid)], versions))

    hits = store.search(worker, release_id, sid, EMBEDDER.encode([same])[0], 10)

    assert [h.record_id for h in hits] == [f"rec_{tag}_{s}" for s in ("a", "m", "z", "far")]
    assert hits[0].sim == hits[1].sim == hits[2].sim          # exactly tied: record_id decides
    assert hits[0].sim == pytest.approx(1.0, abs=1e-6)        # the query is a record's own text
    assert hits[3].sim < 0.5
    first = hits[0]
    assert isinstance(first, store.Hit) and isinstance(first.sim, float)
    assert (first.service_id, first.text, first.category, first.subcategory, first.heading, first.source_url) == (
        sid, same, "process_steps", "sub", f"Heading rec_{tag}_a", f"https://example.test/rec_{tag}_a")


def test_search_similarity_is_one_minus_cosine_distance_and_returns_at_most_k(connect):
    worker = connect("worker")
    tag = new_tag()
    sid = f"svc_{tag}"
    # Cosine similarity to the query basis(1) is the first component. Two records tie at 0.6.
    spec = [("z60", basis(0.6, 0.8)), ("opposite", basis(-1.0)), ("c80", basis(0.8, 0.6)), ("perfect", basis(1.0)),
            ("a60", basis(0.6, 0.8)), ("zero", basis(0.0, 1.0))]
    versions = [new_version(sid, f"rec_{tag}_{name}", f"text {name} {tag}", embedding=vector) for name, vector in spec]
    release_id = store.create_draft(worker, make_draft(tag, [entry(sid)], versions))
    query = basis(1.0)

    hits = store.search(worker, release_id, sid, query, 10)
    names = [h.record_id.removeprefix(f"rec_{tag}_") for h in hits]
    assert names == ["perfect", "c80", "a60", "z60", "zero", "opposite"]
    assert [h.sim for h in hits] == pytest.approx([1.0, 0.8, 0.6, 0.6, 0.0, -1.0], abs=1e-6)
    expected = worker.execute(  # sim is exactly 1 - (embedding <=> query), as PostgreSQL computes it
        "SELECT 1 - (embedding <=> %s::vector) FROM record_versions WHERE record_id = %s",
        (store.vector_to_text(query), f"rec_{tag}_c80"),
    ).fetchone()[0]
    assert hits[1].sim == expected

    for k, size in ((1, 1), (2, 2), (3, 3), (5, 5), (6, 6), (7, 6), (100, 6), (0, 0)):
        limited = store.search(worker, release_id, sid, query, k)
        assert len(limited) == size, k
        assert limited == hits[:size], k             # the best k, in the same order
    with pytest.raises(ValueError, match="k"):
        store.search(worker, release_id, sid, query, -1)


def test_search_is_limited_to_the_release_and_service(connect):
    worker = connect("worker")
    tag = new_tag()
    s1, s2 = f"svc_{tag}_1", f"svc_{tag}_2"
    a1, a2, a3, b1 = (f"rec_{tag}_{n}" for n in ("a1", "a2", "a3", "b1"))
    first = store.create_draft(worker, make_draft(tag, [entry(s1), entry(s2)], [
        new_version(s1, a1, f"a1 original {tag}"), new_version(s1, a2, f"a2 {tag}"), new_version(s2, b1, f"b1 {tag}"),
    ], note="first"))
    # Release 2 holds an edited a1 (a second version of the same record) and a new a3, no a2, no s2.
    second = store.create_draft(worker, make_draft(tag, [entry(s1)], [
        new_version(s1, a1, f"a1 edited {tag}", edited=True), new_version(s1, a3, f"a3 {tag}"),
    ], note="second"))
    # A record with a version that no release contains.
    add_record(worker, s1, f"rec_{tag}_orphan", text=f"orphan {tag}")
    query = EMBEDDER.encode([f"a1 original {tag}"])[0]

    def found(release_id, service_id):
        return {h.record_id: h.text for h in store.search(worker, release_id, service_id, query, 50)}

    assert found(first, s1) == {a1: f"a1 original {tag}", a2: f"a2 {tag}"}      # not a3, not b1, not the orphan
    assert found(first, s2) == {b1: f"b1 {tag}"}
    assert found(second, s1) == {a1: f"a1 edited {tag}", a3: f"a3 {tag}"}        # the edited version, not the old one
    assert found(second, s2) == {}                                              # s2 is not in release 2
    assert found(UNKNOWN_ID, s1) == {}
    assert found(first, f"svc_{tag}_nope") == {}
    assert all(h.service_id == s1 for h in store.search(worker, first, s1, query, 50))


# --- publish / rollback / audit ----------------------------------------------------------------


def test_publish_rollback_wrappers_raise_storeerror_with_the_db_message(connect):
    worker = connect("worker")
    tag = new_tag()
    actor = new_actor()
    first = validated_release(worker, tag, "one")
    second = validated_release(worker, tag, "two")
    unjudged_sid = f"svc_{tag}_draft"
    unjudged = new_version(unjudged_sid, f"rec_{tag}_d", "d")
    draft = store.create_draft(worker, make_draft(tag, [entry(unjudged_sid)], [unjudged]))

    with pytest.raises(store.StoreError) as unknown:
        store.publish(worker, UNKNOWN_ID, actor)
    assert str(unknown.value) == f"release {UNKNOWN_ID} does not exist"
    with pytest.raises(store.StoreError) as unjudged:
        store.publish(worker, draft, actor)
    assert str(unjudged.value) == f"release {draft} is not validated"
    with pytest.raises(store.StoreError) as blank:
        store.publish(worker, first, "  ")
    assert str(blank.value) == "an actor is required"
    with pytest.raises(store.StoreError) as blank_rollback:
        store.rollback(worker, "")
    assert str(blank_rollback.value) == "an actor is required"
    assert count(worker, "SELECT 1") == 1  # refusals leave the connection usable

    store.publish(worker, first, actor)
    assert store.active_release_id(worker) == first
    store.publish(worker, second, actor)
    assert store.active_release_id(worker) == second
    with pytest.raises(store.StoreError) as again:
        store.publish(worker, second, actor)
    assert str(again.value) == f"release {second} is already active"

    assert store.rollback(worker, actor) == first
    assert store.active_release_id(worker) == first

    assert [row[:2] for row in audit_rows(connect("web"), actor)] == [
        ("release.publish", f"release:{first}"),
        ("release.publish", f"release:{second}"),
        ("release.rollback", f"release:{first}"),
    ]
    listed = {row["release_id"]: row for row in store.list_releases(worker)}
    assert [rid for rid, row in listed.items() if row["is_active"]] == [first]
    assert store.release_row(worker, first)["is_active"] is True
    assert store.release_row(worker, second)["is_active"] is False


def test_audit_writes_one_row_with_the_given_details(connect):
    worker = connect("worker")
    actor = new_actor()
    store.audit(worker, actor, "release.build", "release:42", {"commit": COMMIT, "embedded": 3, "note": "বাংলা"})
    store.audit(worker, actor, "release.validate", "release:42", {})
    assert audit_rows(connect("web"), actor) == [
        ("release.build", "release:42", {"commit": COMMIT, "embedded": 3, "note": "বাংলা"}),
        ("release.validate", "release:42", {}),
    ]


# --- the read helpers --------------------------------------------------------------------------


def test_read_helpers_describe_a_release(connect):
    worker = connect("worker")
    tag = new_tag()
    live, hidden = f"svc_{tag}_live", f"svc_{tag}_hidden"
    versions = [new_version(live, f"rec_{tag}_l1", "live one"), new_version(live, f"rec_{tag}_l2", "live two"),
                new_version(hidden, f"rec_{tag}_h1", "hidden one")]
    draft = make_draft(tag, [entry(hidden, "hidden"), entry(live)], versions)
    release_id = store.create_draft(worker, draft)

    row = store.release_row(worker, release_id)
    assert row["release_id"] == release_id and row["status"] == "draft" and row["is_active"] is False
    assert (row["source_commit"], row["embedding_model"], row["note"], row["created_by"]) == (
        COMMIT, MODEL, f"store test {tag}", "itest-store")
    assert row["manifest"] == draft.manifest_json and row["registry"] == draft.registry
    assert row["registry_sha256"] == "f" * 64 and row["thresholds"] == draft.thresholds_json
    assert row["gate_report"] is None and row["override_reason"] is None and row["created_at"] is not None
    assert store.release_row(worker, UNKNOWN_ID) is None

    listing = store.list_releases(worker)
    assert [r["release_id"] for r in listing] == sorted(r["release_id"] for r in listing)
    entry_for_release = next(r for r in listing if r["release_id"] == release_id)
    assert set(entry_for_release) == {"release_id", "status", "source_commit", "created_at", "embedding_model",
                                      "is_active"}
    assert entry_for_release["status"] == "draft" and entry_for_release["source_commit"] == COMMIT
    assert entry_for_release["embedding_model"] == MODEL and entry_for_release["is_active"] is False

    assert store.member_hashes(worker, release_id) == {v.record_id: v.content_hash for v in versions}
    assert store.live_services(worker, release_id) == [live]                     # hidden services are not live
    assert store.live_services(worker, UNKNOWN_ID) == []
    vectors = store.stored_vectors(worker, release_id)
    assert set(vectors) == {v.record_id for v in versions}
    for v in versions:
        assert vectors[v.record_id] == pytest.approx(v.embedding, abs=1e-7)
    assert store.stored_vectors(worker, UNKNOWN_ID) == {}
    assert store.member_hashes(worker, UNKNOWN_ID) == {}

    services_of_records = store.stored_record_services(worker)
    assert {v.record_id: services_of_records[v.record_id] for v in versions} == {v.record_id: v.service_id
                                                                                  for v in versions}
    names = store.stored_service_names(worker)
    assert names[live] == (f"সেবা {live}", f"Service {live}")
    assert names[hidden] == (f"সেবা {hidden}", f"Service {hidden}")
    active = store.active_release_id(worker)
    assert active is None or isinstance(active, int)
