#!/bin/bash
# The guard watchdog and the database ownership that goes with it (spec section 8.1 and 8.3). Runs
# once, on an empty data volume, right after 10-roles.sh and as the same superuser. After changing
# this file, reset with: docker compose down -v
#
# Why this exists. The append-only and state-rule triggers of the release history and the audit log,
# and the functions behind them (publish, rollback, verify, the hash chain), are owned by gsn_migrate,
# which could therefore drop or disable them. Moving them to the superuser would break the migrations
# that create them, so instead the SUPERUSER owns a watchdog: event triggers that, after every schema
# change in this database, check a registry of protected objects and raise (which rolls the change
# back) when one of them was dropped, renamed, retyped, disabled or replaced. gsn_migrate may only
# ADD objects to the registry (gsn_guard.protect_table / protect_function, called by migration 0004
# and by later migrations for new guards); nobody can remove an entry. Changing a guard on purpose
# therefore needs the superuser (a documented break-glass step, see docs/DEVELOPMENT.md). The
# superuser itself is out of scope: it is reachable only from the server console.
#
# Step 1 (database postgres): the database changes owner to the superuser, because an owner can
# re-grant itself any privilege, so merely revoking CREATE from gsn_migrate would not hold. The schema
# `public` stays with gsn_migrate (10-roles.sh), so migrations still create tables. Nobody gets
# TEMPORARY (a role that may create temporary objects can shadow type and table names), and PUBLIC can
# no longer connect to the maintenance database `postgres`.
#
# Step 2 (database gsn): schema gsn_guard (superuser; USAGE for gsn_migrate only), the registry tables
# (no privileges for anybody but the superuser), the registry view that lets gsn_migrate LOOK at the
# registry, the two registration functions, and the event triggers.
#
# SECURITY DEFINER rule: every such function pins `search_path = pg_catalog, gsn_guard, pg_temp`
# (pg_temp explicitly LAST; unlisted, PostgreSQL searches it FIRST for relation and type names) and
# writes every relation and type name schema-qualified. They run with the superuser's rights, so none
# of them builds dynamic SQL, and the registration functions touch nothing but the registry.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner="$POSTGRES_USER" <<'SQL'
ALTER DATABASE gsn OWNER TO :"owner";
-- A change of owner rewrites every ACL entry that named the old owner (including the CONNECT that
-- 10-roles.sh granted to gsn_migrate), so the grants are stated again.
REVOKE ALL ON DATABASE gsn FROM PUBLIC;
GRANT CONNECT ON DATABASE gsn TO gsn_migrate, gsn_web, gsn_engine, gsn_worker;
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname gsn <<'SQL'
CREATE SCHEMA gsn_guard;
REVOKE ALL ON SCHEMA gsn_guard FROM PUBLIC;
GRANT USAGE ON SCHEMA gsn_guard TO gsn_migrate;

-- ---------------------------------------------------------------------------------------------
-- The registry. Names are stored as PostgreSQL spells them in pg_event_trigger_dropped_objects()
-- (schema-qualified), never as object ids, so the registry survives pg_dump / pg_restore.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE gsn_guard.protected_tables (
    table_name text  PRIMARY KEY,   -- schema-qualified, e.g. public.audit_log
    columns    jsonb NOT NULL       -- [{"name": ..., "type": ...}] as format_type() spells the type
);

CREATE TABLE gsn_guard.protected_triggers (
    table_name   text NOT NULL REFERENCES gsn_guard.protected_tables (table_name),
    trigger_name text NOT NULL,
    definition   text NOT NULL,     -- gsn_guard.trigger_definition(): what the trigger does, not just its name
    PRIMARY KEY (table_name, trigger_name)
);

CREATE TABLE gsn_guard.protected_functions (
    signature   text PRIMARY KEY,   -- schema-qualified, e.g. public.publish_release(bigint, text)
    fingerprint text NOT NULL       -- gsn_guard.function_fingerprint()
);

