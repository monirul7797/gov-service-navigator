"""The guard watchdog (db/init/20-guard.sh, migration 0004; spec section 8.1) and the database ownership
that goes with it (spec section 8.3), checked as the real roles.

The tables and functions that protect the release history and the audit log (append-only triggers,
state rules, the hash chain, publish/rollback/verify) are owned by ``gsn_migrate``, which could simply
drop or disable them. The watchdog closes that gap with event triggers owned by the superuser: after
every schema change in the database it checks the registered objects, and raises (which rolls the
change back) when one of these holds:

* a registered table is missing under its name (dropped, renamed, moved) or is not an ordinary table;
* a registered column is missing or has another type (adding columns is allowed);
* a registered trigger is missing, redefined (``CREATE OR REPLACE TRIGGER`` included) or not enabled in
  a mode that fires in normal sessions (``tgenabled`` ``O`` or ``A``);
* a registered function is missing or changed (``CREATE OR REPLACE``, ``ALTER FUNCTION``);
* a protected table has a rewrite rule, or row-level security is switched on for it.

Every attempted change below runs as ``gsn_migrate`` inside a transaction that is rolled back, so no
test alters the shared database. Each forbidden statement is preceded, in the same transaction, by a
harmless statement that must succeed: that proves the connection and the transaction are healthy, so
the test cannot pass merely because something unrelated failed. The error must come from the watchdog:
its message starts with ``gsn_guard:``.
"""
import uuid
from contextlib import contextmanager

import pytest
from psycopg.errors import InsufficientPrivilege, RaiseException

PROTECTED_TABLES = {
    "public.audit_log",
    "public.audit_chain",
    "public.record_versions",
    "public.publications",
    "public.services",
    "public.records",
    "public.releases",
    "public.release_members",
    "public.release_services",
    "public.active_release",
}
APPEND_ONLY_AND_STATE_TABLES = sorted(
    PROTECTED_TABLES - {"public.audit_log", "public.audit_chain"}
)
PROTECTED_TRIGGERS = {("public.audit_log", name) for name in (
    "audit_log_no_update_delete", "audit_log_no_truncate", "audit_log_chain_insert")} | {
    (table, f"{table.removeprefix('public.')}_{suffix}")
    for table in APPEND_ONLY_AND_STATE_TABLES
    for suffix in ("guard_row", "guard_truncate")
}
# Functions are registered under the identity PostgreSQL itself reports when an object is dropped
# (pg_event_trigger_dropped_objects), which spells argument types out in full.
PROTECTED_FUNCTIONS = {
    "public.audit_log_reject_change()",
    "public.audit_log_chain()",
    "public.audit_verify()",
    "public.audit_verify_rows(pg_catalog.regclass,bigint,pg_catalog.bytea)",
    "public.gsn_reject_change()",
    "public.releases_guard()",
    "public.release_children_guard()",
    "public.active_release_guard()",
    "public.publish_release(bigint,pg_catalog.text)",
    "public.rollback_release(pg_catalog.text)",
}

NOOP_FUNCTION = "$$BEGIN RETURN NEW; END$$"


@contextmanager
def rolled_back(connect):
    """A ``gsn_migrate`` connection inside a transaction that is rolled back when the block ends."""
    migrate = connect("migrate")
    with migrate.transaction(force_rollback=True):
        # A lock held by some other session would otherwise make a test hang instead of fail.
        migrate.execute("SET LOCAL lock_timeout = '10s'")
        yield migrate


def harmless(migrate):
    """A statement the watchdog must allow: proves the transaction is usable."""
    migrate.execute(f"CREATE TABLE public.itest_guard_{uuid.uuid4().hex} (id int)")


# --- what the watchdog forbids ---------------------------------------------------------------------

