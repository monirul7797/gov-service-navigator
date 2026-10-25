"""The exact privileges of the four login roles and of PUBLIC, read from the system catalog.

Behavioural tests elsewhere try individual statements; this file compares the complete set of
privileges with the specification (spec sections 6.1, 8.2 and 8.3), so an accidental extra GRANT (or a
missing one) fails here even if no other test happens to try it.

WHAT IS COVERED, exactly:

* every table, view and sequence in the ``public`` schema, found by LISTING THE CATALOG (not from a list
  of names written here): the object-level privileges (the eight table privileges; USAGE, SELECT and
  UPDATE for a sequence) and the column-level privileges (SELECT, INSERT, UPDATE, REFERENCES) of
  ``gsn_migrate``, ``gsn_web``, ``gsn_worker``, ``gsn_engine`` and of PUBLIC;
* EXECUTE on every function in ``public`` (functions that belong to an extension, here pgvector, are
  left out), for the same five principals;
* all of that against ONE table of expectations (``EXPECTED`` below). An object that is not in the table
  is expected to be closed to everybody but its owner, so a table added by a later migration with a
  stray GRANT fails here. An object in the table that does not exist also fails;
* that the owner of all those objects is ``gsn_migrate`` (which therefore holds every privilege on them;
  it is added to the expectations automatically);
* that the ACL of every table, view and function in ``public``, of the ``public`` schema and of the database
  is written down explicitly (a NULL ACL means "the defaults", which for a function include EXECUTE for
  PUBLIC; sequences and the migration runner's own table are the exceptions) and has no entry for PUBLIC;
* that every function in ``public`` pins ``search_path = pg_catalog, pg_temp`` (and which ones are
  SECURITY DEFINER);
* the rights on the database and on the ``public`` schema (spec section 8.3) and the owners of both;
* that the cluster has no login role besides the four (a fifth would escape the matrix).

NOT covered here: the ``gsn_guard`` schema (test_guard.py), rights on types or large objects, default
privileges for objects created later, and what a role can actually DO (the behavioural tests in
test_database.py, test_guard.py, test_releases_schema.py, test_audit_chain.py and test_store.py).

The comparison lives in ``find_deviations`` / ``find_acl_problems``, which return a list of sentences. The
real database must produce an empty list, and the last tests here break a rolled-back copy of it on purpose
(an extra GRANT, a missing one, a table or function nobody listed) and check that the same functions
notice, so the guard itself is proven to bite.

Expected matrix for the two oldest objects (``id``, ``at`` and the four hash-chain columns added by
migration 0003 are never insertable by a caller; the triggers set them):

    audit_log     table privileges     columns INSERT         columns SELECT   sequence
    gsn_migrate   all (it is the owner) all                    all              all
    gsn_web       SELECT                actor action target    all              none
                                        details
    gsn_worker    none                  actor action target    none             none
                                        details
    gsn_engine    none                  none                   none             none

(``audit_log_id_seq`` needs no grant for web and worker: the identity column draws from it without one;
migration 0003 revoked the USAGE that 0001 had granted.) The owner's UPDATE/DELETE/TRUNCATE are still
blocked, by the triggers (see test_database.py).
"""
from contextlib import contextmanager

import pytest

TABLE_PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"]
COLUMN_PRIVILEGES = ["SELECT", "INSERT", "UPDATE", "REFERENCES"]
SEQUENCE_PRIVILEGES = ["USAGE", "SELECT", "UPDATE"]
LOGIN_ROLES = ["migrate", "web", "worker", "engine"]
PRINCIPALS = [f"gsn_{role}" for role in LOGIN_ROLES] + ["public"]  # "public" is how has_*_privilege names PUBLIC

CALLER_COLUMNS = ["actor", "action", "target", "details"]

# --- the one table of expectations ---------------------------------------------------------------------


class EveryColumnBut(frozenset):
    """A column set meaning: all columns of the relation except these (resolved against the catalog)."""


def every_column_but(*names):
    return EveryColumnBut(names)


def grant(*privileges, **column_grants):
    """What one role holds on one object.

    ``privileges``: object-level ones (table/view/sequence privileges, or ``"EXECUTE"``). ``SELECT=`` /
    ``INSERT=`` / ``UPDATE=`` / ``REFERENCES=``: columns granted one by one, IN ADDITION to what an
    object-level privilege implies for every column.
    """
    assert set(column_grants) <= set(COLUMN_PRIVILEGES), column_grants
    return {"privileges": set(privileges), "columns": column_grants}


