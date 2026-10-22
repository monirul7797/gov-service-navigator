"""Release content tables, state rules, publish/rollback and engine views (migration 0002).

Everything is checked as the real roles (spec sections 3, 3.1 and 6). The stack tests share one
database across runs, so every test makes its own service and record ids (a random suffix) and
never assumes an empty ``active_release``; the one state that only a fresh database has lives in
``test_00_first_publication.py``.

Vectors are text literals cast to ``vector`` (the Python side never needs a pgvector package).
"""
import hashlib
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date

import psycopg
import pytest
from psycopg.errors import (
    CheckViolation,
    DataException,
    ForeignKeyViolation,
    InsufficientPrivilege,
    NotNullViolation,
    RaiseException,
    UniqueViolation,
)
from psycopg.types.json import Jsonb

DIM = 1024
UNIT_VECTOR = "[" + ",".join(["0.03125"] * DIM) + "]"  # (1/32)^2 * 1024 = 1: unit length
COMMIT = "0123456789abcdef0123456789abcdef01234567"
MODEL = "fake-sha256-v1"


def _tag():
    return uuid.uuid4().hex[:8]


def _actor():
    """A unique actor per test: audit_log rows can never be deleted, so tests only add rows."""
    return f"itest-{uuid.uuid4().hex}"


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Built:
    release_id: int
    service_id: str
    record_ids: tuple
    version_ids: tuple
    tag: str


def add_version(conn, record_id, *, text, content_hash=None, url_kind="portal", source_commit=COMMIT,
                embedding=UNIT_VECTOR, model=MODEL, source_url=None, verified_at=date(2026, 7, 12)):
    """Insert one ``record_versions`` row (as whatever role ``conn`` is) and return its ``version_id``."""
    row = conn.execute(
        "INSERT INTO record_versions (record_id, content_hash, text_hash, hash_version, record, embed_text, "
        "category, subcategory, heading, lang, source_url, url_kind, official, source_verified, verified_at, "
        "embedding, embedding_model, source_commit, created_by, change_note) "
        "VALUES (%s, %s, %s, 1, %s, %s, 'process_steps', NULL, 'Heading', 'en', %s, %s, true, true, %s, "
        "%s::vector, %s, %s, 'itest', NULL) RETURNING version_id",
        (
            record_id,
            content_hash or _sha(text),
            _sha(text),
            Jsonb({"id": record_id, "text": text}),
            text,
            source_url or f"https://example.test/{record_id}",
            url_kind,
            verified_at,
            embedding,
            model,
            source_commit,
        ),
    ).fetchone()
    return row[0]


def add_record(conn, service_id, record_id, *, text):
    """Insert a record and its first version; returns ``(record_id, version_id)``."""
    conn.execute("INSERT INTO records (record_id, service_id) VALUES (%s, %s)", (record_id, service_id))
    return record_id, add_version(conn, record_id, text=text)


def make_draft_release(connect, n_records=2, *, service_status="live"):
    """A service with ``n_records`` records (one version each) and a draft release holding them, as the worker."""
    worker = connect("worker")
    tag = _tag()
    service_id = f"svc_{tag}"
    record_ids, version_ids = [], []
    with worker.transaction():
        worker.execute(
            "INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, %s, %s)",
            (service_id, f"সেবা {tag}", f"Service {tag}"),
        )
        for i in range(n_records):
            record_id, version_id = add_record(worker, service_id, f"rec_{tag}_{i}", text=f"text {i} {tag}")
            record_ids.append(record_id)
            version_ids.append(version_id)
        release_id = worker.execute(
            "INSERT INTO releases (source_commit, manifest, registry, registry_sha256, thresholds, "
            "embedding_model, note, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, 'itest') RETURNING release_id",
            (
                COMMIT,
                Jsonb({"services": [{"service_id": service_id}]}),
                Jsonb({"services": {service_id: {"names": ["a", "b"]}}}),
                "f" * 64,
                Jsonb({"known_failures": []}),
                MODEL,
                f"itest {tag}",
            ),
        ).fetchone()[0]
        worker.execute(
            "INSERT INTO release_services (release_id, service_id, status) VALUES (%s, %s, %s)",
            (release_id, service_id, service_status),
        )
        for record_id, version_id in zip(record_ids, version_ids):
            worker.execute(
                "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
                (release_id, version_id, record_id),
            )
    return Built(release_id, service_id, tuple(record_ids), tuple(version_ids), tag)


def set_status(conn, release_id, status, *, override_reason=None):
    conn.execute(
        "UPDATE releases SET status = %s, gate_report = %s, override_reason = %s WHERE release_id = %s",
        (status, Jsonb({"status": status}), override_reason, release_id),
    )