FORBIDDEN = [
    # the guard triggers of the audit log
    ("drop-trigger", "DROP TRIGGER audit_log_no_update_delete ON audit_log"),
    ("disable-trigger", "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"),
    ("replica-trigger", "ALTER TABLE audit_log ENABLE REPLICA TRIGGER audit_log_no_update_delete"),
    ("disable-truncate-trigger", "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_truncate"),
    ("disable-chain-trigger", "ALTER TABLE audit_log DISABLE TRIGGER audit_log_chain_insert"),
    ("disable-user-triggers", "ALTER TABLE records DISABLE TRIGGER USER"),
    ("rename-trigger", "ALTER TRIGGER releases_guard_row ON releases RENAME TO releases_guard_row_old"),
    ("drop-state-trigger", "DROP TRIGGER release_members_guard_truncate ON release_members"),
    ("disable-append-only-trigger", "ALTER TABLE services DISABLE TRIGGER services_guard_row"),
    # a trigger that keeps its name and stays enabled, but is made to do nothing
    (
        "replace-trigger-with-never-true-when",
        "CREATE OR REPLACE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW WHEN (false) EXECUTE FUNCTION audit_log_reject_change()",
    ),
    (
        "replace-trigger-function",
        "CREATE OR REPLACE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION gsn_reject_change()",
    ),
    (
        "replace-trigger-events",
        "CREATE OR REPLACE TRIGGER records_guard_row BEFORE DELETE ON records "
        "FOR EACH ROW EXECUTE FUNCTION gsn_reject_change()",
    ),
    # the tables themselves
    ("rename-table", "ALTER TABLE audit_log RENAME TO audit_log_old"),
    ("drop-table", "DROP TABLE publications"),
    ("drop-table-cascade", "DROP TABLE releases CASCADE"),
    ("drop-column", "ALTER TABLE audit_log DROP COLUMN row_hash"),
    ("rename-column", "ALTER TABLE audit_log RENAME COLUMN actor TO who"),
    ("retype-column", "ALTER TABLE audit_log ALTER COLUMN target TYPE varchar(500)"),
    ("drop-column-of-state-table", "ALTER TABLE records DROP COLUMN created_at"),
    ("rename-column-of-state-table", "ALTER TABLE records RENAME COLUMN service_id TO svc"),
    ("create-rule", "CREATE RULE keep AS ON DELETE TO record_versions DO INSTEAD NOTHING"),
    ("enable-rls", "ALTER TABLE releases ENABLE ROW LEVEL SECURITY"),
    ("force-rls", "ALTER TABLE active_release FORCE ROW LEVEL SECURITY"),
    # the chain head is protected for existence and shape only (no triggers)
    ("drop-chain-head", "DROP TABLE audit_chain"),
    ("rename-chain-head", "ALTER TABLE audit_chain RENAME TO audit_chain_old"),
    ("drop-chain-head-column", "ALTER TABLE audit_chain DROP COLUMN last_hash"),
    ("rename-chain-head-column", "ALTER TABLE audit_chain RENAME COLUMN last_seq TO seq"),
    ("retype-chain-head-column", "ALTER TABLE audit_chain ALTER COLUMN last_seq TYPE integer"),
    # the functions
    (
        "replace-trigger-function-body",
        f"CREATE OR REPLACE FUNCTION audit_log_reject_change() RETURNS trigger LANGUAGE plpgsql AS {NOOP_FUNCTION}",
    ),
    (
        "replace-state-guard-body",
        f"CREATE OR REPLACE FUNCTION public.releases_guard() RETURNS trigger LANGUAGE plpgsql AS {NOOP_FUNCTION}",
    ),
    ("security-invoker", "ALTER FUNCTION publish_release(bigint, text) SECURITY INVOKER"),
    ("unpin-search-path", "ALTER FUNCTION audit_log_chain() RESET search_path"),
    ("change-search-path", "ALTER FUNCTION audit_log_chain() SET search_path = public"),
    ("change-volatility", "ALTER FUNCTION audit_verify() VOLATILE"),
    ("rename-function", "ALTER FUNCTION gsn_reject_change() RENAME TO gsn_reject_change_old"),
    ("drop-function-cascade", "DROP FUNCTION rollback_release(text) CASCADE"),
    ("drop-verification-core", "DROP FUNCTION audit_verify_rows(regclass, bigint, bytea)"),
    # everything at once
    ("drop-owned", "DROP OWNED BY gsn_migrate CASCADE"),
]


@pytest.mark.parametrize("ddl", [pytest.param(sql, id=name) for name, sql in FORBIDDEN])
def test_watchdog_rejects(connect, ddl):
    with rolled_back(connect) as migrate:
        harmless(migrate)  # the transaction works, so the next failure is the watchdog's own
        with pytest.raises(RaiseException) as caught:
            migrate.execute(ddl)
    assert caught.value.diag.message_primary.startswith("gsn_guard:"), caught.value.diag.message_primary