NOTHING = grant()
OWNER_GRANT = {  # what the owner of an object holds on it, by kind
    "table": grant(*TABLE_PRIVILEGES),
    "view": grant(*TABLE_PRIVILEGES),
    "sequence": grant(*SEQUENCE_PRIVILEGES),
    "function": grant("EXECUTE"),
}
EXPECTED = {
    # (kind, name) -> {role: grant}. A role that is not named holds nothing; gsn_migrate (the owner) is implicit.
    # --- migrations 0001 and 0003: the audit log and its chain head
    ("table", "audit_log"): {
        "web": grant("SELECT", INSERT=CALLER_COLUMNS),
        "worker": grant(INSERT=CALLER_COLUMNS),
    },
    ("table", "audit_chain"): {},  # no login role touches the head: the chain trigger and audit_verify() are definers
    ("sequence", "audit_log_id_seq"): {},  # identity columns draw from their sequences without a USAGE grant
    ("function", "public.audit_log_reject_change()"): {},
    ("function", "public.audit_log_chain()"): {},  # runs only as a trigger, whoever inserts
    # the verifier is called by the worker (`gsn audit verify`) and may be by the web service
    ("function", "public.audit_verify()"): {"worker": grant("EXECUTE"), "web": grant("EXECUTE")},
    # the verification core takes any table shaped like audit_log, so only the owner may call it
    ("function", "public.audit_verify_rows(regclass, bigint, bytea)"): {},
    # --- migration 0002: the release tables (spec section 3.1). The worker builds drafts; it never writes
    # active_release or publications, which only publish_release / rollback_release change. INSERT is
    # table-level only where every column may be set by the caller; elsewhere it is granted per column,
    # leaving out the identity column and created_at: the caller cannot choose a sequence value or backdate.
    ("table", "services"): {"worker": grant("SELECT", "INSERT")},
    ("table", "records"): {"worker": grant("SELECT", INSERT=every_column_but("created_at"))},
    ("table", "record_versions"): {
        "worker": grant("SELECT", INSERT=every_column_but("version_id", "created_at")),
    },
    ("table", "releases"): {
        "worker": grant(
            "SELECT",
            INSERT=every_column_but("release_id", "created_at"),
            UPDATE=["status", "gate_report", "override_reason"],
        ),
    },
    ("table", "release_members"): {"worker": grant("SELECT", "INSERT", "DELETE")},  # DELETE only while a draft (trigger)
    ("table", "release_services"): {"worker": grant("SELECT", "INSERT")},
    ("table", "active_release"): {"worker": grant("SELECT")},
    ("table", "publications"): {"worker": grant("SELECT")},
    # the engine reads the three views and nothing behind them: only the active release is visible
    ("view", "engine_active_release"): {"engine": grant("SELECT")},
    ("view", "engine_active_services"): {"engine": grant("SELECT")},
    ("view", "engine_active_versions"): {"engine": grant("SELECT")},
    ("sequence", "record_versions_version_id_seq"): {},
    ("sequence", "releases_release_id_seq"): {},
    ("sequence", "publications_publication_id_seq"): {},
    ("function", "public.publish_release(bigint, text)"): {"worker": grant("EXECUTE")},
    ("function", "public.rollback_release(text)"): {"worker": grant("EXECUTE")},
    ("function", "public.gsn_reject_change()"): {},
    ("function", "public.releases_guard()"): {},
    ("function", "public.release_children_guard()"): {},
    ("function", "public.active_release_guard()"): {},
    # --- created by the migration runner (sqlx), not by a migration
    ("table", "_sqlx_migrations"): {},
}
# Functions that run with their owner's rights; every other function in `public` is SECURITY INVOKER.
# (The guards are plain functions that run with the caller's rights, and inside publish/rollback with the owner's.)
DEFINER_FUNCTIONS = {
    "public.publish_release(bigint, text)",
    "public.rollback_release(text)",
    "public.audit_verify()",
    "public.audit_log_chain()",
    "public.audit_verify_rows(regclass, bigint, bytea)",
}
PINNED_SEARCH_PATH = ["search_path=pg_catalog, pg_temp"]
# Tables whose ACL is legitimately left at its default (NULL, owner only): created by the migration runner.
DEFAULT_ACL_TABLES = {"_sqlx_migrations"}

