"""The exact privilege matrix of the four roles, read from the system catalog.

Behavioural tests elsewhere try individual statements; this one compares the complete set of
privileges against the specification (spec section 6.1), so an accidental extra GRANT (or a missing
one) in any migration fails here even if no other test happens to try it.

Expected matrix for ``audit_log`` (``at`` and ``id`` are never insertable by a caller):

    role         table privileges                columns INSERT         columns SELECT   sequence
    gsn_migrate  all (it is the owner)           all                    all              all
    gsn_web      SELECT                          actor action target    all              USAGE
                                                 details
    gsn_worker   none                            actor action target    none             USAGE
                                                 details
    gsn_engine   none                            none                   none             none

The owner's UPDATE/DELETE/TRUNCATE are still blocked, by the triggers (see test_database.py).

The release tables, views, sequences and functions of migration 0002 (spec section 3.1) are checked
further down in the same way: gsn_worker writes drafts, gsn_engine reads three views, gsn_web gets
nothing new, and gsn_migrate owns everything.
"""
import pytest

TABLE_PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"]
SEQUENCE_PRIVILEGES = ["USAGE", "SELECT", "UPDATE"]
COLUMNS = ["id", "at", "actor", "action", "target", "details"]
CALLER_COLUMNS = ["actor", "action", "target", "details"]

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
        "sequence": {"USAGE"},
    },
    "worker": {
        "table": set(),
        "insert_columns": set(CALLER_COLUMNS),
        "select_columns": set(),
        "update_columns": set(),
        "sequence": {"USAGE"},
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
        cur.execute("SELECT has_database_privilege(%s, 'gsn', 'CREATE')", (f"gsn_{role}",))
        assert cur.fetchone()[0] is (role == "migrate")  # CREATE on the database: its owner only


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
FUNCTIONS = {  # signature -> the roles allowed to execute it (the owner always may)
    "public.publish_release(bigint, text)": {"worker", "migrate"},
    "public.rollback_release(text)": {"worker", "migrate"},
}
TRIGGER_FUNCTIONS = [
    "public.gsn_reject_change()",
    "public.releases_guard()",
    "public.release_children_guard()",
    "public.active_release_guard()",
]

# Table-level privileges of gsn_worker (it builds drafts; it never writes active_release or
# publications, which only publish_release / rollback_release change).
WORKER_TABLE_PRIVILEGES = {
    "services": {"SELECT", "INSERT"},
    "records": {"SELECT", "INSERT"},
    "record_versions": {"SELECT", "INSERT"},
    "releases": {"SELECT", "INSERT"},
    "release_members": {"SELECT", "INSERT", "DELETE"},  # DELETE only while the release is a draft (trigger)
    "release_services": {"SELECT", "INSERT"},
    "active_release": {"SELECT"},
    "publications": {"SELECT"},
}
WORKER_UPDATE_COLUMNS = {"releases": {"status", "gate_report", "override_reason"}}


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
            if role == "migrate":
                expected, expected_update, expected_select = set(TABLE_PRIVILEGES), set(columns), set(columns)
            elif role == "worker":
                expected = WORKER_TABLE_PRIVILEGES[table]
                expected_update = WORKER_UPDATE_COLUMNS.get(table, set())
                expected_select = set(columns)
            else:  # web gets nothing new; engine reads the views only
                expected, expected_update, expected_select = set(), set(), set()
            assert privileges == expected, f"{role} table privileges on {table}"
            assert updatable == expected_update, f"{role} column UPDATE on {table}"
            assert selectable == expected_select, f"{role} column SELECT on {table}"


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
def test_publish_and_rollback_are_executable_by_the_worker_only(connect, role):
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


def test_publish_and_rollback_are_security_definer_with_a_pinned_search_path(connect):
    # The functions run with the owner's rights, so a caller must not be able to redirect the names
    # they use: search_path is pinned for the function, whatever the caller's session says.
    with connect("web").cursor() as cur:
        for signature in FUNCTIONS:
            cur.execute("SELECT prosecdef, proconfig FROM pg_proc WHERE oid = %s::regprocedure", (signature,))
            definer, config = cur.fetchone()
            assert definer is True, signature
            assert config == ["search_path=pg_catalog, public"], signature
        for signature in TRIGGER_FUNCTIONS:  # the guards are plain functions: they run with the caller's rights
            cur.execute("SELECT prosecdef FROM pg_proc WHERE oid = %s::regprocedure", (signature,))
            assert cur.fetchone()[0] is False, signature
