-- 0004: register the protected objects with the guard watchdog (spec section 8.1).
--
-- The watchdog itself (schema gsn_guard, its registry, the registration functions and the event
-- triggers) is created by db/init/20-guard.sh, as the superuser, when the database is first created.
-- From the moment this migration has run, every schema change in the database is checked against
-- what is registered here, and is rolled back when it drops, renames, retypes, disables, replaces or
-- rewrites one of these objects, adds a trigger or inheritance to a protected table, or changes the
-- EXECUTE grants of a protected function. Adding columns (without a volatile default), indexes and
-- unrelated tables stays possible; changing a guard on purpose needs the superuser (the break-glass
-- procedure in docs/DEVELOPMENT.md).
--
-- Registration is by schema-qualified NAME (never by object id), so it survives pg_dump / pg_restore.
-- A table is recorded with its column names and types at this moment, and with the triggers that
-- guard it (their definitions, not just their names), and registration fails unless ALL of its
-- triggers are listed. A function is recorded by a fingerprint of its source, settings (the pinned
-- search_path), SECURITY DEFINER flag, owner and language (by name), volatility, strictness and ACL.
--
-- Later migrations register what they add the same way, as their last step, for example:
--     SELECT gsn_guard.protect_table('public.new_table', ARRAY['new_table_guard_row']);
-- There is no unregister: an entry can only be removed by the superuser.

-- Preflight. db/init/ runs only on an EMPTY data volume, so a database created before the watchdog
-- existed does not have it, and cannot get it from a migration (only the superuser may create event
-- triggers). A guard that is only partly there (an init script that stopped half way, an event trigger
-- switched off) would look fine yet protect nothing, so the three event triggers must all exist, call
-- the right functions and be enabled ('O': they fire in normal sessions). Stop with a clear message
-- instead of failing on the first registration, or worse, registering into a watchdog that is not
-- watching.
DO $$
DECLARE
    v_missing pg_catalog.text;
BEGIN
    IF pg_catalog.to_regnamespace('gsn_guard') IS NULL THEN
        RAISE EXCEPTION 'database predates the M1 guard (db/init/20-guard.sh); recreate it: docker compose down -v';
    END IF;

    SELECT pg_catalog.string_agg(w.evtname, ', ' ORDER BY w.evtname) INTO v_missing
    FROM (VALUES ('gsn_guard_check', 'ddl_command_end', 'check'),
                 ('gsn_guard_check_drop', 'sql_drop', 'check_drop'),
                 ('gsn_guard_check_rewrite', 'table_rewrite', 'check_rewrite')) AS w (evtname, evtevent, proname)
    WHERE NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_event_trigger AS e
        JOIN pg_catalog.pg_proc AS p ON p.oid = e.evtfoid
        WHERE e.evtname = w.evtname AND e.evtevent = w.evtevent AND e.evtenabled = 'O'
          AND p.proname = w.proname AND p.pronamespace = 'gsn_guard'::pg_catalog.regnamespace
    );
    IF v_missing IS NOT NULL THEN
        RAISE EXCEPTION 'the M1 guard is incomplete (event trigger missing, disabled or wrong: %); recreate it: docker compose down -v', v_missing;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------------------------
-- Tables and their guard triggers
-- ---------------------------------------------------------------------------------------------

-- 0001 and 0003: append-only (UPDATE, DELETE, TRUNCATE refused), and the hash chain trigger.
SELECT gsn_guard.protect_table('public.audit_log',
    ARRAY['audit_log_no_update_delete', 'audit_log_no_truncate', 'audit_log_chain_insert']);

-- 0003: the chain head has no trigger of its own: the chain trigger updates it on every insert and
-- the tests rewrite it inside rolled-back transactions. It is registered for existence and column
-- signature only, so it cannot be dropped, renamed or retyped while writes to it stay possible.
SELECT gsn_guard.protect_table('public.audit_chain', ARRAY[]::text[]);

-- 0002: append-only tables.
SELECT gsn_guard.protect_table('public.record_versions',
    ARRAY['record_versions_guard_row', 'record_versions_guard_truncate']);
SELECT gsn_guard.protect_table('public.publications',
    ARRAY['publications_guard_row', 'publications_guard_truncate']);
SELECT gsn_guard.protect_table('public.services',
    ARRAY['services_guard_row', 'services_guard_truncate']);
SELECT gsn_guard.protect_table('public.records',
    ARRAY['records_guard_row', 'records_guard_truncate']);

-- 0002: tables with state rules.
SELECT gsn_guard.protect_table('public.releases',
    ARRAY['releases_guard_row', 'releases_guard_truncate']);
SELECT gsn_guard.protect_table('public.release_members',
    ARRAY['release_members_guard_row', 'release_members_guard_truncate']);
SELECT gsn_guard.protect_table('public.release_services',
    ARRAY['release_services_guard_row', 'release_services_guard_truncate']);
SELECT gsn_guard.protect_table('public.active_release',
    ARRAY['active_release_guard_row', 'active_release_guard_truncate']);

-- ---------------------------------------------------------------------------------------------
-- Functions: the trigger functions, the hash chain, and publish / rollback / verify
-- ---------------------------------------------------------------------------------------------

SELECT gsn_guard.protect_function('public.audit_log_reject_change()');
SELECT gsn_guard.protect_function('public.audit_log_chain()');
SELECT gsn_guard.protect_function('public.audit_verify()');
SELECT gsn_guard.protect_function('public.audit_verify_rows(regclass, bigint, bytea)');
SELECT gsn_guard.protect_function('public.gsn_reject_change()');
SELECT gsn_guard.protect_function('public.releases_guard()');
SELECT gsn_guard.protect_function('public.release_children_guard()');
SELECT gsn_guard.protect_function('public.active_release_guard()');
SELECT gsn_guard.protect_function('public.publish_release(bigint, text)');
SELECT gsn_guard.protect_function('public.rollback_release(text)');
