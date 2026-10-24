"""Release content tables, state rules, publish/rollback and engine views (migration 0002).

Everything is checked as the real roles (spec sections 3, 3.1 and 6). The stack tests share one
database across runs, so every test makes its own service and record ids (a random suffix) and
never assumes an empty ``active_release``; the one state that only a fresh database has lives in
``test_00_first_publication.py``. The shared helpers are in ``release_helpers.py``.
"""
import threading
from datetime import date

import pytest
from psycopg.errors import (
    CheckViolation,
    DataException,
    ForeignKeyViolation,
    GeneratedAlways,
    InsufficientPrivilege,
    NotNullViolation,
    RaiseException,
    UniqueViolation,
)
from psycopg.types.json import Jsonb
from release_helpers import (
    COMMIT,
    DIM,
    MODEL,
    UNIT_VECTOR,
    active_release_id,
    add_record,
    add_release,
    add_version,
    audit_rows,
    last_publication_id,
    make_draft_release,
    make_validated_release,
    new_actor,
    new_tag,
    publications_after,
    publish,
    rollback,
    set_status,
    sha256_hex,
    wait_until_a_statement_waits_for_a_lock,
)


# --- the worker builds a release ---------------------------------------------------------------


def test_worker_can_build_a_draft_and_validate_it(connect):
    worker = connect("worker")
    built = make_draft_release(connect)
    row = worker.execute(
        "SELECT status, gate_report, override_reason, embedding_model, source_commit, created_by "
        "FROM releases WHERE release_id = %s",
        (built.release_id,),
    ).fetchone()
    assert row == ("draft", None, None, MODEL, COMMIT, "itest")
    members = worker.execute(
        "SELECT record_id FROM release_members WHERE release_id = %s ORDER BY record_id", (built.release_id,)
    ).fetchall()
    assert [m[0] for m in members] == sorted(built.record_ids)
    dims, distance = worker.execute(
        "SELECT vector_dims(embedding), embedding <=> %s::vector FROM record_versions WHERE version_id = %s",
        (UNIT_VECTOR, built.version_ids[0]),
    ).fetchone()
    assert dims == DIM
    assert distance == pytest.approx(0.0, abs=1e-6)  # the stored vector is the one we sent

    worker.execute(
        "UPDATE releases SET status = 'validated', gate_report = %s, override_reason = %s WHERE release_id = %s",
        (Jsonb({"ok": True}), "a reason", built.release_id),
    )
    row = worker.execute(
        "SELECT status, gate_report, override_reason FROM releases WHERE release_id = %s", (built.release_id,)
    ).fetchone()
    assert row == ("validated", {"ok": True}, "a reason")


@pytest.mark.parametrize(
    "statement, params, error",
    [
        # services.service_id: lower-case letters, digits and underscores, starting with a letter
        ("INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, 'b', 'e')", ("Bad-ID",), CheckViolation),
        ("INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, 'b', 'e')", ("1abc",), CheckViolation),
        ("INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, 'b', 'e')", (None,), NotNullViolation),
        # releases.source_commit is a full lower-case SHA-1
        (
            "INSERT INTO releases (source_commit, manifest, registry, registry_sha256, thresholds, "
            "embedding_model, created_by) VALUES (%s, '{}', '{}', 'x', '{}', 'm', 'itest')",
            ("main",),
            CheckViolation,
        ),
    ],
)
def test_table_constraints_reject_bad_values(connect, statement, params, error):
    with pytest.raises(error):
        connect("worker").execute(statement, params)


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"source_commit": "ABCDEF0123456789abcdef0123456789abcdef01"}, CheckViolation),  # upper-case hex
        ({"source_commit": "0123456789abcdef"}, CheckViolation),  # not 40 characters
        ({"url_kind": "other"}, CheckViolation),
        ({"embedding": "[" + ",".join(["0.5"] * 3) + "]"}, DataException),  # wrong dimension
        ({"embedding": None}, NotNullViolation),
        ({"model": None}, NotNullViolation),
    ],
)
def test_record_version_constraints_reject_bad_values(connect, overrides, error):
    worker = connect("worker")
    tag = new_tag()
    service_id = f"svc_{tag}"
    worker.execute("INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, 'b', 'e')", (service_id,))
    worker.execute("INSERT INTO records (record_id, service_id) VALUES (%s, %s)", (f"rec_{tag}", service_id))
    with pytest.raises(error):
        add_version(worker, f"rec_{tag}", text=f"text {tag}", **overrides)


def test_a_version_is_unique_per_record_content_derived_columns_and_model(connect):
    worker = connect("worker")
    built = make_draft_release(connect, n_records=1)
    record_id = built.record_ids[0]
    same_hash = sha256_hex(f"text 0 {built.tag}")
    with pytest.raises(UniqueViolation):  # same (record, content_hash, fields_hash, model) as the first version
        add_version(worker, record_id, text="other text", content_hash=same_hash)
    # a different model, a different content hash or different derived columns is a different version
    add_version(worker, record_id, text="other text", content_hash=same_hash, model="fake-other-model")
    add_version(worker, record_id, text="other text 2")
    add_version(worker, record_id, text="other text 3", content_hash=same_hash, fields_hash=sha256_hex("derived"))
    with pytest.raises(UniqueViolation):  # ... but only once
        add_version(worker, record_id, text="other text 4", content_hash=same_hash, fields_hash=sha256_hex("derived"))


