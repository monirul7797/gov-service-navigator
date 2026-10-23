"""The audit_log hash chain (migration 0003, spec section 8.2), checked as the real roles.

Every row written to ``audit_log`` is chained to the one before it: a ``BEFORE INSERT`` trigger takes the
next ``chain_seq`` and the previous ``row_hash`` from the single-row head table ``audit_chain`` and
stores the row's own hash. ``audit_verify()`` walks the chain by ``chain_seq`` and reports the first
broken position. The tests below pin down:

* how rows are chained (also from the worker, which cannot read ``audit_log``) and the exact hash
  format, recomputed here in Python from the documented canonical row (``hash_version`` 1);
* that callers cannot choose the chain columns, and that concurrent inserts are chained in lock order;
* that ``audit_verify()`` notices every kind of tampering (edit, removal, truncation, a rewritten or
  missing head) and that its tampering tests leave nothing behind (each runs in a rolled-back
  transaction as the table owner, the only role that could do this at all);
* that the SECURITY DEFINER functions ignore look-alike temporary tables and types a caller creates.

Rows can never be deleted, so each test writes under its own unique actor and only ever reads its own rows.
"""
import hashlib
import json
import threading
from datetime import timezone

import pytest
from psycopg.errors import InsufficientPrivilege, NotNullViolation, RaiseException
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from release_helpers import new_actor, new_tag, wait_until_a_statement_waits_for_a_lock

ROW_COLUMNS = "id, chain_seq, prev_hash, row_hash, hash_version, at, actor, action, target, details"
DECOY_SEQ = 1_000_000  # what a look-alike audit_chain in pg_temp would hand out, if it were ever used


def add_audit_row(conn, actor, action="itest.chain", target="itest:chain", details=None):
    conn.execute(
        "INSERT INTO public.audit_log (actor, action, target, details) VALUES (%s, %s, %s, %s)",
        (actor, action, target, Jsonb(details if details is not None else {})),
    )