MESSAGES = [
    # drops are reported by the sql_drop trigger, which names the object as PostgreSQL spells it
    ("DROP TABLE publications", r"protected (table|trigger) .*publications.* was dropped"),
    (
        "DROP TRIGGER audit_log_no_update_delete ON audit_log",
        r"protected trigger audit_log_no_update_delete on public\.audit_log was dropped",
    ),
    (
        "DROP FUNCTION rollback_release(text) CASCADE",
        r"protected function public\.rollback_release\(pg_catalog\.text\) was dropped",
    ),
    # everything else is found by looking at the state after the change
    (
        "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete",
        r"trigger audit_log_no_update_delete of protected table public\.audit_log is not enabled",
    ),
    ("ALTER TABLE audit_log RENAME TO audit_log_old", r"protected table public\.audit_log is missing"),
    ("ALTER TABLE audit_log DROP COLUMN row_hash", r"column row_hash of protected table public\.audit_log"),
    ("ALTER FUNCTION audit_verify() VOLATILE", r"protected function public\.audit_verify\(\) was changed"),
    (
        "CREATE OR REPLACE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW WHEN (false) EXECUTE FUNCTION audit_log_reject_change()",
        r"trigger audit_log_no_update_delete of protected table public\.audit_log was redefined",
    ),
    ("CREATE RULE keep AS ON DELETE TO record_versions DO INSTEAD NOTHING", r"public\.record_versions has a rewrite rule"),
    ("ALTER TABLE releases ENABLE ROW LEVEL SECURITY", r"row-level security is switched on for protected table public\.releases"),
]


@pytest.mark.parametrize("ddl, message", [pytest.param(sql, text, id=sql[:40]) for sql, text in MESSAGES])
def test_the_message_names_what_was_violated(connect, ddl, message):
    with rolled_back(connect) as migrate:
        with pytest.raises(RaiseException, match=message):
            migrate.execute(ddl)


# --- what it allows --------------------------------------------------------------------------------

ALLOWED = [
    ("add-column", "ALTER TABLE audit_log ADD COLUMN note text"),
    ("add-column-to-state-table", "ALTER TABLE releases ADD COLUMN itest_note text"),
    ("add-index", "CREATE INDEX itest_guard_idx ON audit_log (actor)"),
    ("comment", "COMMENT ON TABLE audit_log IS 'itest'"),
    ("enable-trigger-no-op", "ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"),
    ("enable-always", "ALTER TABLE audit_log ENABLE ALWAYS TRIGGER audit_log_no_update_delete"),
    (
        "recreate-trigger-identically",
        "CREATE OR REPLACE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change()",
    ),
    ("replace-an-unprotected-function", f"CREATE OR REPLACE FUNCTION public.itest_guard_fn() RETURNS trigger LANGUAGE plpgsql AS {NOOP_FUNCTION}"),
    ("create-view", "CREATE VIEW public.itest_guard_view AS SELECT actor FROM audit_log"),
    ("scratch-table", "CREATE TABLE public.itest_guard_scratch (id int)"),
    ("scratch-copy-of-the-log", "CREATE TABLE public.itest_guard_copy AS SELECT * FROM public.audit_log"),
    ("scratch-copy-of-a-state-table", "CREATE TABLE public.itest_guard_copy AS SELECT * FROM public.releases"),
    ("drop-scratch-table", "DROP TABLE public.itest_guard_old"),
    ("grant", "GRANT SELECT ON audit_log TO gsn_web"),
]


@pytest.mark.parametrize("ddl", [pytest.param(sql, id=name) for name, sql in ALLOWED])
def test_the_watchdog_allows(connect, ddl):
    with rolled_back(connect) as migrate:
        migrate.execute("CREATE TABLE public.itest_guard_old (id int)")  # for the drop case
        migrate.execute(ddl)


def test_adding_a_column_is_allowed(connect):
    with rolled_back(connect) as migrate:
        migrate.execute("ALTER TABLE audit_log ADD COLUMN note text")
        columns = migrate.execute(
            "SELECT attname FROM pg_attribute WHERE attrelid = 'public.audit_log'::regclass "
            "AND attnum > 0 AND NOT attisdropped"
        ).fetchall()
        assert ("note",) in columns


