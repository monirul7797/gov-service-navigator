"""Database roles and the append-only ``audit_log`` (migration 0001), checked as the real roles."""
import uuid

import psycopg
import pytest

APPEND_ONLY = "audit_log is append-only"


def _actor():
    """A unique actor per test run: audit_log rows can never be deleted, so tests only add rows."""
    return f"itest-{uuid.uuid4().hex}"


@pytest.mark.parametrize("role", ["migrate", "web", "engine", "worker"])
def test_each_role_can_log_in(connect, role):
    with connect(role).cursor() as cur:
        cur.execute("SELECT current_user")
        assert cur.fetchone()[0] == f"gsn_{role}"


def test_vector_extension_installed(connect):
    with connect("web").cursor() as cur:
        cur.execute("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        assert cur.fetchone()[0] == 1


def test_web_can_insert_and_read_audit_log(connect):
    actor = _actor()
    with connect("web").cursor() as cur:
        cur.execute(
            "INSERT INTO audit_log (actor, action, target, details) VALUES (%s, %s, %s, %s::jsonb)",
            (actor, "itest.web_insert", "audit_log", '{"k": "v"}'),
        )
        cur.execute("SELECT action, target, details, at FROM audit_log WHERE actor = %s", (actor,))
        rows = cur.fetchall()
    assert len(rows) == 1
    action, target, details, at = rows[0]
    assert (action, target, details) == ("itest.web_insert", "audit_log", {"k": "v"})
    assert at is not None  # DEFAULT now()


def test_audit_log_details_default_to_an_empty_object(connect):
    actor = _actor()
    with connect("web").cursor() as cur:
        cur.execute("INSERT INTO audit_log (actor, action) VALUES (%s, %s)", (actor, "itest.defaults"))
        cur.execute("SELECT target, details FROM audit_log WHERE actor = %s", (actor,))
        assert cur.fetchone() == (None, {})


def test_worker_can_insert_audit_log(connect):
    actor = _actor()
    with connect("worker").cursor() as cur:
        cur.execute("INSERT INTO audit_log (actor, action) VALUES (%s, %s)", (actor, "itest.worker_insert"))
    # The worker has INSERT only, so the web role reads the row back.
    with connect("web").cursor() as cur:
        cur.execute("SELECT action FROM audit_log WHERE actor = %s", (actor,))
        assert cur.fetchall() == [("itest.worker_insert",)]


def test_web_cannot_update_or_delete_audit_log(connect):
    actor = _actor()
    with connect("web").cursor() as cur:
        cur.execute("INSERT INTO audit_log (actor, action) VALUES (%s, %s)", (actor, "itest.immutable"))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("UPDATE audit_log SET action = 'tampered' WHERE actor = %s", (actor,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("DELETE FROM audit_log WHERE actor = %s", (actor,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("TRUNCATE audit_log")
        cur.execute("SELECT action FROM audit_log WHERE actor = %s", (actor,))
        assert cur.fetchall() == [("itest.immutable",)]


def test_engine_cannot_touch_audit_log(connect):
    with connect("engine").cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("INSERT INTO audit_log (actor, action) VALUES ('itest-engine', 'nope')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("SELECT * FROM audit_log")


def test_owner_update_delete_truncate_blocked(connect):
    # The table owner (gsn_migrate) has every privilege on audit_log, so only the triggers stand
    # between it and the data: the append-only rule must hold for the owner too.
    actor = _actor()
    with connect("migrate").cursor() as cur:
        cur.execute("INSERT INTO audit_log (actor, action) VALUES (%s, %s)", (actor, "itest.owner"))
        with pytest.raises(psycopg.Error, match=APPEND_ONLY):
            cur.execute("UPDATE audit_log SET action = 'tampered' WHERE actor = %s", (actor,))
        with pytest.raises(psycopg.Error, match=APPEND_ONLY):
            cur.execute("DELETE FROM audit_log WHERE actor = %s", (actor,))
        with pytest.raises(psycopg.Error, match=APPEND_ONLY):
            cur.execute("TRUNCATE audit_log")
        cur.execute("SELECT action FROM audit_log WHERE actor = %s", (actor,))
        assert cur.fetchall() == [("itest.owner",)]


@pytest.mark.parametrize("role", ["web", "engine", "worker"])
def test_non_owner_roles_cannot_create_tables(connect, role):
    with connect(role).cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("CREATE TABLE itest_forbidden (id int)")