def test_a_version_needs_a_fields_hash(connect):
    worker = connect("worker")
    built = make_draft_release(connect, n_records=1)
    with pytest.raises(NotNullViolation):
        worker.execute(
            "INSERT INTO record_versions (record_id, content_hash, text_hash, hash_version, record, embed_text, "
            "source_url, url_kind, source_verified, embedding, embedding_model, source_commit, created_by) "
            "VALUES (%s, 'c', 't', 1, '{}'::jsonb, 'x', 'https://example.test/', 'info', true, %s::vector, "
            "'fake-sha256-v1', %s, 'itest')",
            (built.record_ids[0], UNIT_VECTOR, COMMIT),
        )


# --- append-only and frozen rules ---------------------------------------------------------------

APPEND_ONLY = {
    "services": "UPDATE services SET name_en = 'tampered'",
    "records": "UPDATE records SET created_at = now()",
    "record_versions": "UPDATE record_versions SET change_note = 'tampered'",
    "publications": "UPDATE publications SET actor = 'tampered'",
}


@pytest.mark.parametrize("table", sorted(APPEND_ONLY))
def test_versions_services_records_publications_are_append_only(connect, table):
    built = make_validated_release(connect)
    publish(connect("worker"), built.release_id, new_actor())  # makes sure `publications` has a row
    update = APPEND_ONLY[table]
    migrate = connect("migrate")
    before = migrate.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    assert before > 0

    # The worker has no UPDATE, DELETE or TRUNCATE privilege on these tables at all ...
    worker = connect("worker")
    for statement in (update, f"DELETE FROM {table}", f"TRUNCATE {table}"):
        with pytest.raises(InsufficientPrivilege):
            worker.execute(statement)

    # ... and the owner, who has every privilege, is stopped by the guard triggers. (CASCADE only
    # lets TRUNCATE get past PostgreSQL's foreign-key check to the trigger, which fires first for
    # the table named here and refuses.)
    message = f"{table} is append-only"
    for statement in (update, f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"):
        with pytest.raises(RaiseException, match=message):
            migrate.execute(statement)
    assert migrate.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == before


def test_release_status_moves_once_out_of_draft(connect):
    worker, migrate = connect("worker"), connect("migrate")
    built = make_draft_release(connect)
    rid = built.release_id
    frozen = f"release {rid} is frozen"

    # A draft may only leave draft, and only status, gate_report and override_reason may change.
    with pytest.raises(RaiseException, match=frozen):  # gate report written but status still draft
        worker.execute("UPDATE releases SET gate_report = '{}' WHERE release_id = %s", (rid,))
    with pytest.raises(RaiseException, match=frozen):
        migrate.execute(
            "UPDATE releases SET status = 'validated', source_commit = %s WHERE release_id = %s", ("1" * 40, rid)
        )
    with pytest.raises(RaiseException, match=frozen):
        migrate.execute("UPDATE releases SET status = 'validated', note = 'edited' WHERE release_id = %s", (rid,))
    with pytest.raises(InsufficientPrivilege):  # the worker cannot even try to change other columns
        worker.execute("UPDATE releases SET source_commit = %s WHERE release_id = %s", ("1" * 40, rid))

    set_status(worker, rid, "validated", override_reason="because")  # draft -> validated: fine

    # Once validated nothing changes: not the status, not the gate report, not any other column.
    with pytest.raises(RaiseException, match=frozen):
        set_status(worker, rid, "rejected")
    with pytest.raises(RaiseException, match=frozen):
        worker.execute("UPDATE releases SET gate_report = '{}' WHERE release_id = %s", (rid,))
    with pytest.raises(RaiseException, match=frozen):
        migrate.execute("UPDATE releases SET source_commit = %s WHERE release_id = %s", ("1" * 40, rid))
    with pytest.raises(RaiseException, match=frozen):
        migrate.execute("UPDATE releases SET status = 'draft' WHERE release_id = %s", (rid,))
    with pytest.raises(RaiseException, match=frozen):
        migrate.execute("DELETE FROM releases WHERE release_id = %s", (rid,))
    with pytest.raises(InsufficientPrivilege):
        worker.execute("DELETE FROM releases WHERE release_id = %s", (rid,))
    assert worker.execute(
        "SELECT status, override_reason FROM releases WHERE release_id = %s", (rid,)
    ).fetchone() == ("validated", "because")

    # A rejected release is frozen too (and cannot be validated afterwards).
    rejected = make_draft_release(connect)
    set_status(worker, rejected.release_id, "rejected")
    with pytest.raises(RaiseException, match=f"release {rejected.release_id} is frozen"):
        set_status(worker, rejected.release_id, "validated")


def test_releases_are_never_deleted_or_truncated_even_while_draft(connect):
    built = make_draft_release(connect)
    migrate = connect("migrate")
    with pytest.raises(RaiseException, match=f"release {built.release_id} is frozen"):
        migrate.execute("DELETE FROM releases WHERE release_id = %s", (built.release_id,))
    with pytest.raises(RaiseException, match="is frozen"):
        migrate.execute("TRUNCATE releases CASCADE")
    assert migrate.execute(
        "SELECT status FROM releases WHERE release_id = %s", (built.release_id,)
    ).fetchone() == ("draft",)


def test_a_release_can_only_be_created_as_a_draft(connect):
    worker = connect("worker")
    with pytest.raises(RaiseException, match="a release must be created as a draft"):  # the status branch
        worker.execute(
            "INSERT INTO releases (status, source_commit, manifest, registry, registry_sha256, thresholds, "
            "embedding_model, created_by) VALUES ('validated', %s, '{}', '{}', 'x', '{}', 'm', 'itest')",
            (COMMIT,),
        )


@pytest.mark.parametrize(
    "column, value",
    [("gate_report", Jsonb({"ok": True})), ("override_reason", "pre-approved")],
)
def test_a_release_is_created_without_a_verdict(connect, column, value):
    # The verdict (gate_report, override_reason) is written once, by the draft -> validated/rejected
    # change; a release cannot be created already carrying one. The status here is the default (draft),
    # so only the verdict branch of releases_guard() can refuse; "draft" alone would also match the
    # message of the status branch ("a release must be created as a draft (not ...)").
    with pytest.raises(RaiseException, match="without gate_report or override_reason"):
        connect("worker").execute(
            "INSERT INTO releases (source_commit, manifest, registry, registry_sha256, thresholds, "
            f"embedding_model, created_by, {column}) VALUES (%s, '{{}}', '{{}}', 'x', '{{}}', 'm', 'itest', %s)",
            (COMMIT, value),
        )


def test_a_draft_cannot_be_given_a_null_status(connect):
    # The guard itself refuses it (not only the NOT NULL constraint that would answer later).
    built = make_draft_release(connect)
    with pytest.raises(RaiseException, match=f"release {built.release_id} is frozen"):
        connect("worker").execute("UPDATE releases SET status = NULL WHERE release_id = %s", (built.release_id,))
    with pytest.raises(RaiseException, match=f"release {built.release_id} is frozen"):
        connect("migrate").execute("UPDATE releases SET status = NULL WHERE release_id = %s", (built.release_id,))


FORBIDDEN_INSERTS = [
    # created_at always comes from its default (now()): no backdating
    ("INSERT INTO records (record_id, service_id, created_at) VALUES ('r', 's', '2001-01-01')", InsufficientPrivilege),
    ("INSERT INTO record_versions (record_id, created_at) VALUES ('r', '2001-01-01')", InsufficientPrivilege),
    ("INSERT INTO releases (source_commit, created_at) VALUES ('c', '2001-01-01')", InsufficientPrivilege),
    # identity values always come from the sequence: without OVERRIDING SYSTEM VALUE the column
    # refuses an explicit value, with it the missing privilege does
    ("INSERT INTO record_versions (version_id, record_id) VALUES (1, 'r')", GeneratedAlways),
    ("INSERT INTO record_versions (version_id, record_id) OVERRIDING SYSTEM VALUE VALUES (1, 'r')",
     InsufficientPrivilege),
    ("INSERT INTO releases (release_id, source_commit) VALUES (1, 'c')", GeneratedAlways),
    ("INSERT INTO releases (release_id, source_commit) OVERRIDING SYSTEM VALUE VALUES (1, 'c')",
     InsufficientPrivilege),
]


@pytest.mark.parametrize("statement, error", FORBIDDEN_INSERTS)
def test_worker_cannot_choose_identity_values_or_timestamps(connect, statement, error):
    with pytest.raises(error):
        connect("worker").execute(statement)


def test_members_frozen_after_draft(connect):
    worker, migrate = connect("worker"), connect("migrate")
    built = make_draft_release(connect)
    rid = built.release_id
    # a third record that is not (yet) a member
    extra_record, extra_version = add_record(worker, built.service_id, f"rec_{built.tag}_x", text=f"x {built.tag}")

    # While the release is a draft the worker may remove and add members ...
    worker.execute(
        "DELETE FROM release_members WHERE release_id = %s AND record_id = %s", (rid, built.record_ids[0])
    )
    worker.execute(
        "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
        (rid, extra_version, extra_record),
    )
    # ... but members are never updated, even by the owner
    with pytest.raises(RaiseException, match="release_members"):
        migrate.execute("UPDATE release_members SET record_id = record_id WHERE release_id = %s", (rid,))

    set_status(worker, rid, "validated")

    # After that, nothing can be added or removed, and the same holds for the service list.
    other_record, other_version = add_record(worker, built.service_id, f"rec_{built.tag}_y", text=f"y {built.tag}")
    frozen = f"release {rid} is frozen"
    with pytest.raises(RaiseException, match=frozen):
        worker.execute(
            "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
            (rid, other_version, other_record),
        )
    with pytest.raises(RaiseException, match=frozen):
        worker.execute("DELETE FROM release_members WHERE release_id = %s AND record_id = %s", (rid, extra_record))
    with pytest.raises(RaiseException, match=frozen):
        migrate.execute("DELETE FROM release_members WHERE release_id = %s", (rid,))
    other_service = f"svc_{new_tag()}"
    worker.execute("INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, 'b', 'e')", (other_service,))
    with pytest.raises(RaiseException, match=frozen):
        worker.execute(
            "INSERT INTO release_services (release_id, service_id, status) VALUES (%s, %s, 'live')",
            (rid, other_service),
        )
    with pytest.raises(RaiseException, match=frozen):
        migrate.execute("DELETE FROM release_services WHERE release_id = %s", (rid,))
    with pytest.raises(RaiseException, match="release_services"):
        migrate.execute("UPDATE release_services SET status = 'hidden' WHERE release_id = %s", (rid,))
    with pytest.raises(RaiseException, match="release_members is frozen"):
        migrate.execute("TRUNCATE release_members")
    with pytest.raises(RaiseException, match="release_services is frozen"):
        migrate.execute("TRUNCATE release_services")
    # nothing changed
    assert worker.execute(
        "SELECT count(*) FROM release_members WHERE release_id = %s", (rid,)
    ).fetchone()[0] == len(built.record_ids)  # one removed, one added
    # The worker cannot delete service rows at all (no DELETE privilege on release_services).
    with pytest.raises(InsufficientPrivilege):
        worker.execute("DELETE FROM release_services WHERE release_id = %s", (rid,))


def test_one_version_per_record_per_release(connect):
    worker = connect("worker")
    built = make_draft_release(connect)
    record_id = built.record_ids[0]
    second = add_version(worker, record_id, text="a later wording of the same record")
    with pytest.raises(UniqueViolation):
        worker.execute(
            "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
            (built.release_id, second, record_id),
        )
    # a member's version must belong to the member's record
    z_record, z_version = add_record(worker, built.service_id, f"rec_{built.tag}_z", text=f"z {built.tag}")
    y_record, _ = add_record(worker, built.service_id, f"rec_{built.tag}_y", text=f"y {built.tag}")
    with pytest.raises(ForeignKeyViolation):  # a version of record z filed under record y
        worker.execute(
            "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
            (built.release_id, z_version, y_record),
        )
    with pytest.raises(ForeignKeyViolation):  # a record that does not exist
        worker.execute(
            "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
            (built.release_id, z_version, f"rec_{built.tag}_nope"),
        )
    # and the same version may sit in many releases
    other_release = make_draft_release(connect, n_records=1)
    worker.execute(
        "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
        (other_release.release_id, built.version_ids[0], built.record_ids[0]),
    )


# --- publish and rollback ------------------------------------------------------------------------


def test_publish_and_rollback_write_switch_publication_and_audit(connect):
    worker, web = connect("worker"), connect("web")
    r1, r2 = make_validated_release(connect), make_validated_release(connect)
    before_active = active_release_id(worker)
    before_publication = last_publication_id(worker)
    actor = new_actor()

    publish(worker, r1.release_id, actor)
    assert active_release_id(worker) == r1.release_id
    publish(worker, r2.release_id, actor)
    assert active_release_id(worker) == r2.release_id
    assert rollback(worker, actor) == r1.release_id  # returns the release that is active again
    assert active_release_id(worker) == r1.release_id

    assert publications_after(worker, before_publication) == [
        (r1.release_id, before_active, "publish", actor),
        (r2.release_id, r1.release_id, "publish", actor),
        (r1.release_id, r2.release_id, "rollback", actor),
    ]
    # One audit row per action, read back through the web role (the worker cannot read audit_log).
    assert audit_rows(web, actor) == [
        ("release.publish", f"release:{r1.release_id}", {"previous": before_active}),
        ("release.publish", f"release:{r2.release_id}", {"previous": r1.release_id}),
        ("release.rollback", f"release:{r1.release_id}", {"previous": r2.release_id}),
    ]
    switched = worker.execute("SELECT singleton, switched_at > now() - interval '1 minute' FROM active_release")
    assert switched.fetchall() == [(True, True)]


def test_second_rollback_reapplies_the_undone_release(connect):
    worker, web = connect("worker"), connect("web")
    r1, r2 = make_validated_release(connect), make_validated_release(connect)
    before_publication = last_publication_id(worker)
    actor = new_actor()
    publish(worker, r1.release_id, actor)
    publish(worker, r2.release_id, actor)

    assert rollback(worker, actor) == r1.release_id
    assert rollback(worker, actor) == r2.release_id  # undoes the rollback
    assert active_release_id(worker) == r2.release_id
    assert rollback(worker, actor) == r1.release_id  # and it flips back again
    assert active_release_id(worker) == r1.release_id

    assert [row[:3] for row in publications_after(worker, before_publication)][2:] == [
        (r1.release_id, r2.release_id, "rollback"),
        (r2.release_id, r1.release_id, "rollback"),
        (r1.release_id, r2.release_id, "rollback"),
    ]
    assert [row[0] for row in audit_rows(web, actor)] == ["release.publish"] * 2 + ["release.rollback"] * 3


def test_publish_refuses_unvalidated_and_already_active(connect):
    worker, web = connect("worker"), connect("web")
    draft = make_draft_release(connect)
    rejected = make_draft_release(connect)
    set_status(worker, rejected.release_id, "rejected")
    validated = make_validated_release(connect)
    publish(worker, validated.release_id, new_actor())
    before_publication = last_publication_id(worker)
    before_active = active_release_id(worker)
    actor = new_actor()

    for release in (draft, rejected):
        with pytest.raises(RaiseException, match=f"release {release.release_id} is not validated"):
            publish(worker, release.release_id, actor)
    with pytest.raises(RaiseException, match=f"release {validated.release_id} is already active"):
        publish(worker, validated.release_id, actor)
    with pytest.raises(RaiseException, match="does not exist"):
        publish(worker, 2**62, actor)
    for bad_actor in (None, "", "   "):
        with pytest.raises(RaiseException, match="actor"):
            publish(worker, draft.release_id, bad_actor)
        with pytest.raises(RaiseException, match="actor"):
            rollback(worker, bad_actor)

    # every refusal rolled back completely: no switch, no publication, no audit row
    assert active_release_id(worker) == before_active == validated.release_id
    assert publications_after(worker, before_publication) == []
    assert audit_rows(web, actor) == []


def test_worker_cannot_write_active_release_or_publications_directly(connect):
    built = make_validated_release(connect)
    other = make_validated_release(connect)
    publish(connect("worker"), built.release_id, new_actor())
    for role in ("worker", "web", "engine"):
        conn = connect(role)
        for statement in (
            f"INSERT INTO active_release (release_id) VALUES ({other.release_id})",
            f"UPDATE active_release SET release_id = {other.release_id}",
            "DELETE FROM active_release",
            "TRUNCATE active_release",
            f"INSERT INTO publications (release_id, action, actor) VALUES ({other.release_id}, 'publish', 'x')",
            "UPDATE publications SET actor = 'x'",
            "DELETE FROM publications",
            "TRUNCATE publications",
        ):
            with pytest.raises(InsufficientPrivilege):
                conn.execute(statement)
    assert active_release_id(connect("worker")) == built.release_id


def test_active_release_must_be_a_validated_release_and_is_never_removed(connect):
    # Even the owner, who may write the table, cannot point it at an unvalidated release or remove
    # the row: the guard triggers allow only what publish_release / rollback_release do.
    migrate = connect("migrate")
    published = make_validated_release(connect)
    publish(connect("worker"), published.release_id, new_actor())
    draft = make_draft_release(connect)
    not_validated = f"release {draft.release_id} is not validated"
    with pytest.raises(RaiseException, match=not_validated):
        migrate.execute("UPDATE active_release SET release_id = %s", (draft.release_id,))
    with pytest.raises(RaiseException, match=not_validated):
        migrate.execute("INSERT INTO active_release (release_id) VALUES (%s)", (draft.release_id,))
    with pytest.raises(RaiseException, match="active_release"):
        migrate.execute("DELETE FROM active_release")
    with pytest.raises(RaiseException, match="active_release"):
        migrate.execute("TRUNCATE active_release")
    assert active_release_id(migrate) == published.release_id
    # the table holds at most one row (the primary key is the constant `true`)
    with pytest.raises(CheckViolation):
        migrate.execute("UPDATE active_release SET singleton = false")


def test_concurrent_publishes_are_serialised(connect):
    # publish_release locks active_release first, so a second publish waits for the first to commit
    # and then records the first one as its "previous" release (instead of both seeing the old one).
    r1, r2 = make_validated_release(connect), make_validated_release(connect)
    first, second, watcher = connect("worker"), connect("worker"), connect("worker")
    actor = new_actor()
    before_publication = last_publication_id(watcher)
    outcome = {}

    def second_publish():
        try:
            publish(second, r2.release_id, actor)
            outcome["ok"] = True
        except Exception as exc:  # reported by the main thread
            outcome["error"] = exc

    thread = None
    first.execute("BEGIN")
    try:
        publish(first, r1.release_id, actor)  # holds the lock until COMMIT
        thread = threading.Thread(target=second_publish)
        thread.start()
        waiting = wait_until_a_statement_waits_for_a_lock(watcher, "publish_release")
        assert waiting, "the second publish did not wait for the first one's lock"
        assert thread.is_alive()
    finally:
        first.execute("COMMIT")
        if thread is not None:
            thread.join(timeout=15)
    assert "error" not in outcome, outcome.get("error")
    assert outcome.get("ok")
    rows = publications_after(watcher, before_publication)
    assert [(release, action) for release, _, action, _ in rows] == [
        (r1.release_id, "publish"),
        (r2.release_id, "publish"),
    ]
    assert rows[1][1] == r1.release_id  # the second saw the first as the active release
    assert active_release_id(watcher) == r2.release_id


def test_a_member_cannot_slip_into_a_release_that_is_being_validated(connect):
    # The validator has changed the status but not yet committed. The builder's insert must wait for
    # that transaction and then see the release as validated; a plain read of the status would let
    # the member in, and the frozen release would silently gain a record.
    worker = connect("worker")
    built = make_draft_release(connect)
    extra_record, extra_version = add_record(worker, built.service_id, f"rec_{built.tag}_x", text=f"x {built.tag}")
    validator, builder, watcher = connect("worker"), connect("worker"), connect("worker")
    outcome = {}

    def add_member():
        try:
            builder.execute(
                "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
                (built.release_id, extra_version, extra_record),
            )
            outcome["ok"] = True
        except Exception as exc:  # reported by the main thread
            outcome["error"] = exc

    thread = None
    validator.execute("BEGIN")
    try:
        set_status(validator, built.release_id, "validated")  # not committed yet
        thread = threading.Thread(target=add_member)
        thread.start()
        assert wait_until_a_statement_waits_for_a_lock(watcher, "INSERT INTO release_members"), (
            "the insert did not wait for the status change"
        )
    finally:
        validator.execute("COMMIT")
        if thread is not None:
            thread.join(timeout=15)
    assert "ok" not in outcome
    assert isinstance(outcome.get("error"), RaiseException), outcome
    assert f"release {built.release_id} is frozen" in str(outcome["error"])
    assert watcher.execute(
        "SELECT count(*) FROM release_members WHERE release_id = %s", (built.release_id,)
    ).fetchone()[0] == len(built.record_ids)


# --- what the engine sees ------------------------------------------------------------------------

BASE_TABLES = [
    "services",
    "records",
    "record_versions",
    "releases",
    "release_members",
    "release_services",
    "active_release",
    "publications",
    "audit_log",
]
VIEW_COLUMNS = (
    "version_id, record_id, service_id, embed_text, record, category, subcategory, heading, lang, "
    "source_url, url_kind, official, source_verified, verified_at, embedding_model"
)


def test_engine_sees_only_the_active_release_views(connect):
    worker, engine = connect("worker"), connect("engine")
    r1, r2 = make_validated_release(connect, n_records=3), make_validated_release(connect, n_records=2)
    draft = make_draft_release(connect)  # drafts and unpublished releases are invisible
    actor = new_actor()
    publish(worker, r1.release_id, actor)

    def active_versions(service_id):
        return engine.execute(
            f"SELECT {VIEW_COLUMNS} FROM engine_active_versions WHERE service_id = %s ORDER BY record_id",
            (service_id,),
        ).fetchall()

    release_row = engine.execute("SELECT release_id, registry, registry_sha256, embedding_model FROM engine_active_release")
    assert release_row.fetchall() == [
        (r1.release_id, {"services": {r1.service_id: {"names": ["a", "b"]}}}, "f" * 64, MODEL)
    ]
    services = {row[0]: row[1:] for row in engine.execute("SELECT service_id, name_bn, name_en FROM engine_active_services")}
    assert services[r1.service_id] == (f"সেবা {r1.tag}", f"Service {r1.tag}")
    assert r2.service_id not in services and draft.service_id not in services

    rows = active_versions(r1.service_id)
    assert [r[1] for r in rows] == sorted(r1.record_ids)
    assert dict(zip(r1.record_ids, r1.version_ids)) == {r[1]: r[0] for r in rows}
    first = next(r for r in rows if r[1] == r1.record_ids[0])
    assert first == (
        r1.version_ids[0], r1.record_ids[0], r1.service_id, f"text 0 {r1.tag}",
        {"id": r1.record_ids[0], "text": f"text 0 {r1.tag}"}, "process_steps", None, "Heading", "en",
        f"https://example.test/{r1.record_ids[0]}", "portal", True, True, date(2026, 7, 12), MODEL,
    )
    assert active_versions(r2.service_id) == [] and active_versions(draft.service_id) == []

    # A newer wording of record R sits in a draft and in a validated release that is not active. The
    # engine must keep seeing the version the ACTIVE release holds, once, and not R's newer version
    # (a view that joined record_versions on record_id alone would return both).
    newer = add_version(worker, r1.record_ids[0], text=f"NEWER wording {r1.tag}")
    add_release(worker, r1.service_id, r1.tag, [(newer, r1.record_ids[0])])  # a draft
    unpublished = add_release(worker, r1.service_id, r1.tag, [(newer, r1.record_ids[0])])
    set_status(worker, unpublished, "validated")
    assert engine.execute(
        "SELECT version_id, embed_text FROM engine_active_versions WHERE record_id = %s", (r1.record_ids[0],)
    ).fetchall() == [(r1.version_ids[0], f"text 0 {r1.tag}")]
    assert active_versions(r1.service_id) == rows
    dims = engine.execute(
        "SELECT vector_dims(embedding), embedding <=> %s::vector FROM engine_active_versions WHERE record_id = %s",
        (UNIT_VECTOR, r1.record_ids[0]),
    ).fetchone()
    assert dims[0] == DIM and dims[1] == pytest.approx(0.0, abs=1e-6)

    # Publishing another release switches every view; nothing of the old one is left.
    publish(worker, r2.release_id, actor)
    assert engine.execute("SELECT release_id FROM engine_active_release").fetchall() == [(r2.release_id,)]
    assert active_versions(r1.service_id) == []
    assert [r[1] for r in active_versions(r2.service_id)] == sorted(r2.record_ids)
    names = [r[0] for r in engine.execute("SELECT service_id FROM engine_active_services")]
    assert r2.service_id in names and r1.service_id not in names

    # The engine reads these three views and nothing else.
    for table in BASE_TABLES:
        with pytest.raises(InsufficientPrivilege):
            engine.execute(f"SELECT * FROM {table}")
    for role in ("web", "worker"):  # the other roles get no view access either
        for view in ("engine_active_release", "engine_active_services", "engine_active_versions"):
            with pytest.raises(InsufficientPrivilege):
                connect(role).execute(f"SELECT * FROM {view}")


def test_the_engine_does_not_see_the_records_of_a_hidden_service(connect):
    worker, engine = connect("worker"), connect("engine")
    hidden = make_validated_release(connect, service_status="hidden")
    publish(worker, hidden.release_id, new_actor())
    assert engine.execute("SELECT release_id FROM engine_active_release").fetchall() == [(hidden.release_id,)]
    assert engine.execute(
        "SELECT count(*) FROM engine_active_services WHERE service_id = %s", (hidden.service_id,)
    ).fetchone() == (0,)
    assert engine.execute(
        "SELECT count(*) FROM engine_active_versions WHERE service_id = %s", (hidden.service_id,)
    ).fetchone() == (0,)


def test_the_engine_views_are_security_barrier_views_with_owner_rights(connect):
    # The views are security_barrier views: a condition written by the engine (for instance a cast
    # that fails on some text) is applied to the rows of the active release only, so it cannot be
    # used to probe rows of drafts or old versions.
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT c.relname, c.relowner::regrole::text, c.reloptions FROM pg_class c "
            "WHERE c.relname LIKE 'engine\\_active\\_%' AND c.relkind = 'v' ORDER BY c.relname"
        )
        views = cur.fetchall()
    assert [v[0] for v in views] == ["engine_active_release", "engine_active_services", "engine_active_versions"]
    for name, owner, options in views:
        assert owner == "gsn_migrate", name
        assert "security_barrier=true" in (options or []), name
        assert "security_invoker=true" not in (options or []), name  # the view runs with its owner's rights