def test_unrelated_ddl_is_allowed(connect):
    with rolled_back(connect) as migrate:
        name = f"scratch_{uuid.uuid4().hex}"
        migrate.execute(f"CREATE TABLE {name} (id int)")
        migrate.execute(f"ALTER TABLE {name} ADD COLUMN x text")
        migrate.execute(f"ALTER TABLE {name} RENAME TO {name}_b")
        migrate.execute(f"DROP TABLE {name}_b")


def test_scratch_copies_and_chain_head_writes_still_work(connect):
    # The audit-chain tests tamper with COPIES of audit_log and rewrite the chain head, all inside
    # rolled-back transactions as gsn_migrate. The watchdog must leave both possible.
    with rolled_back(connect) as migrate:
        migrate.execute("CREATE TABLE public.itest_guard_copy AS SELECT * FROM public.audit_log")
        migrate.execute("UPDATE public.itest_guard_copy SET action = 'itest.tampered'")
        migrate.execute("DELETE FROM public.itest_guard_copy")
        migrate.execute("UPDATE public.audit_chain SET last_hash = '\\xdeca'::bytea, last_seq = last_seq + 7")
        (seq,) = migrate.execute("SELECT last_seq FROM public.audit_chain").fetchone()
        assert seq >= 7


def test_the_protected_objects_are_intact_after_all_that(connect):
    # Everything above was rolled back: the guard triggers are still there and still enabled.
    with connect("migrate").cursor() as cur:
        cur.execute(
            "SELECT c.relname || '.' || t.tgname, t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE c.relnamespace = 'public'::regnamespace AND NOT t.tgisinternal"
        )
        triggers = dict(cur.fetchall())
    for table, trigger in PROTECTED_TRIGGERS:
        assert triggers[f"{table.removeprefix('public.')}.{trigger}"] in ("O", "A"), (table, trigger)


# --- the registration functions ---------------------------------------------------------------------


def test_registration_is_not_callable_by_other_roles(connect):
    for role in ("web", "worker", "engine"):
        conn = connect(role)
        for call in (
            "SELECT gsn_guard.protect_table('public.audit_log', ARRAY[]::text[])",
            "SELECT gsn_guard.protect_function('public.audit_verify()')",
        ):
            with pytest.raises(InsufficientPrivilege):
                conn.execute(call)
        rows = conn.execute(
            "SELECT p.proname, has_function_privilege(p.oid, 'EXECUTE') FROM pg_proc p "
            "WHERE p.pronamespace = 'gsn_guard'::regnamespace"
        ).fetchall()
        assert {name for name, _ in rows} >= {"protect_table", "protect_function", "check", "check_drop"}
        assert [name for name, executable in rows if executable] == [], role


def test_only_the_migrate_role_may_register(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT p.proname, has_function_privilege('gsn_migrate', p.oid, 'EXECUTE') FROM pg_proc p "
            "WHERE p.pronamespace = 'gsn_guard'::regnamespace"
        )
        executable = dict(cur.fetchall())
    # The event trigger functions are invoked by PostgreSQL itself, regardless of grants; the two
    # definition helpers are for the superuser's own functions.
    assert {name for name, can in executable.items() if can} == {"protect_table", "protect_function"}
    assert {"check", "check_drop", "trigger_definition", "function_fingerprint"} <= set(executable)


@pytest.mark.parametrize(
    "call, message",
    [
        ("SELECT gsn_guard.protect_table('audit_log', ARRAY[]::text[])", "schema-qualified"),
        ("SELECT gsn_guard.protect_table('public.itest_no_such_table', ARRAY[]::text[])", "does not exist"),
        ("SELECT gsn_guard.protect_table('public.audit_log', ARRAY[]::text[])", "public.audit_log is already protected"),
        ("SELECT gsn_guard.protect_table('public.engine_active_services', ARRAY[]::text[])", "not an ordinary"),
        ("SELECT gsn_guard.protect_function('audit_verify()')", "schema-qualified"),
        ("SELECT gsn_guard.protect_function('public.itest_no_such_function()')", "does not exist"),
        ("SELECT gsn_guard.protect_function('public.audit_verify()')", "public.audit_verify() is already protected"),
        ("SELECT gsn_guard.protect_function('public.audit_verify( )')", "is already protected"),
    ],
)
def test_registration_refuses(connect, call, message):
    with rolled_back(connect) as migrate:
        with pytest.raises(RaiseException, match=message.replace("(", r"\(").replace(")", r"\)")):
            migrate.execute(call)