RELATION_KINDS = {"r": "table", "p": "table", "f": "table", "v": "view", "m": "view", "S": "sequence"}
_RELATION_FILTER = (
    "c.relnamespace = 'public'::regnamespace AND c.relkind::text = ANY(%(kinds)s) AND NOT EXISTS ("
    "SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass AND d.objid = c.oid AND d.deptype = 'e')"
)
_FUNCTION_FILTER = (  # functions of an extension (pgvector) are not ours to judge
    "p.pronamespace = 'public'::regnamespace AND NOT EXISTS ("
    "SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_proc'::regclass AND d.objid = p.oid AND d.deptype = 'e')"
)


def _resolve(columns, all_columns):
    if isinstance(columns, EveryColumnBut):
        return set(all_columns) - columns
    return set(columns)


def _expected_grant(kind, name, role):
    if role == "migrate":
        return OWNER_GRANT[kind]
    return EXPECTED.get((kind, name), {}).get(role, NOTHING)


def find_deviations(cur):
    """Every difference between the privileges in the catalog and ``EXPECTED``, as sentences (``[]``: none)."""
    problems = []
    non_sequence_kinds = [k for k, kind in RELATION_KINDS.items() if kind != "sequence"]

    cur.execute(
        f"SELECT c.relname, c.relkind::text FROM pg_class c WHERE {_RELATION_FILTER}",
        {"kinds": list(RELATION_KINDS)},
    )
    relations = {name: RELATION_KINDS[relkind] for name, relkind in cur.fetchall()}
    cur.execute(
        "SELECT c.relname, a.attname FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid "
        f"AND a.attnum > 0 AND NOT a.attisdropped WHERE {_RELATION_FILTER}",
        {"kinds": non_sequence_kinds},
    )
    columns = {}
    for name, column in cur.fetchall():
        columns.setdefault(name, []).append(column)

    # object-level privileges that ARE held
    held = {}  # (name, principal) -> set of privileges
    for has_privilege, privileges, kinds in (
        ("has_table_privilege", TABLE_PRIVILEGES, non_sequence_kinds),
        ("has_sequence_privilege", SEQUENCE_PRIVILEGES, ["S"]),
    ):
        cur.execute(
            "SELECT c.relname, r.principal, p.privilege FROM pg_class c "
            "CROSS JOIN unnest(%(principals)s::text[]) AS r(principal) "
            "CROSS JOIN unnest(%(privileges)s::text[]) AS p(privilege) "
            f"WHERE {_RELATION_FILTER} AND {has_privilege}(r.principal::name, c.oid, p.privilege)",
            {"kinds": kinds, "principals": PRINCIPALS, "privileges": privileges},
        )
        for name, principal, privilege in cur.fetchall():
            held.setdefault((name, principal), set()).add(privilege)
    # column-level privileges that ARE held (an object-level privilege counts for every column)
    held_columns = {}  # (name, principal, privilege) -> set of columns
    cur.execute(
        "SELECT c.relname, a.attname, r.principal, p.privilege FROM pg_class c "
        "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
        "CROSS JOIN unnest(%(principals)s::text[]) AS r(principal) "
        "CROSS JOIN unnest(%(privileges)s::text[]) AS p(privilege) "
        f"WHERE {_RELATION_FILTER} AND has_column_privilege(r.principal::name, c.oid, a.attnum, p.privilege)",
        {"kinds": non_sequence_kinds, "principals": PRINCIPALS, "privileges": COLUMN_PRIVILEGES},
    )
    for name, column, principal, privilege in cur.fetchall():
        held_columns.setdefault((name, principal, privilege), set()).add(column)

    for (kind, name) in EXPECTED:
        if kind != "function" and relations.get(name) != kind:
            problems.append(f"expected {kind} {name} is not in the public schema")
    for name, kind in sorted(relations.items()):
        for principal in PRINCIPALS:
            role = principal.removeprefix("gsn_")
            wanted = _expected_grant(kind, name, role)
            expected_object = wanted["privileges"]
            actual_object = held.get((name, principal), set())
            for privilege in sorted(actual_object - expected_object):
                problems.append(f"{principal} holds {privilege} on {kind} {name}, which is not expected")
            for privilege in sorted(expected_object - actual_object):
                problems.append(f"{principal} lacks {privilege} on {kind} {name}, which is expected")
            if kind == "sequence":
                continue
            for privilege in COLUMN_PRIVILEGES:
                if privilege in expected_object:
                    expected_columns = set(columns[name])
                else:
                    expected_columns = _resolve(wanted["columns"].get(privilege, ()), columns[name])
                actual_columns = held_columns.get((name, principal, privilege), set())
                for column in sorted(actual_columns - expected_columns):
                    problems.append(f"{principal} holds column {privilege} on {name}.{column}, which is not expected")
                for column in sorted(expected_columns - actual_columns):
                    problems.append(f"{principal} lacks column {privilege} on {name}.{column}, which is expected")

    # EXECUTE on functions
    cur.execute(
        "SELECT p.oid::int8, (pg_identify_object('pg_proc'::regclass, p.oid, 0)).identity FROM pg_proc p "
        f"WHERE {_FUNCTION_FILTER}"
    )
    functions = {oid: identity for oid, identity in cur.fetchall()}
    expected_by_oid = {}
    for (kind, signature), roles in EXPECTED.items():
        if kind != "function":
            continue
        cur.execute("SELECT to_regprocedure(%s)::oid::int8", (signature,))
        oid = cur.fetchone()[0]
        if oid is None or oid not in functions:
            problems.append(f"expected function {signature} is not in the public schema")
        else:
            expected_by_oid[oid] = (signature, roles)
    cur.execute(
        "SELECT p.oid::int8, r.principal FROM pg_proc p CROSS JOIN unnest(%(principals)s::text[]) AS r(principal) "
        f"WHERE {_FUNCTION_FILTER} AND has_function_privilege(r.principal::name, p.oid, 'EXECUTE')",
        {"principals": PRINCIPALS},
    )
    executable = {}
    for oid, principal in cur.fetchall():
        executable.setdefault(oid, set()).add(principal)
    for oid, identity in sorted(functions.items(), key=lambda item: item[1]):
        signature, roles = expected_by_oid.get(oid, (identity, {}))
        for principal in PRINCIPALS:
            role = principal.removeprefix("gsn_")
            wanted = role == "migrate" or role in roles
            actual = principal in executable.get(oid, set())
            if actual and not wanted:
                problems.append(f"{principal} can EXECUTE function {signature}, which is not expected")
            if wanted and not actual:
                problems.append(f"{principal} cannot EXECUTE function {signature}, which is expected")
    return problems


