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


@pytest.mark.parametrize("role", ["web", "worker"])
def test_callers_cannot_backdate_a_row_or_choose_its_id(connect, role):
    # INSERT is granted on (actor, action, target, details) only, so `at` and `id` always come from
    # their defaults (now() and the identity sequence).
    with connect(role).cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO audit_log (at, actor, action) VALUES (%s, %s, %s)",
                ("2001-01-01T00:00:00Z", _actor(), "itest.backdated"),
            )
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(
                "INSERT INTO audit_log (id, actor, action) OVERRIDING SYSTEM VALUE VALUES (%s, %s, %s)",
                (1, _actor(), "itest.chosen_id"),
            )
        # Without OVERRIDING SYSTEM VALUE a GENERATED ALWAYS column refuses an explicit id anyway.
        with pytest.raises(psycopg.errors.GeneratedAlways):
            cur.execute("INSERT INTO audit_log (id, actor, action) VALUES (%s, %s, %s)", (1, _actor(), "itest.id"))
        # A normal insert still works, and the row got an id and a timestamp from the defaults.
        actor = _actor()
        cur.execute("INSERT INTO audit_log (actor, action) VALUES (%s, %s)", (actor, "itest.normal"))
    with connect("web").cursor() as cur:
        cur.execute("SELECT id, at > now() - interval '1 minute' FROM audit_log WHERE actor = %s", (actor,))
        (row_id, recent), = cur.fetchall()
        assert row_id > 0 and recent


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
        with pytest.raises(psycopg.errors.RaiseException, match=APPEND_ONLY):
            cur.execute("UPDATE audit_log SET action = 'tampered' WHERE actor = %s", (actor,))
        with pytest.raises(psycopg.errors.RaiseException, match=APPEND_ONLY):
            cur.execute("DELETE FROM audit_log WHERE actor = %s", (actor,))
        with pytest.raises(psycopg.errors.RaiseException, match=APPEND_ONLY):
            cur.execute("TRUNCATE audit_log")
        cur.execute("SELECT action FROM audit_log WHERE actor = %s", (actor,))
        assert cur.fetchall() == [("itest.owner",)]


@pytest.mark.parametrize("role", ["web", "engine", "worker"])
def test_non_owner_roles_cannot_create_tables(connect, role):
    with connect(role).cursor() as cur:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("CREATE TABLE itest_forbidden (id int)")