def test_publish_rollback_functions_are_not_executable_by_web_or_engine(connect):
    built = make_validated_release(connect)
    publish(connect("worker"), built.release_id, new_actor())
    before = active_release_id(connect("worker"))
    for role in ("web", "engine"):
        conn = connect(role)
        with pytest.raises(InsufficientPrivilege):
            conn.execute("SELECT publish_release(%s, 'intruder')", (built.release_id,))
        with pytest.raises(InsufficientPrivilege):
            conn.execute("SELECT rollback_release('intruder')")
    assert active_release_id(connect("worker")) == before


# --- the guard triggers, by name (Task B2 registers these names) ---------------------------------

GUARDS = {  # table -> trigger function (each table has `<table>_guard_row` and `<table>_guard_truncate`)
    "services": "gsn_reject_change",
    "records": "gsn_reject_change",
    "record_versions": "gsn_reject_change",
    "publications": "gsn_reject_change",
    "releases": "releases_guard",
    "release_members": "release_children_guard",
    "release_services": "release_children_guard",
    "active_release": "active_release_guard",
}


# pg_trigger.tgtype bits
ROW, BEFORE, INSERT, DELETE, UPDATE, TRUNCATE = 1, 2, 4, 8, 16, 32
APPEND_ONLY_EVENTS = UPDATE | DELETE  # gsn_reject_change guards updates and deletes (not inserts)
STATE_RULE_EVENTS = INSERT | UPDATE | DELETE  # the state rules also check what is inserted
EVENTS = {
    "services": APPEND_ONLY_EVENTS,
    "records": APPEND_ONLY_EVENTS,
    "record_versions": APPEND_ONLY_EVENTS,
    "publications": APPEND_ONLY_EVENTS,
    "releases": STATE_RULE_EVENTS,
    "release_members": STATE_RULE_EVENTS,
    "release_services": STATE_RULE_EVENTS,
    "active_release": STATE_RULE_EVENTS,
}
GUARD_SEARCH_PATH = ["search_path=pg_catalog, pg_temp"]