def test_registration_refuses_a_missing_or_disabled_trigger(connect):
    with rolled_back(connect) as migrate:
        migrate.execute("CREATE TABLE public.itest_guard_t (id int)")
        migrate.execute(f"CREATE FUNCTION public.itest_guard_t_fn() RETURNS trigger LANGUAGE plpgsql AS {NOOP_FUNCTION}")
        migrate.execute(
            "CREATE TRIGGER itest_guard_t_trg BEFORE INSERT ON public.itest_guard_t "
            "FOR EACH ROW EXECUTE FUNCTION public.itest_guard_t_fn()"
        )
        with pytest.raises(RaiseException, match="has no trigger no_such_trigger"):
            with migrate.transaction():
                migrate.execute("SELECT gsn_guard.protect_table('public.itest_guard_t', ARRAY['no_such_trigger'])")
        migrate.execute("ALTER TABLE public.itest_guard_t DISABLE TRIGGER itest_guard_t_trg")
        with pytest.raises(RaiseException, match="not enabled"):
            with migrate.transaction():
                migrate.execute("SELECT gsn_guard.protect_table('public.itest_guard_t', ARRAY['itest_guard_t_trg'])")
        # the failed attempts registered nothing (a registration is all or nothing)
        migrate.execute("ALTER TABLE public.itest_guard_t ENABLE TRIGGER itest_guard_t_trg")
        migrate.execute("SELECT gsn_guard.protect_table('public.itest_guard_t', ARRAY['itest_guard_t_trg'])")


def test_a_newly_registered_table_is_protected_from_then_on(connect):
    # Registration itself, end to end, on a scratch table: nothing here outlives the transaction.
    with rolled_back(connect) as migrate:
        migrate.execute("CREATE TABLE public.itest_guard_t (id int, note text)")
        migrate.execute(f"CREATE FUNCTION public.itest_guard_t_fn() RETURNS trigger LANGUAGE plpgsql AS {NOOP_FUNCTION}")
        migrate.execute(
            "CREATE TRIGGER itest_guard_t_trg BEFORE INSERT ON public.itest_guard_t "
            "FOR EACH ROW EXECUTE FUNCTION public.itest_guard_t_fn()"
        )
        migrate.execute("ALTER TABLE public.itest_guard_t DISABLE TRIGGER itest_guard_t_trg")  # fine: not yet registered
        migrate.execute("ALTER TABLE public.itest_guard_t ENABLE TRIGGER itest_guard_t_trg")
        migrate.execute("SELECT gsn_guard.protect_table('public.itest_guard_t', ARRAY['itest_guard_t_trg'])")
        migrate.execute("SELECT gsn_guard.protect_function('public.itest_guard_t_fn()')")
        rows = migrate.execute(
            "SELECT kind, name FROM gsn_guard.registry WHERE name LIKE '%itest_guard_t%' ORDER BY kind, name"
        ).fetchall()
        assert rows == [
            ("function", "public.itest_guard_t_fn()"),
            ("table", "public.itest_guard_t"),
            ("trigger", "itest_guard_t_trg"),
        ]
        for ddl in (
            "ALTER TABLE public.itest_guard_t DISABLE TRIGGER itest_guard_t_trg",
            "ALTER TABLE public.itest_guard_t RENAME TO itest_guard_u",
            "ALTER TABLE public.itest_guard_t DROP COLUMN note",
            "ALTER TABLE public.itest_guard_t ALTER COLUMN note TYPE varchar(5)",
            "DROP TABLE public.itest_guard_t",
            "DROP TRIGGER itest_guard_t_trg ON public.itest_guard_t",
            "CREATE OR REPLACE FUNCTION public.itest_guard_t_fn() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RETURN OLD; END$$",
            "DROP FUNCTION public.itest_guard_t_fn() CASCADE",
        ):
            with pytest.raises(RaiseException, match="gsn_guard:"):
                with migrate.transaction():  # a savepoint: the refusal undoes only this statement
                    migrate.execute(ddl)
        migrate.execute("ALTER TABLE public.itest_guard_t ADD COLUMN extra text")  # still allowed


