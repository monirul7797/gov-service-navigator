"""The exact privilege matrix of the four roles, read from the system catalog.

Behavioural tests elsewhere try individual statements; this one compares the complete set of
privileges against the specification (spec section 6.1), so an accidental extra GRANT (or a missing
one) in any migration fails here even if no other test happens to try it.

Expected matrix for ``audit_log`` (``id``, ``at`` and the four hash-chain columns added by migration
0003 are never insertable by a caller; the trigger sets them):

    role         table privileges                columns INSERT         columns SELECT   sequence
    gsn_migrate  all (it is the owner)           all                    all              all
    gsn_web      SELECT                          actor action target    all              none
                                                 details
    gsn_worker   none                            actor action target    none             none
                                                 details
    gsn_engine   none                            none                   none             none

(``audit_log_id_seq`` needs no grant for web and worker: the identity column draws from it without
one; migration 0003 revoked the USAGE that 0001 had granted.)

The owner's UPDATE/DELETE/TRUNCATE are still blocked, by the triggers (see test_database.py).

The release tables, views, sequences and functions of migration 0002 (spec section 3.1) are checked
further down in the same way: gsn_worker writes drafts, gsn_engine reads three views, gsn_web gets
nothing new, and gsn_migrate owns everything. Migration 0003 (spec section 8.2) adds the chain head
table, which no login role may touch, and the three audit functions.
"""
import pytest

TABLE_PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"]
SEQUENCE_PRIVILEGES = ["USAGE", "SELECT", "UPDATE"]
CALLER_COLUMNS = ["actor", "action", "target", "details"]
CHAIN_COLUMNS = ["chain_seq", "prev_hash", "row_hash", "hash_version"]  # migration 0003
COLUMNS = ["id", "at", *CALLER_COLUMNS, *CHAIN_COLUMNS]

EXPECTED = {
    "migrate": {
        "table": set(TABLE_PRIVILEGES),
        "insert_columns": set(COLUMNS),
        "select_columns": set(COLUMNS),
        "update_columns": set(COLUMNS),
        "sequence": set(SEQUENCE_PRIVILEGES),
    },
    "web": {
        "table": {"SELECT"},
        "insert_columns": set(CALLER_COLUMNS),
        "select_columns": set(COLUMNS),
        "update_columns": set(),
        "sequence": set(),
    },
    "worker": {
        "table": set(),
        "insert_columns": set(CALLER_COLUMNS),
        "select_columns": set(),
        "update_columns": set(),
        "sequence": set(),
    },
    "engine": {
        "table": set(),
        "insert_columns": set(),
        "select_columns": set(),
        "update_columns": set(),
        "sequence": set(),
    },
}


def _granted(cur, query, role, names):
    """The subset of ``names`` for which ``query`` (a has_*_privilege call taking role, name) is true."""
    granted = set()
    for name in names:
        cur.execute(query, (f"gsn_{role}", name))
        if cur.fetchone()[0]:
            granted.add(name)
    return granted


@pytest.mark.parametrize("role", ["migrate", "web", "worker", "engine"])
def test_audit_log_privileges_match_the_specification_exactly(connect, role):
    expected = EXPECTED[role]
    with connect("web").cursor() as cur:  # any role may ask the catalog about another role
        table = _granted(
            cur, "SELECT has_table_privilege(%s, 'public.audit_log', %s)", role, TABLE_PRIVILEGES
        )
        sequence = _granted(
            cur,
            "SELECT has_sequence_privilege(%s, 'public.audit_log_id_seq', %s)",
            role,
            SEQUENCE_PRIVILEGES,
        )
        columns = {}
        for privilege in ("INSERT", "SELECT", "UPDATE"):
            columns[privilege] = _granted(
                cur,
                "SELECT has_column_privilege(%s, 'public.audit_log', %s, '" + privilege + "')",
                role,
                COLUMNS,
            )
    assert table == expected["table"], "table-level privileges"
    assert columns["INSERT"] == expected["insert_columns"], "column-level INSERT"
    assert columns["SELECT"] == expected["select_columns"], "column-level SELECT"
    assert columns["UPDATE"] == expected["update_columns"], "column-level UPDATE"
    assert sequence == expected["sequence"], "sequence privileges"