REVOKE ALL ON gsn_guard.protected_tables, gsn_guard.protected_triggers, gsn_guard.protected_functions FROM PUBLIC;

-- gsn_migrate may look at the registry (through this view, which runs with the superuser's rights),
-- never change it.
CREATE VIEW gsn_guard.registry AS
SELECT 'table'::pg_catalog.text AS kind, t.table_name AS name, NULL::pg_catalog.text AS parent,
       t.columns::pg_catalog.text AS detail
FROM gsn_guard.protected_tables AS t
UNION ALL
SELECT 'trigger', g.trigger_name, g.table_name, g.definition
FROM gsn_guard.protected_triggers AS g
UNION ALL
SELECT 'function', f.signature, NULL, f.fingerprint
FROM gsn_guard.protected_functions AS f;

REVOKE ALL ON gsn_guard.registry FROM PUBLIC;
GRANT SELECT ON gsn_guard.registry TO gsn_migrate;

-- ---------------------------------------------------------------------------------------------
-- What is recorded about a trigger and a function. One definition each, used by BOTH the
-- registration and the check, so the two always agree.
--
-- A trigger is recorded by its function, events and timing (tgtype), the columns of UPDATE OF, the
-- WHEN condition, its arguments and its deferral and transition-table settings: not by name alone,
-- because CREATE OR REPLACE TRIGGER keeps the name (and the enabled state) while it can make the
-- trigger do something else.
--
-- A function is recorded by its source, its settings (the pinned search_path), SECURITY DEFINER
-- flag, owner and volatility. (Its name and argument types are the registry key.)
-- ---------------------------------------------------------------------------------------------

CREATE FUNCTION gsn_guard.trigger_definition(p_trigger pg_catalog.oid) RETURNS pg_catalog.text
LANGUAGE sql
STABLE
SET search_path = pg_catalog, gsn_guard, pg_temp
AS $$
    SELECT pg_catalog.concat_ws('|',
        t.tgtype::pg_catalog.text,
        t.tgfoid::pg_catalog.regprocedure::pg_catalog.text,
        t.tgattr::pg_catalog.text,
        coalesce(pg_catalog.pg_get_expr(t.tgqual, t.tgrelid), ''),
        pg_catalog.encode(t.tgargs, 'hex'),
        t.tgdeferrable::pg_catalog.text,
        t.tginitdeferred::pg_catalog.text,
        coalesce(t.tgoldtable::pg_catalog.text, ''),
        coalesce(t.tgnewtable::pg_catalog.text, ''))
    FROM pg_catalog.pg_trigger AS t
    WHERE t.oid = p_trigger
$$;

CREATE FUNCTION gsn_guard.function_fingerprint(p_function pg_catalog.oid) RETURNS pg_catalog.text
LANGUAGE sql
STABLE
SET search_path = pg_catalog, gsn_guard, pg_temp
AS $$
    SELECT pg_catalog.md5(p.prosrc || coalesce(p.proconfig::pg_catalog.text, '') || p.prosecdef::pg_catalog.text
                          || p.proowner::pg_catalog.text || p.provolatile::pg_catalog.text)
    FROM pg_catalog.pg_proc AS p
    WHERE p.oid = p_function
$$;

-- ---------------------------------------------------------------------------------------------
-- Registration. Called by migrations as gsn_migrate; SECURITY DEFINER because only the superuser
-- may write the registry. There is no unregister.
-- ---------------------------------------------------------------------------------------------

CREATE FUNCTION gsn_guard.protect_table(p_table pg_catalog.text, p_triggers pg_catalog.text[])
RETURNS pg_catalog.void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, gsn_guard, pg_temp
AS $$
DECLARE
    v_oid         pg_catalog.oid;
    v_relkind     pg_catalog."char";
    v_persistence pg_catalog."char";
    v_name        pg_catalog.text;
    v_columns     pg_catalog.jsonb;
    v_trigger     pg_catalog.text;
    v_trigger_oid pg_catalog.oid;
    v_enabled     pg_catalog."char";
BEGIN
    IF p_table IS NULL OR pg_catalog.cardinality(pg_catalog.parse_ident(p_table)) <> 2 THEN
        RAISE EXCEPTION 'gsn_guard: % must be a schema-qualified table name (schema.table)', p_table;
    END IF;
    v_oid := pg_catalog.to_regclass(p_table);
    IF v_oid IS NULL THEN
        RAISE EXCEPTION 'gsn_guard: table % does not exist', p_table;
    END IF;
    SELECT c.relkind, c.relpersistence INTO v_relkind, v_persistence
    FROM pg_catalog.pg_class AS c
    WHERE c.oid = v_oid;
    IF v_relkind NOT IN ('r', 'p') OR v_persistence = 't' THEN
        RAISE EXCEPTION 'gsn_guard: % is not an ordinary permanent table', p_table;
    END IF;

    -- The name as PostgreSQL itself spells it when the object is dropped.
    SELECT i.identity INTO v_name
    FROM pg_catalog.pg_identify_object('pg_catalog.pg_class'::pg_catalog.regclass, v_oid, 0) AS i;
    IF EXISTS (SELECT 1 FROM gsn_guard.protected_tables AS t WHERE t.table_name = v_name) THEN
        RAISE EXCEPTION '% is already protected', v_name;
    END IF;

    SELECT pg_catalog.jsonb_agg(
               pg_catalog.jsonb_build_object('name', a.attname, 'type', pg_catalog.format_type(a.atttypid, a.atttypmod))
               ORDER BY a.attnum)
    INTO v_columns
    FROM pg_catalog.pg_attribute AS a
    WHERE a.attrelid = v_oid AND a.attnum > 0 AND NOT a.attisdropped;

    INSERT INTO gsn_guard.protected_tables (table_name, columns) VALUES (v_name, v_columns);

    FOREACH v_trigger IN ARRAY coalesce(p_triggers, ARRAY[]::pg_catalog.text[]) LOOP
        SELECT t.oid, t.tgenabled INTO v_trigger_oid, v_enabled
        FROM pg_catalog.pg_trigger AS t
        WHERE t.tgrelid = v_oid AND t.tgname = v_trigger AND NOT t.tgisinternal;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'gsn_guard: table % has no trigger %', v_name, v_trigger;
        END IF;
        IF v_enabled NOT IN ('O', 'A') THEN
            RAISE EXCEPTION 'gsn_guard: trigger % on % is not enabled (it would not fire in normal sessions)',
                v_trigger, v_name;
        END IF;
        INSERT INTO gsn_guard.protected_triggers (table_name, trigger_name, definition)
        VALUES (v_name, v_trigger, gsn_guard.trigger_definition(v_trigger_oid));
    END LOOP;
END;
$$;

CREATE FUNCTION gsn_guard.protect_function(p_signature pg_catalog.text)
RETURNS pg_catalog.void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, gsn_guard, pg_temp
AS $$
DECLARE
    v_oid  pg_catalog.oid;
    v_name pg_catalog.text;
BEGIN
    IF p_signature IS NULL
       OR pg_catalog.cardinality(pg_catalog.parse_ident(pg_catalog.split_part(p_signature, '(', 1))) <> 2 THEN
        RAISE EXCEPTION 'gsn_guard: % must be a schema-qualified function signature (schema.name(argument types))',
            p_signature;
    END IF;
    v_oid := pg_catalog.to_regprocedure(p_signature);
    IF v_oid IS NULL THEN
        RAISE EXCEPTION 'gsn_guard: function % does not exist', p_signature;
    END IF;

    SELECT i.identity INTO v_name
    FROM pg_catalog.pg_identify_object('pg_catalog.pg_proc'::pg_catalog.regclass, v_oid, 0) AS i;
    IF EXISTS (SELECT 1 FROM gsn_guard.protected_functions AS f WHERE f.signature = v_name) THEN
        RAISE EXCEPTION '% is already protected', v_name;
    END IF;

    INSERT INTO gsn_guard.protected_functions (signature, fingerprint)
    VALUES (v_name, gsn_guard.function_fingerprint(v_oid));
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- The watchdog. check() runs after every schema change (ddl_command_end) and looks at the STATE of
-- every registered object, so it does not matter which statement caused the change. check_drop()
-- runs when objects are dropped (sql_drop) and names what was dropped. Either raises, and the
-- statement is rolled back. With nothing registered (before migration 0004) both return at once.
-- Event trigger functions are called by PostgreSQL itself, so nobody needs EXECUTE on them.
--
-- SECURITY DEFINER because they read the registry, which gsn_migrate (whose statements they check)
-- cannot read.
-- ---------------------------------------------------------------------------------------------

CREATE FUNCTION gsn_guard.check() RETURNS pg_catalog.event_trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, gsn_guard, pg_temp
AS $$
DECLARE
    v_table       pg_catalog.record;
    v_column      pg_catalog.record;
    v_trigger     pg_catalog.record;
    v_function    pg_catalog.record;
    v_oid         pg_catalog.oid;
    v_relkind     pg_catalog."char";
    v_rls         pg_catalog.bool;
    v_found       pg_catalog.record;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM gsn_guard.protected_tables)
       AND NOT EXISTS (SELECT 1 FROM gsn_guard.protected_functions) THEN
        RETURN;
    END IF;

    FOR v_table IN SELECT t.table_name, t.columns FROM gsn_guard.protected_tables AS t ORDER BY t.table_name LOOP
        v_oid := pg_catalog.to_regclass(v_table.table_name);
        IF v_oid IS NULL THEN
            RAISE EXCEPTION 'gsn_guard: protected table % is missing (dropped, renamed or moved)', v_table.table_name;
        END IF;
        SELECT c.relkind, (c.relrowsecurity OR c.relforcerowsecurity) INTO v_relkind, v_rls
        FROM pg_catalog.pg_class AS c
        WHERE c.oid = v_oid;
        IF v_relkind NOT IN ('r', 'p') THEN
            RAISE EXCEPTION 'gsn_guard: protected table % is no longer an ordinary table', v_table.table_name;
        END IF;
        IF v_rls THEN
            RAISE EXCEPTION 'gsn_guard: row-level security is switched on for protected table %', v_table.table_name;
        END IF;
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_rewrite AS r WHERE r.ev_class = v_oid AND r.rulename <> '_RETURN') THEN
            RAISE EXCEPTION 'gsn_guard: protected table % has a rewrite rule', v_table.table_name;
        END IF;

        FOR v_column IN
            SELECT e.elem ->> 'name' AS name, e.elem ->> 'type' AS type
            FROM pg_catalog.jsonb_array_elements(v_table.columns) AS e (elem)
        LOOP
            IF NOT EXISTS (
                SELECT 1
                FROM pg_catalog.pg_attribute AS a
                WHERE a.attrelid = v_oid AND a.attnum > 0 AND NOT a.attisdropped
                  AND a.attname = v_column.name
                  AND pg_catalog.format_type(a.atttypid, a.atttypmod) = v_column.type
            ) THEN
                RAISE EXCEPTION 'gsn_guard: column % of protected table % was dropped, renamed or changed to another type',
                    v_column.name, v_table.table_name;
            END IF;
        END LOOP;

        FOR v_trigger IN
            SELECT g.trigger_name, g.definition
            FROM gsn_guard.protected_triggers AS g
            WHERE g.table_name = v_table.table_name
        LOOP
            SELECT t.tgenabled, gsn_guard.trigger_definition(t.oid) AS definition INTO v_found
            FROM pg_catalog.pg_trigger AS t
            WHERE t.tgrelid = v_oid AND t.tgname = v_trigger.trigger_name AND NOT t.tgisinternal;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'gsn_guard: trigger % of protected table % is missing',
                    v_trigger.trigger_name, v_table.table_name;
            END IF;
            IF v_found.tgenabled NOT IN ('O', 'A') THEN
                RAISE EXCEPTION 'gsn_guard: trigger % of protected table % is not enabled (it would not fire in normal sessions)',
                    v_trigger.trigger_name, v_table.table_name;
            END IF;
            IF v_found.definition IS DISTINCT FROM v_trigger.definition THEN
                RAISE EXCEPTION 'gsn_guard: trigger % of protected table % was redefined',
                    v_trigger.trigger_name, v_table.table_name;
            END IF;
        END LOOP;
    END LOOP;

    FOR v_function IN
        SELECT f.signature, f.fingerprint FROM gsn_guard.protected_functions AS f ORDER BY f.signature
    LOOP
        v_oid := pg_catalog.to_regprocedure(v_function.signature);
        IF v_oid IS NULL THEN
            RAISE EXCEPTION 'gsn_guard: protected function % is missing (dropped, renamed or moved)', v_function.signature;
        END IF;
        IF gsn_guard.function_fingerprint(v_oid) IS DISTINCT FROM v_function.fingerprint THEN
            RAISE EXCEPTION 'gsn_guard: protected function % was changed', v_function.signature;
        END IF;
    END LOOP;