def test_the_registry_lists_every_protected_object(connect):
    with connect("migrate").cursor() as cur:
        cur.execute("SELECT name FROM gsn_guard.registry WHERE kind = 'table'")
        assert {row[0] for row in cur.fetchall()} == PROTECTED_TABLES
        cur.execute("SELECT parent, name FROM gsn_guard.registry WHERE kind = 'trigger'")
        assert {tuple(row) for row in cur.fetchall()} == PROTECTED_TRIGGERS
        cur.execute("SELECT name FROM gsn_guard.registry WHERE kind = 'function'")
        assert {row[0] for row in cur.fetchall()} == PROTECTED_FUNCTIONS
    assert len(PROTECTED_TABLES) == 10 and len(PROTECTED_FUNCTIONS) == 10


def test_every_trigger_and_definer_function_of_the_schema_is_registered(connect):
    # A guard added by a later migration but never registered would be a silent hole. The audit chain
    # head is the one registered table with no trigger.
    with connect("migrate").cursor() as cur:
        cur.execute(
            "SELECT c.relnamespace::regnamespace::text || '.' || c.relname, t.tgname FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE c.relnamespace = 'public'::regnamespace AND NOT t.tgisinternal"
        )
        triggers = {tuple(row) for row in cur.fetchall()}
        assert triggers == PROTECTED_TRIGGERS
        cur.execute(
            "SELECT (pg_identify_object('pg_proc'::regclass, p.oid, 0)).identity FROM pg_proc p "
            "WHERE p.pronamespace = 'public'::regnamespace "
            "AND (p.prosecdef OR p.prorettype = 'trigger'::regtype) "
            "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e')"
        )
        assert {row[0] for row in cur.fetchall()} == PROTECTED_FUNCTIONS


def test_the_registry_fingerprints_match_the_functions(connect):
    with connect("migrate").cursor() as cur:
        cur.execute(
            "SELECT g.name, g.detail, md5(p.prosrc || coalesce(p.proconfig::text, '') || p.prosecdef::text "
            "|| p.proowner::text || p.provolatile::text) "
            "FROM gsn_guard.registry g JOIN pg_proc p ON p.oid = to_regprocedure(g.name) WHERE g.kind = 'function'"
        )
        rows = cur.fetchall()
    assert len(rows) == len(PROTECTED_FUNCTIONS)
    for name, recorded, computed in rows:
        assert recorded == computed, name


# --- who owns what, and what the schema looks like ---------------------------------------------------


def test_the_guard_schema_and_its_event_triggers_belong_to_the_superuser(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT r.rolsuper FROM pg_namespace n JOIN pg_roles r ON r.oid = n.nspowner WHERE n.nspname = 'gsn_guard'"
        )
        assert cur.fetchall() == [(True,)]
        cur.execute(
            "SELECT e.evtname, e.evtevent, e.evtenabled, r.rolsuper, p.prosecdef, p.proconfig "
            "FROM pg_event_trigger e JOIN pg_roles r ON r.oid = e.evtowner JOIN pg_proc p ON p.oid = e.evtfoid "
            "ORDER BY e.evtevent"
        )
        assert cur.fetchall() == [
            ("gsn_guard_check", "ddl_command_end", "O", True, True, ["search_path=pg_catalog, gsn_guard, pg_temp"]),
            ("gsn_guard_check_drop", "sql_drop", "O", True, True, ["search_path=pg_catalog, gsn_guard, pg_temp"]),
        ]
        cur.execute(
            "SELECT p.oid::regprocedure::text, r.rolsuper, p.prosecdef, p.proconfig FROM pg_proc p "
            "JOIN pg_roles r ON r.oid = p.proowner WHERE p.pronamespace = 'gsn_guard'::regnamespace "
            "ORDER BY 1"
        )
        functions = cur.fetchall()
    assert functions, "no functions in gsn_guard"
    for signature, superuser, _definer, config in functions:
        assert superuser is True, signature
        assert config == ["search_path=pg_catalog, gsn_guard, pg_temp"], signature  # pg_temp last, always
    definers = {signature for signature, _s, definer, _c in functions if definer}
    assert {
        "gsn_guard.protect_table(text,text[])",
        "gsn_guard.protect_function(text)",
        'gsn_guard."check"()',  # CHECK is a keyword, so PostgreSQL quotes it
        "gsn_guard.check_drop()",
    } <= definers