def find_acl_problems(cur):
    """Tables, views, functions, the ``public`` schema and the database with a default (NULL) ACL or an entry for PUBLIC."""
    problems = []
    objects = []  # (description, acl_is_null, entries_for_public, may_be_null)
    cur.execute(
        "SELECT c.relkind::text, c.relname, c.relacl IS NULL, "
        "(SELECT count(*) FROM aclexplode(c.relacl) AS a WHERE a.grantee = 0) FROM pg_class c "
        f"WHERE {_RELATION_FILTER}",
        {"kinds": list(RELATION_KINDS)},
    )
    for relkind, name, is_null, public_entries in cur.fetchall():
        kind = RELATION_KINDS[relkind]
        # a sequence is closed by default (owner only), and the effect check in find_deviations covers it
        objects.append((f"{kind} {name}", is_null, public_entries, kind == "sequence" or name in DEFAULT_ACL_TABLES))
    cur.execute(
        "SELECT (pg_identify_object('pg_proc'::regclass, p.oid, 0)).identity, p.proacl IS NULL, "
        f"(SELECT count(*) FROM aclexplode(p.proacl) AS a WHERE a.grantee = 0) FROM pg_proc p WHERE {_FUNCTION_FILTER}"
    )
    for identity, is_null, public_entries in cur.fetchall():
        objects.append((f"function {identity}", is_null, public_entries, False))
    cur.execute(
        "SELECT 'schema public', nspacl IS NULL, (SELECT count(*) FROM aclexplode(nspacl) AS a WHERE a.grantee = 0) "
        "FROM pg_namespace WHERE nspname = 'public'"
    )
    objects += [(*row, False) for row in cur.fetchall()]
    cur.execute(
        "SELECT 'database ' || datname, datacl IS NULL, "
        "(SELECT count(*) FROM aclexplode(datacl) AS a WHERE a.grantee = 0) FROM pg_database "
        "WHERE datname = current_database()"
    )
    objects += [(*row, False) for row in cur.fetchall()]
    for description, is_null, public_entries, may_be_null in objects:
        if is_null and not may_be_null:
            problems.append(f"{description} has a default (NULL) ACL: write it down with REVOKE ... FROM PUBLIC")
        if public_entries:
            problems.append(f"{description} has an ACL entry for PUBLIC")
    return problems