END;
$$;

CREATE FUNCTION gsn_guard.check_drop() RETURNS pg_catalog.event_trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, gsn_guard, pg_temp
AS $$
DECLARE
    v_dropped pg_catalog.record;
BEGIN
    FOR v_dropped IN SELECT d.object_type, d.object_identity FROM pg_catalog.pg_event_trigger_dropped_objects() AS d LOOP
        IF v_dropped.object_type = 'table'
           AND EXISTS (SELECT 1 FROM gsn_guard.protected_tables AS t WHERE t.table_name = v_dropped.object_identity) THEN
            RAISE EXCEPTION 'gsn_guard: protected table % was dropped', v_dropped.object_identity;
        ELSIF v_dropped.object_type = 'trigger'
              AND EXISTS (SELECT 1 FROM gsn_guard.protected_triggers AS g
                          WHERE pg_catalog.quote_ident(g.trigger_name) || ' on ' || g.table_name = v_dropped.object_identity) THEN
            RAISE EXCEPTION 'gsn_guard: protected trigger % was dropped', v_dropped.object_identity;
        ELSIF v_dropped.object_type = 'function'
              AND EXISTS (SELECT 1 FROM gsn_guard.protected_functions AS f WHERE f.signature = v_dropped.object_identity) THEN
            RAISE EXCEPTION 'gsn_guard: protected function % was dropped', v_dropped.object_identity;
        END IF;
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- Grants. Functions are executable by PUBLIC unless revoked. Only gsn_migrate may register; the
-- event trigger functions and the two definition helpers are for PostgreSQL and the superuser.
-- ---------------------------------------------------------------------------------------------

REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA gsn_guard FROM PUBLIC;
GRANT EXECUTE ON FUNCTION gsn_guard.protect_table(pg_catalog.text, pg_catalog.text[]) TO gsn_migrate;
GRANT EXECUTE ON FUNCTION gsn_guard.protect_function(pg_catalog.text) TO gsn_migrate;

-- Last: from here on every schema change in this database is checked (the registry is still empty,
-- so nothing is enforced until migration 0004 registers the protected objects).
CREATE EVENT TRIGGER gsn_guard_check ON ddl_command_end EXECUTE FUNCTION gsn_guard.check();
CREATE EVENT TRIGGER gsn_guard_check_drop ON sql_drop EXECUTE FUNCTION gsn_guard.check_drop();
SQL