def chained_rows(conn, actor):
    """The actor's rows as dicts, in chain order (``conn`` must be allowed to read audit_log)."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(f"SELECT {ROW_COLUMNS} FROM public.audit_log WHERE actor = %s ORDER BY chain_seq", (actor,))
        return cur.fetchall()


def verify(conn):
    """``(ok, rows_checked, first_bad_seq)`` as audit_verify() reports it."""
    return conn.execute("SELECT ok, rows_checked, first_bad_seq FROM public.audit_verify()").fetchone()


def head(conn):
    return conn.execute("SELECT last_seq, last_hash FROM public.audit_chain").fetchone()


def count_chained(conn):
    return conn.execute("SELECT count(*) FROM public.audit_log WHERE chain_seq IS NOT NULL").fetchone()[0]


def last_chain_seq(conn):
    return conn.execute("SELECT max(chain_seq) FROM public.audit_log").fetchone()[0]


def canonical_hash(row, *, action=None):
    """sha256 of the documented canonical row (``hash_version`` 1), computed independently of the database.

    The canonical row is the JSON array [hex(prev_hash) or null, chain_seq, id, at as UTC with microseconds,
    actor, action, target or null, details] in PostgreSQL's jsonb text form (", " and ": " separators, raw
    UTF-8), hashed as UTF-8.
    """
    prev = row["prev_hash"]
    at = row["at"].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    canonical = json.dumps(
        [
            prev.hex() if prev is not None else None,
            row["chain_seq"],
            row["id"],
            at,
            row["actor"],
            action if action is not None else row["action"],
            row["target"],
            row["details"],
        ],
        separators=(", ", ": "),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).digest()


# --- chaining ---------------------------------------------------------------------------------


def test_worker_and_web_inserts_are_chained(connect):
    worker, web = connect("worker"), connect("web")
    actor = new_actor()
    add_audit_row(worker, actor, "itest.chain.worker")  # the worker cannot read audit_log, yet it is chained
    add_audit_row(web, actor, "itest.chain.web", target=None)
    first, second = chained_rows(web, actor)

    assert first["hash_version"] == second["hash_version"] == 1
    assert len(first["row_hash"]) == len(second["row_hash"]) == 32
    assert second["chain_seq"] > first["chain_seq"]
    assert second["id"] > first["id"]
    for row in (first, second):
        # each row points at the row with the previous chain_seq (the very first row of a chain at none)
        previous = web.execute(
            "SELECT row_hash FROM public.audit_log WHERE chain_seq = %s", (row["chain_seq"] - 1,)
        ).fetchone()
        assert row["prev_hash"] == (previous[0] if previous else None)
    ok, rows_checked, first_bad = verify(worker)
    assert (ok, first_bad) == (True, None)
    assert rows_checked == count_chained(web)


def test_row_hash_is_the_sha256_of_the_documented_canonical_row(connect):
    worker, web = connect("worker"), connect("web")
    actor = new_actor()
    add_audit_row(worker, actor, "itest.canonical", "লক্ষ্য:১", {"a": [1, None, "ক"], "b": {"c": True}})
    add_audit_row(web, actor, "itest.canonical.null_target", target=None)
    rows = chained_rows(web, actor)
    assert len(rows) == 2
    for row in rows:
        assert row["row_hash"] == canonical_hash(row)
    assert rows[1]["prev_hash"] == rows[0]["row_hash"]


def test_a_failed_insert_does_not_advance_the_chain(connect):
    worker, migrate = connect("worker"), connect("migrate")
    before = head(migrate)
    with pytest.raises(NotNullViolation):
        worker.execute("INSERT INTO public.audit_log (actor, action) VALUES (NULL, 'itest.rejected')")
    assert head(migrate) == before
    assert verify(worker)[0] is True


@pytest.mark.parametrize("role", ["web", "worker"])
@pytest.mark.parametrize(
    "column, value",
    [("chain_seq", 1), ("prev_hash", b"\x00"), ("row_hash", b"\x00" * 32), ("hash_version", 2)],
)
def test_callers_cannot_set_hash_columns(connect, role, column, value):
    with pytest.raises(InsufficientPrivilege):
        connect(role).execute(
            f"INSERT INTO public.audit_log (actor, action, {column}) VALUES (%s, 'itest.forged', %s)",
            (new_actor(), value),
        )


@pytest.mark.parametrize("role", ["web", "worker", "engine"])
def test_login_roles_cannot_touch_the_chain_head(connect, role):
    conn = connect(role)
    for statement in (
        "SELECT * FROM public.audit_chain",
        "SELECT last_seq FROM public.audit_chain",
        "INSERT INTO public.audit_chain (singleton, last_seq) VALUES (true, 0)",
        "UPDATE public.audit_chain SET last_seq = 0",
        "DELETE FROM public.audit_chain",
        "TRUNCATE public.audit_chain",
    ):
        with pytest.raises(InsufficientPrivilege):
            conn.execute(statement)


def test_the_engine_cannot_run_the_audit_functions(connect):
    with pytest.raises(InsufficientPrivilege):
        connect("engine").execute("SELECT * FROM public.audit_verify()")


@pytest.mark.parametrize("role", ["web", "worker"])
def test_inserts_work_without_sequence_usage(connect, role):
    # The id is an identity column: it draws from its sequence without any USAGE grant on it (migration
    # 0003 revokes the grant from 0001), so both roles still insert.
    conn = connect(role)
    assert conn.execute(
        "SELECT has_sequence_privilege(current_user, 'public.audit_log_id_seq', 'USAGE')"
    ).fetchone() == (False,)
    with pytest.raises(InsufficientPrivilege):
        conn.execute("SELECT nextval('public.audit_log_id_seq')")
    actor = new_actor()
    add_audit_row(conn, actor, f"itest.no_sequence_usage.{role}")
    assert len(chained_rows(connect("web"), actor)) == 1


# --- concurrency -------------------------------------------------------------------------------


def test_concurrent_inserts_chain_in_lock_order(connect):
    # The head row is locked by the first inserter until it commits, so a second insert waits, and the
    # chain follows the order in which the locks were taken (here: A, then B).
    a, b, watcher = connect("web"), connect("worker"), connect("worker")  # pg_stat_activity: same role as b
    actor_a, actor_b, marker = new_actor(), new_actor(), f"b1-concurrent-{new_tag()}"
    outcome = {}

    def insert_b():
        try:
            b.execute(
                f"INSERT INTO public.audit_log (actor, action) /* {marker} */ VALUES (%s, 'itest.concurrent.b')",
                (actor_b,),
            )
            outcome["ok"] = True
        except Exception as exc:  # reported by the main thread
            outcome["error"] = exc

    thread = None
    a.execute("BEGIN")
    try:
        add_audit_row(a, actor_a, "itest.concurrent.a")  # holds the head lock until COMMIT
        thread = threading.Thread(target=insert_b)
        thread.start()
        waiting = wait_until_a_statement_waits_for_a_lock(watcher, marker)
        assert waiting, "the second insert did not wait for the first one's lock on the chain head"
        assert thread.is_alive()
    finally:
        a.execute("COMMIT")
        if thread is not None:
            thread.join(timeout=15)
    assert not thread.is_alive()
    assert "error" not in outcome, outcome.get("error")
    assert outcome.get("ok")

    (row_a,) = chained_rows(a, actor_a)
    (row_b,) = chained_rows(a, actor_b)
    assert row_b["chain_seq"] == row_a["chain_seq"] + 1
    assert row_b["prev_hash"] == row_a["row_hash"]
    assert row_b["row_hash"] == canonical_hash(row_b)
    assert verify(watcher)[0] is True


# --- verification ------------------------------------------------------------------------------


def three_rows(connect):
    """Three consecutive chained rows written by the worker under one unique actor; returns them (as dicts)."""
    worker, web = connect("worker"), connect("web")
    actor = new_actor()
    for i in range(3):
        add_audit_row(worker, actor, f"itest.tamper.{i}", details={"i": i})
    rows = chained_rows(web, actor)
    assert [r["chain_seq"] for r in rows] == [rows[0]["chain_seq"] + i for i in range(3)]
    return rows


def disable_row_guard(migrate):
    migrate.execute("ALTER TABLE public.audit_log DISABLE TRIGGER audit_log_no_update_delete")


def trigger_states(conn):
    return dict(
        conn.execute(
            "SELECT tgname, tgenabled FROM pg_trigger WHERE tgrelid = 'public.audit_log'::regclass AND NOT tgisinternal"
        ).fetchall()
    )


def test_verify_is_ok_and_counts_the_chained_rows(connect):
    three_rows(connect)
    web, worker = connect("web"), connect("worker")
    ok, rows_checked, first_bad = verify(worker)
    assert (ok, first_bad) == (True, None)
    assert rows_checked == count_chained(web) >= 3
    assert verify(web) == (ok, rows_checked, first_bad)  # the web role may verify too


def test_verify_detects_a_changed_row_and_the_rollback_restores_everything(connect):
    rows = three_rows(connect)
    middle = rows[1]
    migrate, worker = connect("migrate"), connect("worker")
    assert verify(worker)[0] is True

    # Only the table owner can do this, and only by switching a guard trigger off. (Task B2's watchdog will
    # refuse the DISABLE TRIGGER; its test then tampers with the chain head instead, see below.)
    with migrate.transaction(force_rollback=True):
        disable_row_guard(migrate)
        migrate.execute("UPDATE public.audit_log SET action = 'itest.tampered' WHERE id = %s", (middle["id"],))
        ok, rows_checked, first_bad = verify(migrate)
    assert (ok, first_bad) == (False, middle["chain_seq"])
    assert rows_checked == middle["chain_seq"] - 1  # the rows before the broken one were checked and are fine

    assert verify(worker)[0] is True
    assert trigger_states(migrate)["audit_log_no_update_delete"] == "O"  # the rollback re-enabled the guard


@pytest.mark.parametrize(
    "assignment",
    ["action = 'itest.tampered'", "details = '{\"i\": 99}'::jsonb", "target = 'itest:elsewhere'",
     "actor = 'someone-else'", "at = at + interval '1 microsecond'", "prev_hash = NULL"],
)
def test_verify_detects_a_change_of_any_hashed_column(connect, assignment):
    rows = three_rows(connect)
    migrate = connect("migrate")
    with migrate.transaction(force_rollback=True):
        disable_row_guard(migrate)
        migrate.execute(f"UPDATE public.audit_log SET {assignment} WHERE id = %s", (rows[1]["id"],))
        ok, _, first_bad = verify(migrate)
    assert (ok, first_bad) == (False, rows[1]["chain_seq"])


def test_a_rewritten_row_hash_breaks_the_link_to_the_next_row(connect):
    # An attacker who knows the format can recompute the changed row's own hash; the next row still
    # points at the old one, so the chain breaks one position later.
    rows = three_rows(connect)
    middle = rows[1]
    migrate = connect("migrate")
    with migrate.transaction(force_rollback=True):
        disable_row_guard(migrate)
        migrate.execute(
            "UPDATE public.audit_log SET action = 'itest.tampered', row_hash = %s WHERE id = %s",
            (canonical_hash(middle, action="itest.tampered"), middle["id"]),
        )
        ok, _, first_bad = verify(migrate)
    assert (ok, first_bad) == (False, rows[2]["chain_seq"])


def test_verify_detects_a_removed_row_in_the_middle(connect):
    rows = three_rows(connect)
    migrate = connect("migrate")
    with migrate.transaction(force_rollback=True):
        disable_row_guard(migrate)
        migrate.execute("DELETE FROM public.audit_log WHERE id = %s", (rows[1]["id"],))
        ok, rows_checked, first_bad = verify(migrate)
    assert (ok, first_bad) == (False, rows[1]["chain_seq"])  # the first missing position
    assert rows_checked == rows[1]["chain_seq"] - 1


def test_verify_detects_removed_rows_at_the_end_of_the_chain(connect):
    three_rows(connect)
    migrate = connect("migrate")
    last = last_chain_seq(migrate)
    with migrate.transaction(force_rollback=True):
        disable_row_guard(migrate)
        migrate.execute("DELETE FROM public.audit_log WHERE chain_seq = %s", (last,))
        ok, rows_checked, first_bad = verify(migrate)
    assert (ok, first_bad) == (False, last)  # the remaining rows are consistent, but the head expects one more
    assert rows_checked == last - 1


@pytest.mark.parametrize(
    "assignment, bad_position",
    [
        ("last_hash = '\\x00'::bytea", "last"),  # head and last row disagree
        ("last_seq = last_seq + 1", "after_last"),  # head claims a row that is not there
        ("last_seq = last_seq - 1", "last"),  # head forgot the last row
    ],
)
def test_verify_detects_a_rewritten_chain_head(connect, assignment, bad_position):
    three_rows(connect)
    migrate = connect("migrate")
    last = last_chain_seq(migrate)
    with migrate.transaction(force_rollback=True):
        migrate.execute(f"UPDATE public.audit_chain SET {assignment}")
        ok, rows_checked, first_bad = verify(migrate)
    assert not ok
    assert first_bad == (last if bad_position == "last" else last + 1)
    assert rows_checked == first_bad - 1  # everything before the first disagreement was checked and is fine
    assert verify(migrate)[0] is True  # the rollback restored the head


def test_a_missing_chain_head_fails_verification_and_blocks_new_rows(connect):
    three_rows(connect)
    migrate = connect("migrate")
    last = last_chain_seq(migrate)
    with migrate.transaction(force_rollback=True):
        migrate.execute("DELETE FROM public.audit_chain")
        assert verify(migrate) == (False, last, last + 1)
        with pytest.raises(RaiseException, match="no head row"):  # fails closed: nothing is written unchained
            with migrate.transaction():
                add_audit_row(migrate, new_actor())
    assert verify(migrate)[0] is True


def test_rows_without_a_chain_position_are_pre_chain_and_skipped(connect):
    # A local database that held audit rows before migration 0003 has such rows (chain_seq NULL). Only a
    # table owner who switches the chain trigger off can create one now.
    three_rows(connect)
    migrate = connect("migrate")
    before = verify(migrate)
    with migrate.transaction(force_rollback=True):
        migrate.execute("ALTER TABLE public.audit_log DISABLE TRIGGER audit_log_chain_insert")
        add_audit_row(migrate, new_actor(), "itest.pre_chain")
        assert migrate.execute(
            "SELECT count(*) FROM public.audit_log WHERE chain_seq IS NULL AND action = 'itest.pre_chain'"
        ).fetchone()[0] == 1
        assert verify(migrate) == before
    assert trigger_states(migrate)["audit_log_chain_insert"] == "O"


def test_the_chain_trigger_is_a_before_insert_row_trigger_that_fires_in_normal_sessions(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT t.tgenabled, t.tgtype, p.proname, p.prosecdef, p.proowner::regrole::text "
            "FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid "
            "WHERE t.tgrelid = 'public.audit_log'::regclass AND t.tgname = 'audit_log_chain_insert'"
        )
        enabled, tgtype, function, definer, owner = cur.fetchone()
    row, before, insert = 1, 2, 4  # bits of pg_trigger.tgtype
    assert enabled == "O"
    assert tgtype == row | before | insert
    assert (function, definer, owner) == ("audit_log_chain", True, "gsn_migrate")


# --- name resolution in the SECURITY DEFINER functions -------------------------------------------


def shadowed_worker_session(connect):
    """A worker session whose temporary schema holds look-alikes of everything the audit functions touch.

    PostgreSQL searches pg_temp FIRST for relation and type names unless search_path lists it. The session
    therefore gets a temporary ``audit_chain`` (a decoy head far ahead of the real one) and ``audit_log``,
    and temporary domains named like the types the functions use, whose CHECK calls a function that raises
    "shadow used". A function that resolved one of those names through the caller's pg_temp would fail
    (types), write the wrong chain position (the head) or verify nothing (the log).
    """
    conn = connect("worker")
    try:
        conn.execute(
            "CREATE TEMP TABLE audit_chain (singleton pg_catalog.bool, last_seq pg_catalog.int8, "
            "last_hash pg_catalog.bytea)"
        )
    except InsufficientPrivilege:
        pytest.skip("TEMP is revoked from the worker: nothing can be shadowed")
    conn.execute(
        "CREATE TEMP TABLE audit_log (id pg_catalog.int8, at pg_catalog.timestamptz, actor pg_catalog.text, "
        "action pg_catalog.text, target pg_catalog.text, details pg_catalog.jsonb, chain_seq pg_catalog.int8, "
        "prev_hash pg_catalog.bytea, row_hash pg_catalog.bytea, hash_version pg_catalog.int2)"
    )
    conn.execute("INSERT INTO pg_temp.audit_chain VALUES (true, %s, %s)", (DECOY_SEQ, b"\xde\xca\xde"))
    conn.execute(
        "CREATE FUNCTION pg_temp.shadow_hit(pg_catalog.text) RETURNS pg_catalog.bool LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'shadow used'; END $$"
    )
    for name in ("text", "bytea", "int8", "jsonb", "bool", "timestamptz"):
        conn.execute(f"CREATE DOMAIN pg_temp.{name} AS pg_catalog.{name} CHECK (pg_temp.shadow_hit('{name}'))")
    # Canary: an unqualified type name in this session really does reach the look-alike, so the test
    # below cannot pass just because the trap is not armed.
    with pytest.raises(RaiseException, match="shadow used"):
        conn.execute("DO $$ DECLARE x text; BEGIN NULL; END $$")
    return conn


def test_chain_trigger_and_verify_ignore_look_alike_tables_and_types_in_pg_temp(connect):
    observer, web = connect("worker"), connect("web")
    before = verify(observer)
    attacker = shadowed_worker_session(connect)
    actor = new_actor()

    add_audit_row(attacker, actor, "itest.shadow", "itest:shadow", {"k": 1})  # must chain in the REAL tables
    status = verify(attacker)  # must read the REAL tables

    (row,) = chained_rows(web, actor)
    assert row["chain_seq"] != DECOY_SEQ + 1
    assert row["hash_version"] == 1 and row["row_hash"] == canonical_hash(row)
    assert status == (True, before[1] + 1, None)
    assert verify(observer) == status
    # the look-alikes were never used
    assert attacker.execute("SELECT count(*) FROM pg_temp.audit_log").fetchone()[0] == 0
    assert attacker.execute("SELECT last_seq, last_hash FROM pg_temp.audit_chain").fetchall() == [
        (DECOY_SEQ, b"\xde\xca\xde")
    ]