def find_function_setting_problems(cur):
    """Functions in ``public`` without the pinned ``search_path``, or whose SECURITY DEFINER flag is not as listed."""
    problems = []
    definer_oids = set()
    for signature in sorted(DEFINER_FUNCTIONS):
        cur.execute("SELECT to_regprocedure(%s)::oid::int8", (signature,))
        oid = cur.fetchone()[0]
        if oid is None:
            problems.append(f"expected SECURITY DEFINER function {signature} is not in the public schema")
        definer_oids.add(oid)
    cur.execute(
        "SELECT p.oid::int8, (pg_identify_object('pg_proc'::regclass, p.oid, 0)).identity, p.prosecdef, p.proconfig "
        f"FROM pg_proc p WHERE {_FUNCTION_FILTER} ORDER BY 2"
    )
    rows = cur.fetchall()
    if not rows:
        problems.append("no function found in the public schema: the listing is broken")
    for oid, identity, is_definer, config in rows:
        if config != PINNED_SEARCH_PATH:
            problems.append(f"function {identity} has settings {config}, expected {PINNED_SEARCH_PATH}")
        if is_definer != (oid in definer_oids):
            problems.append(f"function {identity} has SECURITY DEFINER = {is_definer}, expected {oid in definer_oids}")
    return problems


# --- the real database must match -------------------------------------------------------------------


def test_the_expectation_table_is_well_formed():
    for (kind, name), roles in EXPECTED.items():
        assert kind in OWNER_GRANT, (kind, name)
        assert set(roles) <= {"web", "worker", "engine"}, (name, "the owner is implicit; PUBLIC never holds anything")
        allowed = {
            "table": TABLE_PRIVILEGES, "view": TABLE_PRIVILEGES, "sequence": SEQUENCE_PRIVILEGES, "function": ["EXECUTE"],
        }[kind]
        for role, wanted in roles.items():
            assert wanted["privileges"] <= set(allowed), (name, role)
            assert not wanted["columns"] or kind in ("table", "view"), (name, role)


def test_no_role_holds_a_privilege_beyond_the_expectations(connect):
    with connect("web").cursor() as cur:  # any role may ask the catalog about another role
        assert find_deviations(cur) == []


def test_everything_in_public_belongs_to_the_migrate_role(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT 'relation ' || c.relname, pg_get_userbyid(c.relowner) FROM pg_class c "
            f"WHERE {_RELATION_FILTER}",
            {"kinds": list(RELATION_KINDS)},
        )
        owners = dict(cur.fetchall())
        cur.execute(
            "SELECT 'function ' || (pg_identify_object('pg_proc'::regclass, p.oid, 0)).identity, "
            f"pg_get_userbyid(p.proowner) FROM pg_proc p WHERE {_FUNCTION_FILTER}"
        )
        owners.update(cur.fetchall())
    assert len(owners) >= len(EXPECTED), "the catalog listing found fewer objects than the expectations name"
    assert {name: owner for name, owner in owners.items() if owner != "gsn_migrate"} == {}


def test_public_has_no_acl_entry_and_no_acl_is_left_at_its_default(connect):
    # PUBLIC is not a role: its rights are an ACL entry whose grantee is 0. Counting such entries in a NULL
    # ACL (aclexplode(NULL) returns no rows) would pass vacuously, though NULL means "the defaults", which
    # for a function include EXECUTE for PUBLIC. find_acl_problems reports a NULL ACL as a problem itself.
    with connect("web").cursor() as cur:
        assert find_acl_problems(cur) == []


def test_every_function_in_public_pins_its_search_path(connect):
    # The functions run with the owner's rights (or, for the guards, inside publish/rollback with them), so a
    # caller must not be able to redirect the names they use: search_path is pinned for the function,
    # whatever the caller's session says. pg_temp is listed explicitly and LAST; left out, PostgreSQL searches
    # it FIRST for relation and type names, so a caller could shadow them with look-alike temporary objects
    # (test_releases_schema.py plays that attack against publish_release and rollback_release,
    # test_audit_chain.py against the audit chain trigger and audit_verify). `public` is never listed:
    # gsn_migrate owns it and a look-alike function created there would win the call (test_guard.py).
    # That includes audit_log_reject_change() of the applied migration 0001, which migration 0004 pins.
    with connect("web").cursor() as cur:
        assert find_function_setting_problems(cur) == []