def make_validated_release(connect, n_records=2, *, service_status="live"):
    """``make_draft_release`` and then the gate's verdict: the release becomes ``validated``."""
    built = make_draft_release(connect, n_records, service_status=service_status)
    set_status(connect("worker"), built.release_id, "validated")
    return built


def publish(conn, release_id, actor):
    conn.execute("SELECT publish_release(%s, %s)", (release_id, actor))


def rollback(conn, actor):
    return conn.execute("SELECT rollback_release(%s)", (actor,)).fetchone()[0]


def active_release_id(conn):
    row = conn.execute("SELECT release_id FROM active_release").fetchone()
    return row[0] if row else None


def last_publication_id(conn):
    return conn.execute("SELECT coalesce(max(publication_id), 0) FROM publications").fetchone()[0]


def publications_after(conn, publication_id):
    return conn.execute(
        "SELECT release_id, previous_release_id, action, actor FROM publications "
        "WHERE publication_id > %s ORDER BY publication_id",
        (publication_id,),
    ).fetchall()


def audit_rows(conn, actor):
    return conn.execute(
        "SELECT action, target, details FROM audit_log WHERE actor = %s ORDER BY id", (actor,)
    ).fetchall()


def wait_until_a_statement_waits_for_a_lock(watcher, text, timeout=10):
    """Poll until another session's statement containing ``text`` is blocked on a lock; the number of such sessions."""
    deadline = time.monotonic() + timeout
    waiting = 0
    while time.monotonic() < deadline and not waiting:
        waiting = watcher.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock' AND query LIKE %s AND pid <> pg_backend_pid()",
            (f"%{text}%",),
        ).fetchone()[0]
        time.sleep(0.05)
    return waiting


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
    tag = _tag()
    service_id = f"svc_{tag}"
    worker.execute("INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, 'b', 'e')", (service_id,))
    worker.execute("INSERT INTO records (record_id, service_id) VALUES (%s, %s)", (f"rec_{tag}", service_id))
    with pytest.raises(error):
        add_version(worker, f"rec_{tag}", text=f"text {tag}", **overrides)


def test_a_version_is_unique_per_record_hash_and_model(connect):
    worker = connect("worker")
    built = make_draft_release(connect, n_records=1)
    record_id = built.record_ids[0]
    same_hash = _sha(f"text 0 {built.tag}")
    with pytest.raises(UniqueViolation):  # same (record, content_hash, model) as the first version
        add_version(worker, record_id, text="other text", content_hash=same_hash)
    # a different model or a different content hash is a different version
    add_version(worker, record_id, text="other text", content_hash=same_hash, model="fake-other-model")
    add_version(worker, record_id, text="other text 2")


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
    publish(connect("worker"), built.release_id, _actor())  # makes sure `publications` has a row
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
    with pytest.raises(RaiseException, match="draft"):
        worker.execute(
            "INSERT INTO releases (status, source_commit, manifest, registry, registry_sha256, thresholds, "
            "embedding_model, created_by) VALUES ('validated', %s, '{}', '{}', 'x', '{}', 'm', 'itest')",
            (COMMIT,),
        )


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
    other_service = f"svc_{_tag()}"
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
    actor = _actor()

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
    actor = _actor()
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
    publish(worker, validated.release_id, _actor())
    before_publication = last_publication_id(worker)
    before_active = active_release_id(worker)
    actor = _actor()

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
    publish(connect("worker"), built.release_id, _actor())
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
    publish(connect("worker"), published.release_id, _actor())
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
    actor = _actor()
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
    actor = _actor()
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
    publish(worker, hidden.release_id, _actor())
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
    publish(connect("worker"), built.release_id, _actor())
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


def test_guard_triggers_have_the_registered_names_and_fire_for_normal_sessions(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT c.relname, t.tgname, p.proname, t.tgenabled, (t.tgtype & 1) = 1, (t.tgtype & 32) = 32, "
            "p.prosecdef, p.proowner::regrole::text "
            "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE NOT t.tgisinternal AND c.relname = ANY(%s)",
            (list(GUARDS),),
        )
        found = {(r[0], r[1]): r[2:] for r in cur.fetchall()}
    expected = {}
    for table, function in GUARDS.items():
        expected[(table, f"{table}_guard_row")] = (function, "O", True, False, False, "gsn_migrate")
        expected[(table, f"{table}_guard_truncate")] = (function, "O", False, True, False, "gsn_migrate")
    assert found == expected  # exactly these two triggers per table, enabled, not SECURITY DEFINER