def test_guard_triggers_have_the_registered_names_and_fire_for_normal_sessions(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT c.relname, t.tgname, p.proname, t.tgenabled, t.tgtype, "
            "t.tgqual IS NULL AND t.tgattr::text = '' AND t.tgnargs = 0 AS unconditional, "
            "p.prosecdef, p.proowner::regrole::text, p.proconfig "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE NOT t.tgisinternal AND c.relname = ANY(%s)",
            (list(GUARDS),),
        )
        found = {(r[0], r[1]): r[2:] for r in cur.fetchall()}
    expected = {}
    for table, function in GUARDS.items():
        # `tgtype` carries timing, level and events: both triggers fire BEFORE the change; the row
        # trigger for exactly the events above, the statement trigger for TRUNCATE only. `O` means
        # "enabled for normal sessions"; no WHEN condition, column list or arguments narrow them.
        expected[(table, f"{table}_guard_row")] = (
            function, "O", ROW | BEFORE | EVENTS[table], True, False, "gsn_migrate", GUARD_SEARCH_PATH,
        )
        expected[(table, f"{table}_guard_truncate")] = (
            function, "O", BEFORE | TRUNCATE, True, False, "gsn_migrate", GUARD_SEARCH_PATH,
        )
    assert found == expected  # exactly these two triggers per table; the functions are not SECURITY DEFINER