@pytest.mark.parametrize(
    "role, can_create",
    [("migrate", True), ("web", False), ("worker", False), ("engine", False)],
)
def test_only_the_owner_can_create_objects_in_the_public_schema(connect, role, can_create):
    with connect("web").cursor() as cur:
        cur.execute("SELECT has_schema_privilege(%s, 'public', 'CREATE')", (f"gsn_{role}",))
        assert cur.fetchone()[0] is can_create
        cur.execute("SELECT has_schema_privilege(%s, 'public', 'USAGE')", (f"gsn_{role}",))
        assert cur.fetchone()[0] is True
        # The DATABASE belongs to the superuser (db/init/20-guard.sh, spec section 8.3), so no login
        # role, gsn_migrate included, may create schemas or temporary objects in it.
        cur.execute(
            "SELECT has_database_privilege(%s, 'gsn', 'CREATE'), has_database_privilege(%s, 'gsn', 'TEMPORARY'), "
            "has_database_privilege(%s, 'gsn', 'CONNECT')",
            (f"gsn_{role}",) * 3,
        )
        assert cur.fetchone() == (False, False, True)


def test_the_database_is_owned_by_the_superuser_and_the_public_schema_by_migrate(connect):
    # An owner can re-grant itself anything, so merely revoking CREATE from gsn_migrate would not hold:
    # the database itself moved to the superuser. Only the public schema stays with gsn_migrate.
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT r.rolsuper FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba "
            "WHERE d.datname = current_database()"
        )
        assert cur.fetchone()[0] is True
        cur.execute("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'public'")
        assert cur.fetchone()[0] == "gsn_migrate"
        # no ACL entry for PUBLIC (grantee 0) on the database: neither CONNECT nor TEMPORARY
        cur.execute(
            "SELECT count(*) FROM aclexplode((SELECT datacl FROM pg_database WHERE datname = current_database())) "
            "WHERE grantee = 0"
        )
        assert cur.fetchone()[0] == 0


def test_public_has_no_rights_on_audit_log(connect):
    with connect("web").cursor() as cur:
        # PUBLIC is not a role: look at the ACL itself for an entry whose grantee is 0.
        cur.execute(
            "SELECT count(*) FROM aclexplode((SELECT relacl FROM pg_class WHERE oid = 'public.audit_log'::regclass)) "
            "WHERE grantee = 0"
        )
        assert cur.fetchone()[0] == 0


# --- migration 0002: release tables, views, sequences and functions (spec section 3.1) ------------

RELEASE_TABLES = [
    "services",
    "records",
    "record_versions",
    "releases",
    "release_members",
    "release_services",
    "active_release",
    "publications",
]
ENGINE_VIEWS = ["engine_active_release", "engine_active_services", "engine_active_versions"]
RELEASE_SEQUENCES = [
    "record_versions_version_id_seq",
    "releases_release_id_seq",
    "publications_publication_id_seq",
]
FUNCTIONS = {  # SECURITY DEFINER functions: signature -> the roles allowed to execute it (the owner always may)
    "public.publish_release(bigint, text)": {"worker", "migrate"},
    "public.rollback_release(text)": {"worker", "migrate"},
    # migration 0003: the verifier is called by the worker (`gsn audit verify`) and may be by the web
    # service; the chain trigger function runs only as a trigger, whoever inserts, so nobody calls it.
    "public.audit_verify()": {"worker", "web", "migrate"},
    "public.audit_log_chain()": {"migrate"},
    # the verification core takes any table shaped like audit_log, so only the owner may call it
    # (audit_verify() calls it with the real log and head; the tests call it on scratch copies)
    "public.audit_verify_rows(regclass, bigint, bytea)": {"migrate"},
}
TRIGGER_FUNCTIONS = [
    "public.gsn_reject_change()",
    "public.releases_guard()",
    "public.release_children_guard()",
    "public.active_release_guard()",
]

# Table-level privileges of gsn_worker (it builds drafts; it never writes active_release or
# publications, which only publish_release / rollback_release change). INSERT is table-level only
# where every column may be set by the caller; on the other tables it is granted per column, leaving
# out the identity column and created_at (like audit_log's id and at): the caller cannot choose a
# sequence value or backdate a row.
WORKER_TABLE_PRIVILEGES = {
    "services": {"SELECT", "INSERT"},
    "records": {"SELECT"},
    "record_versions": {"SELECT"},
    "releases": {"SELECT"},
    "release_members": {"SELECT", "INSERT", "DELETE"},  # DELETE only while the release is a draft (trigger)
    "release_services": {"SELECT", "INSERT"},
    "active_release": {"SELECT"},
    "publications": {"SELECT"},
}
WORKER_UPDATE_COLUMNS = {"releases": {"status", "gate_report", "override_reason"}}
WORKER_INSERT_EXCLUDED = {  # columns the worker may NOT name in an INSERT (tables it may insert into at all)
    "services": set(),
    "records": {"created_at"},
    "record_versions": {"version_id", "created_at"},
    "releases": {"release_id", "created_at"},
    "release_services": set(),
    "release_members": set(),
}


