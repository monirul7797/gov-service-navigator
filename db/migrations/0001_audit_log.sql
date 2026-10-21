-- 0001: the append-only audit log (spec section 6).
--
-- Every admin and system action gets one row here. Rows are never changed or removed: UPDATE,
-- DELETE and TRUNCATE are rejected by triggers, and the table's owner (gsn_migrate) is subject to
-- them too, because triggers fire for every role. (Only a role that may drop the triggers or the
-- table, i.e. the owner or a superuser, could remove the protection, and that is a visible schema
-- change. gsn_migrate is used only by the one-shot `migrate` job.)
--
-- Roles (created by db/init/10-roles.sh): the web service reads and appends, the worker appends,
-- the engine has no access to this table.

CREATE TABLE audit_log (
    id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at      timestamptz NOT NULL DEFAULT now(),
    actor   text        NOT NULL,
    action  text        NOT NULL,
    target  text,
    details jsonb       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX audit_log_at_idx ON audit_log (at);

CREATE FUNCTION audit_log_reject_change() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$;

CREATE TRIGGER audit_log_no_update_delete
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change();

CREATE TRIGGER audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change();

REVOKE ALL ON audit_log FROM PUBLIC;
GRANT SELECT, INSERT ON audit_log TO gsn_web;
GRANT INSERT ON audit_log TO gsn_worker;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO gsn_web, gsn_worker;