def test_migrate_can_neither_read_nor_write_the_registry_tables_nor_add_to_the_schema(connect):
    with rolled_back(connect) as migrate:
        for table in ("protected_tables", "protected_triggers", "protected_functions"):
            with pytest.raises(InsufficientPrivilege):
                with migrate.transaction():
                    migrate.execute(f"SELECT * FROM gsn_guard.{table}")
            with pytest.raises(InsufficientPrivilege):
                with migrate.transaction():
                    migrate.execute(f"DELETE FROM gsn_guard.{table}")
        with pytest.raises(InsufficientPrivilege):
            with migrate.transaction():
                migrate.execute("INSERT INTO gsn_guard.protected_functions VALUES ('public.x()', 'x')")
        with pytest.raises(InsufficientPrivilege):
            with migrate.transaction():
                migrate.execute("CREATE FUNCTION gsn_guard.evil() RETURNS int LANGUAGE sql AS 'SELECT 1'")


@pytest.mark.parametrize(
    "statement",
    [
        "DROP SCHEMA gsn_guard CASCADE",
        "ALTER SCHEMA gsn_guard OWNER TO gsn_migrate",
        "DROP EVENT TRIGGER gsn_guard_check",
        "DROP EVENT TRIGGER gsn_guard_check_drop",
        "ALTER EVENT TRIGGER gsn_guard_check DISABLE",
        "ALTER EVENT TRIGGER gsn_guard_check_drop DISABLE",
        "ALTER FUNCTION gsn_guard.check() SET search_path = public",
        "DROP FUNCTION gsn_guard.check_drop()",
        "CREATE OR REPLACE FUNCTION gsn_guard.check() RETURNS event_trigger LANGUAGE plpgsql AS $$BEGIN NULL; END$$",
        "SET event_triggers = off",
        "SET session_replication_role = replica",
    ],
)
def test_migrate_cannot_remove_or_bypass_the_watchdog(connect, statement):
    with rolled_back(connect) as migrate:
        with pytest.raises(InsufficientPrivilege):
            migrate.execute(statement)


def test_other_roles_cannot_even_see_the_guard_schema(connect):
    for role in ("web", "worker", "engine"):
        with pytest.raises(InsufficientPrivilege):
            connect(role).execute("SELECT * FROM gsn_guard.registry")


# --- database ownership (spec section 8.3) -------------------------------------------------------------


def test_the_database_belongs_to_the_superuser_and_the_schema_to_migrate(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT r.rolname, r.rolsuper FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba "
            "WHERE d.datname = current_database()"
        )
        ((owner, superuser),) = cur.fetchall()
        assert superuser is True and owner not in {f"gsn_{role}" for role in ("migrate", "web", "engine", "worker")}
        cur.execute("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'public'")
        assert cur.fetchone()[0] == "gsn_migrate"  # only the database moved; migrations still create tables


@pytest.mark.parametrize("role", ["migrate", "web", "engine", "worker"])
def test_no_login_role_can_create_schemas_or_temporary_objects(connect, role):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT has_database_privilege(%s, current_database(), 'CREATE'), "
            "has_database_privilege(%s, current_database(), 'TEMPORARY'), "
            "has_database_privilege(%s, current_database(), 'CONNECT')",
            (f"gsn_{role}",) * 3,
        )
        assert cur.fetchone() == (False, False, True)  # all four still connect
    conn = connect(role)
    with conn.transaction(force_rollback=True):  # should a refusal ever fail, nothing is left behind
        with pytest.raises(InsufficientPrivilege):
            with conn.transaction():
                conn.execute("CREATE TEMP TABLE itest_forbidden_temp (id int)")
        with pytest.raises(InsufficientPrivilege):
            with conn.transaction():
                conn.execute("CREATE SCHEMA itest_forbidden_schema")


def test_the_public_pseudo_role_has_no_temporary_privilege_on_the_database(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM aclexplode((SELECT datacl FROM pg_database WHERE datname = current_database())) "
            "WHERE grantee = 0"
        )
        assert cur.fetchone()[0] == 0  # neither CONNECT nor TEMPORARY for PUBLIC


@pytest.mark.parametrize("role", ["migrate", "web", "engine", "worker"])
def test_public_cannot_connect_to_the_postgres_database(connect, role):
    with connect("web").cursor() as cur:
        cur.execute("SELECT has_database_privilege(%s, 'postgres', 'CONNECT')", (f"gsn_{role}",))
        assert cur.fetchone()[0] is False