def _columns(cur, table):
    cur.execute(
        "SELECT attname FROM pg_attribute WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped",
        (f"public.{table}",),
    )
    return [row[0] for row in cur.fetchall()]


@pytest.mark.parametrize("role", ["migrate", "web", "worker", "engine"])
def test_release_table_privileges_match_the_specification_exactly(connect, role):
    with connect("web").cursor() as cur:
        for table in RELEASE_TABLES:
            privileges = _granted(
                cur, f"SELECT has_table_privilege(%s, 'public.{table}', %s)", role, TABLE_PRIVILEGES
            )
            columns = _columns(cur, table)
            updatable = _granted(
                cur, f"SELECT has_column_privilege(%s, 'public.{table}', %s, 'UPDATE')", role, columns
            )
            selectable = _granted(
                cur, f"SELECT has_column_privilege(%s, 'public.{table}', %s, 'SELECT')", role, columns
            )
            insertable = _granted(
                cur, f"SELECT has_column_privilege(%s, 'public.{table}', %s, 'INSERT')", role, columns
            )
            if role == "migrate":
                expected, expected_update, expected_select = set(TABLE_PRIVILEGES), set(columns), set(columns)
                expected_insert = set(columns)
            elif role == "worker":
                expected = WORKER_TABLE_PRIVILEGES[table]
                expected_update = WORKER_UPDATE_COLUMNS.get(table, set())
                expected_select = set(columns)
                excluded = WORKER_INSERT_EXCLUDED.get(table)
                expected_insert = set() if excluded is None else set(columns) - excluded
            else:  # web gets nothing new; engine reads the views only
                expected, expected_update, expected_select = set(), set(), set()
                expected_insert = set()
            assert privileges == expected, f"{role} table privileges on {table}"
            assert updatable == expected_update, f"{role} column UPDATE on {table}"
            assert selectable == expected_select, f"{role} column SELECT on {table}"
            assert insertable == expected_insert, f"{role} column INSERT on {table}"


@pytest.mark.parametrize("role", ["web", "worker", "engine"])
def test_engine_views_are_readable_by_the_engine_only(connect, role):
    with connect("web").cursor() as cur:
        for view in ENGINE_VIEWS:
            privileges = _granted(
                cur, f"SELECT has_table_privilege(%s, 'public.{view}', %s)", role, TABLE_PRIVILEGES
            )
            assert privileges == ({"SELECT"} if role == "engine" else set()), f"{role} on {view}"


@pytest.mark.parametrize("role", ["web", "worker", "engine"])
def test_no_role_but_the_owner_can_use_the_new_sequences(connect, role):
    # The identity columns draw from their sequences without a USAGE grant, so nobody needs one.
    with connect("web").cursor() as cur:
        for sequence in RELEASE_SEQUENCES:
            granted = _granted(
                cur, f"SELECT has_sequence_privilege(%s, 'public.{sequence}', %s)", role, SEQUENCE_PRIVILEGES
            )
            assert granted == set(), f"{role} on {sequence}"


@pytest.mark.parametrize("role", ["migrate", "web", "worker", "engine"])
def test_security_definer_functions_are_executable_by_the_named_roles_only(connect, role):
    with connect("web").cursor() as cur:
        for signature, allowed in FUNCTIONS.items():
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (f"gsn_{role}", signature))
            assert cur.fetchone()[0] is (role in allowed), f"{role} on {signature}"


def test_public_has_no_rights_on_the_new_objects(connect):
    with connect("web").cursor() as cur:
        # PUBLIC is not a role: look for an ACL entry whose grantee is 0.
        for name in RELEASE_TABLES + ENGINE_VIEWS:
            cur.execute(
                "SELECT count(*) FROM aclexplode((SELECT relacl FROM pg_class WHERE oid = %s::regclass)) "
                "WHERE grantee = 0",
                (f"public.{name}",),
            )
            assert cur.fetchone()[0] == 0, name
        for signature in list(FUNCTIONS) + TRIGGER_FUNCTIONS:
            cur.execute(
                "SELECT count(*) FROM aclexplode((SELECT proacl FROM pg_proc WHERE oid = %s::regprocedure)) "
                "WHERE grantee = 0",
                (signature,),
            )
            assert cur.fetchone()[0] == 0, signature