# --- pg_temp shadowing (a SECURITY DEFINER function must not resolve names in the caller's pg_temp) ---


def shadowed_worker_session(connect):
    """A worker session whose temporary schema holds look-alikes of what the functions touch.

    PostgreSQL searches pg_temp FIRST for relation and type names unless search_path lists it. The
    session therefore gets temporary tables named like the real ones and temporary domains named
    ``text``, ``int8``, ``jsonb`` and ``bool`` whose CHECK calls a function that raises "shadow used".
    A function that resolved one of those names through the caller's pg_temp would fail (types) or
    write to the wrong table (relations), and a SECURITY DEFINER function would run the look-alike
    function with the owner's rights.
    """
    conn = connect("worker")
    try:
        conn.execute("CREATE TEMP TABLE active_release (singleton boolean, release_id bigint, switched_at timestamptz)")
    except InsufficientPrivilege:
        pytest.skip("TEMP is revoked from PUBLIC on the database (db/init/20-guard.sh, spec 8.3): the worker can no longer create temporary objects, so there is nothing to shadow; the pinned search_path is checked in test_grants.py")
    conn.execute(
        "CREATE TEMP TABLE publications (publication_id bigint, release_id bigint, previous_release_id bigint, "
        "action text, at timestamptz, actor text)"
    )
    conn.execute("CREATE TEMP TABLE releases (release_id bigint, status text)")
    conn.execute(
        "CREATE TEMP TABLE audit_log (id bigint, at timestamptz, actor text, action text, target text, details jsonb)"
    )
    conn.execute(
        "CREATE FUNCTION pg_temp.shadow_hit(pg_catalog.text) RETURNS pg_catalog.bool LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'shadow used'; END $$"
    )
    for name in ("text", "int8", "jsonb", "bool"):
        conn.execute(f"CREATE DOMAIN pg_temp.{name} AS pg_catalog.{name} CHECK (pg_temp.shadow_hit('{name}'))")
    # Canary: an unqualified type name in this session really does reach the look-alike, so the
    # tests below cannot pass just because the trap is not armed.
    with pytest.raises(RaiseException, match="shadow used"):
        conn.execute("DO $$ DECLARE x text; BEGIN NULL; END $$")
    return conn