# --- the database and the public schema (spec section 8.3) -------------------------------------------


def test_the_database_belongs_to_the_superuser_and_the_public_schema_to_the_migrate_role(connect):
    # An owner can re-grant itself anything, so merely revoking CREATE from gsn_migrate would not hold: the
    # database itself moved to the superuser (db/init/20-guard.sh). Only the public schema stays with
    # gsn_migrate, so migrations still create their tables.
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT r.rolname, r.rolsuper FROM pg_database d JOIN pg_roles r ON r.oid = d.datdba "
            "WHERE d.datname = current_database()"
        )
        ((owner, superuser),) = cur.fetchall()
        assert superuser is True and owner not in PRINCIPALS
        cur.execute("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'public'")
        assert cur.fetchone()[0] == "gsn_migrate"


@pytest.mark.parametrize("role", LOGIN_ROLES)
def test_rights_of_each_login_role_on_the_database_and_the_schema(connect, role):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT has_schema_privilege(%(r)s, 'public', 'USAGE'), has_schema_privilege(%(r)s, 'public', 'CREATE')",
            {"r": f"gsn_{role}"},
        )
        assert cur.fetchone() == (True, role == "migrate")  # only the owner can create tables
        # The DATABASE belongs to the superuser, so no login role, gsn_migrate included, may create schemas
        # or temporary objects in it (test_guard.py tries both); all four still connect.
        cur.execute(
            "SELECT has_database_privilege(%(r)s, current_database(), 'CREATE'), "
            "has_database_privilege(%(r)s, current_database(), 'TEMPORARY'), "
            "has_database_privilege(%(r)s, current_database(), 'CONNECT'), "
            "has_database_privilege(%(r)s, 'postgres', 'CONNECT')",
            {"r": f"gsn_{role}"},
        )
        assert cur.fetchone() == (False, False, True, False)  # the postgres database is closed to them


def test_public_holds_nothing_on_the_database_or_the_schema(connect):
    with connect("web").cursor() as cur:
        cur.execute(
            "SELECT has_database_privilege('public', current_database(), 'CREATE'), "
            "has_database_privilege('public', current_database(), 'TEMPORARY'), "
            "has_database_privilege('public', current_database(), 'CONNECT'), "
            "has_database_privilege('public', 'postgres', 'CONNECT'), "
            "has_schema_privilege('public', 'public', 'USAGE'), has_schema_privilege('public', 'public', 'CREATE')"
        )
        assert cur.fetchone() == (False,) * 6
        # (the ACLs of the database and the schema are checked for a NULL ACL and a PUBLIC entry by
        # test_public_has_no_acl_entry_and_no_acl_is_left_at_its_default)


def test_the_cluster_has_no_login_role_besides_the_four(connect):
    # The matrix compares four roles and PUBLIC; a fifth ordinary role would hold rights it never looks at.
    with connect("web").cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles WHERE NOT rolsuper AND rolname !~ '^pg_' ORDER BY 1")
        assert [row[0] for row in cur.fetchall()] == sorted(f"gsn_{role}" for role in LOGIN_ROLES)


# --- the guard itself bites: the same functions on a deliberately broken, rolled-back copy ---------------


@contextmanager
def broken(connect, *statements):
    """A ``gsn_migrate`` cursor inside a transaction that is rolled back, after running ``statements``."""
    migrate = connect("migrate")
    with migrate.transaction(force_rollback=True):
        migrate.execute("SET LOCAL lock_timeout = '10s'")  # a lock held elsewhere fails the test instead of hanging it
        with migrate.cursor() as cur:
            assert find_deviations(cur) == [], "the copy was not clean before it was broken"
            assert find_acl_problems(cur) == []
            assert find_function_setting_problems(cur) == []
        for statement in statements:
            migrate.execute(statement)
        with migrate.cursor() as cur:
            yield cur


