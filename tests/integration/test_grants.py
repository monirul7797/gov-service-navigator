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