def test_new_objects_are_owned_by_the_migrate_role(connect):
    with connect("web").cursor() as cur:
        for name in RELEASE_TABLES + ENGINE_VIEWS + RELEASE_SEQUENCES:
            cur.execute("SELECT relowner::regrole::text FROM pg_class WHERE oid = %s::regclass", (f"public.{name}",))
            assert cur.fetchone()[0] == "gsn_migrate", name
        for signature in list(FUNCTIONS) + TRIGGER_FUNCTIONS:
            cur.execute("SELECT proowner::regrole::text FROM pg_proc WHERE oid = %s::regprocedure", (signature,))
            assert cur.fetchone()[0] == "gsn_migrate", signature


def test_security_definer_functions_pin_their_search_path(connect):
    # The functions run with the owner's rights, so a caller must not be able to redirect the names
    # they use: search_path is pinned for the function, whatever the caller's session says. pg_temp is
    # listed explicitly and LAST; left out, PostgreSQL searches it FIRST for relation and type names,
    # so a caller could shadow them with look-alike temporary objects (test_releases_schema.py plays
    # that attack against publish_release and rollback_release, test_audit_chain.py against the audit
    # chain trigger and audit_verify).
    with connect("web").cursor() as cur:
        for signature in FUNCTIONS:
            cur.execute("SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (signature,))
            definer, config = cur.fetchone()
            assert definer is True, signature
            assert config == ["search_path=pg_catalog, pg_temp"], signature
        # The guards are plain functions that run with the caller's rights (inside publish/rollback,
        # with the owner's); their search_path is pinned the same way.
        for signature in TRIGGER_FUNCTIONS:
            cur.execute("SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (signature,))
            assert cur.fetchone() == (False, ["search_path=pg_catalog, pg_temp"]), signature


# --- migration 0003: the audit hash chain (spec section 8.2) -----------------------------------------

CHAIN_HEAD_COLUMNS = ["singleton", "last_seq", "last_hash"]


@pytest.mark.parametrize("role", ["migrate", "web", "worker", "engine"])
def test_the_audit_chain_head_belongs_to_the_owner_alone(connect, role):
    # The chain trigger and audit_verify() are SECURITY DEFINER functions of gsn_migrate; no login role
    # needs, or gets, any right on the head table (not even SELECT: the worker verifies without reading).
    with connect("web").cursor() as cur:
        privileges = _granted(
            cur, "SELECT has_table_privilege(%s, 'public.audit_chain', %s)", role, TABLE_PRIVILEGES
        )
        columns = {
            privilege: _granted(
                cur,
                "SELECT has_column_privilege(%s, 'public.audit_chain', %s, '" + privilege + "')",
                role,
                CHAIN_HEAD_COLUMNS,
            )
            for privilege in ("SELECT", "INSERT", "UPDATE")
        }
    if role == "migrate":
        assert privileges == set(TABLE_PRIVILEGES)
        assert all(granted == set(CHAIN_HEAD_COLUMNS) for granted in columns.values())
    else:
        assert privileges == set(), f"{role} table privileges on audit_chain"
        assert columns == {"SELECT": set(), "INSERT": set(), "UPDATE": set()}, f"{role} column privileges"


def test_the_audit_chain_head_is_owned_by_the_migrate_role_and_closed_to_public(connect):
    with connect("web").cursor() as cur:
        cur.execute("SELECT relowner::regrole::text FROM pg_class WHERE oid = 'public.audit_chain'::regclass")
        assert cur.fetchone()[0] == "gsn_migrate"
        cur.execute(
            "SELECT count(*) FROM aclexplode((SELECT relacl FROM pg_class WHERE oid = 'public.audit_chain'::regclass)) "
            "WHERE grantee = 0"
        )
        assert cur.fetchone()[0] == 0


def test_no_one_but_the_owner_holds_a_right_on_the_audit_id_sequence(connect):
    # Not PUBLIC, not a login role: the identity column needs no grant (see test_audit_chain.py).
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT a.grantee::regrole::text FROM aclexplode((SELECT relacl FROM pg_class "
            "WHERE oid = 'public.audit_log_id_seq'::regclass)) AS a WHERE a.grantee <> 'gsn_migrate'::regrole"
        )
        assert cur.fetchall() == []