BREAKS = [
    # (id, statements, fragments that one reported problem must contain)
    ("engine-reads-the-record-versions", ["GRANT SELECT ON public.record_versions TO gsn_engine"],
     ["gsn_engine holds SELECT on table record_versions"]),
    ("a-column-the-worker-must-not-read", ["GRANT SELECT (target) ON public.audit_log TO gsn_worker"],
     ["gsn_worker holds column SELECT on audit_log.target"]),
    ("a-column-the-worker-must-not-update", ["GRANT UPDATE (note) ON public.releases TO gsn_worker"],
     ["gsn_worker holds column UPDATE on releases.note"]),
    ("worker-may-insert-into-the-audit-log-as-a-whole", ["GRANT INSERT ON public.audit_log TO gsn_worker"],
     ["gsn_worker holds INSERT on table audit_log"]),
    ("web-uses-a-sequence", ["GRANT USAGE ON SEQUENCE public.record_versions_version_id_seq TO gsn_web"],
     ["gsn_web holds USAGE on sequence record_versions_version_id_seq"]),
    ("web-reads-an-engine-view", ["GRANT SELECT ON public.engine_active_services TO gsn_web"],
     ["gsn_web holds SELECT on view engine_active_services"]),
    ("public-reads-the-chain-head", ["GRANT SELECT ON public.audit_chain TO PUBLIC"],
     ["public holds SELECT on table audit_chain"]),
    ("web-loses-its-read-of-the-audit-log", ["REVOKE SELECT ON public.audit_log FROM gsn_web"],
     ["gsn_web lacks SELECT on table audit_log"]),
    ("a-table-nobody-listed", ["CREATE TABLE public.itest_extra (id int)", "GRANT SELECT ON public.itest_extra TO gsn_web"],
     ["gsn_web holds SELECT on table itest_extra"]),
    ("a-function-nobody-listed", ["CREATE FUNCTION public.itest_leak() RETURNS int LANGUAGE sql AS 'SELECT 1'"],
     ["public can EXECUTE function public.itest_leak()"]),
]


@pytest.mark.parametrize(
    "statements, fragments",
    [pytest.param(statements, fragments, id=name) for name, statements, fragments in BREAKS],
)
def test_the_privilege_check_notices_a_deliberate_break(connect, statements, fragments):
    with broken(connect, *statements) as cur:
        problems = find_deviations(cur)
    assert [problem for problem in problems if all(fragment in problem for fragment in fragments)], problems


@pytest.mark.parametrize(
    "statements, fragment",
    [
        pytest.param(["GRANT SELECT ON public.audit_chain TO PUBLIC"], "table audit_chain has an ACL entry for PUBLIC",
                     id="entry-for-public"),
        # a table or function created without any REVOKE keeps the default ACL; for a function that includes EXECUTE for PUBLIC
        pytest.param(["CREATE TABLE public.itest_extra (id int)"], "table itest_extra has a default (NULL) ACL",
                     id="default-acl-table"),
        pytest.param(["CREATE FUNCTION public.itest_leak() RETURNS int LANGUAGE sql AS 'SELECT 1'"],
                     "function public.itest_leak() has a default (NULL) ACL", id="default-acl-function"),
    ],
)
def test_the_acl_check_notices_a_deliberate_break(connect, statements, fragment):
    with broken(connect, *statements) as cur:
        problems = find_acl_problems(cur)
    assert [problem for problem in problems if fragment in problem], problems


@pytest.mark.parametrize(
    "statement, fragment",
    [
        pytest.param("CREATE FUNCTION public.itest_unpinned() RETURNS int LANGUAGE sql AS 'SELECT 1'",
                     "function public.itest_unpinned() has settings None", id="no-setting"),
        pytest.param("CREATE FUNCTION public.itest_public_path() RETURNS int LANGUAGE sql SET search_path = public, pg_temp "
                     "AS 'SELECT 1'", "function public.itest_public_path() has settings ['search_path=public, pg_temp']",
                     id="public-in-the-path"),
        pytest.param("CREATE FUNCTION public.itest_no_pg_temp() RETURNS int LANGUAGE sql SET search_path = pg_catalog "
                     "AS 'SELECT 1'", "function public.itest_no_pg_temp() has settings ['search_path=pg_catalog']",
                     id="pg_temp-not-listed"),
        pytest.param("CREATE FUNCTION public.itest_definer() RETURNS int LANGUAGE sql SECURITY DEFINER "
                     "SET search_path = pg_catalog, pg_temp AS 'SELECT 1'",
                     "function public.itest_definer() has SECURITY DEFINER = True, expected False", id="unlisted-definer"),
    ],
)
def test_the_function_setting_check_notices_a_deliberate_break(connect, statement, fragment):
    with broken(connect, statement) as cur:
        problems = find_function_setting_problems(cur)
    assert [problem for problem in problems if fragment in problem], problems