def assert_shadows_untouched(conn):
    for table in ("active_release", "publications", "releases", "audit_log"):
        assert conn.execute(f"SELECT count(*) FROM pg_temp.{table}").fetchone()[0] == 0, table


def test_publish_ignores_look_alike_tables_and_types_in_pg_temp(connect):
    r1, r2 = make_validated_release(connect), make_validated_release(connect)
    observer, web = connect("worker"), connect("web")
    actor = new_actor()
    publish(observer, r1.release_id, actor)
    before = last_publication_id(observer)
    attacker = shadowed_worker_session(connect)

    publish(attacker, r2.release_id, actor)  # must succeed, and write the REAL tables

    assert active_release_id(observer) == r2.release_id
    assert publications_after(observer, before) == [(r2.release_id, r1.release_id, "publish", actor)]
    assert [row[0] for row in audit_rows(web, actor)] == ["release.publish", "release.publish"]
    assert_shadows_untouched(attacker)


def test_rollback_ignores_look_alike_tables_and_types_in_pg_temp(connect):
    r1, r2 = make_validated_release(connect), make_validated_release(connect)
    observer, web = connect("worker"), connect("web")
    actor = new_actor()
    publish(observer, r1.release_id, actor)
    publish(observer, r2.release_id, actor)
    before = last_publication_id(observer)
    attacker = shadowed_worker_session(connect)

    assert rollback(attacker, actor) == r1.release_id  # must succeed, and write the REAL tables

    assert active_release_id(observer) == r1.release_id
    assert publications_after(observer, before) == [(r1.release_id, r2.release_id, "rollback", actor)]
    assert [row[0] for row in audit_rows(web, actor)] == ["release.publish", "release.publish", "release.rollback"]
    assert_shadows_untouched(attacker)


def test_the_guard_triggers_ignore_look_alike_tables_and_types_in_pg_temp(connect):
    # The guards run in the caller's session too (and inside the publish/rollback functions): a worker
    # building a draft still works, and the frozen-release rule still holds, with the look-alikes in place.
    built = make_draft_release(connect)
    attacker = shadowed_worker_session(connect)
    extra_record, extra_version = add_record(
        attacker, built.service_id, f"rec_{built.tag}_x", text=f"x {built.tag}"
    )
    attacker.execute(
        "INSERT INTO public.release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
        (built.release_id, extra_version, extra_record),
    )
    attacker.execute(
        "UPDATE public.releases SET status = 'validated', gate_report = '{}' WHERE release_id = %s",
        (built.release_id,),
    )
    with pytest.raises(RaiseException, match=f"release {built.release_id} is frozen"):
        attacker.execute("DELETE FROM public.release_members WHERE release_id = %s", (built.release_id,))
    assert_shadows_untouched(attacker)
