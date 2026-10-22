-- 0002: releases (M1, spec sections 3, 3.1 and 6).
--
-- The chatbot's knowledge base as immutable, versioned releases:
--
--   services, records, record_versions   what exists; versions are append-only, vectors included
--   releases, release_services,          one build of the corpus at one git commit; drafts are
--   release_members                      assembled, the gate validates or rejects them once, and
--                                        from then on they are frozen
--   active_release, publications         which release is live, and the history of switches;
--                                        written only by publish_release() / rollback_release()
--   engine_active_* views                what the engine may read: the active release, nothing else
--
-- Who may do what (spec 3.1): gsn_worker builds drafts and may call publish/rollback; gsn_engine
-- reads three views; gsn_web gets nothing new; gsn_migrate owns everything. Grants are only half of
-- the protection: the guard triggers below also stop the owner. Every guarded table has exactly two
-- triggers, `<table>_guard_row` and `<table>_guard_truncate` (a later migration registers them by
-- name with the guard watchdog). Applied migrations are never edited; change the schema in a new one.

-- ---------------------------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------------------------

CREATE TABLE services (
    service_id text PRIMARY KEY CHECK (service_id ~ '^[a-z][a-z0-9_]*$'),
    name_bn    text NOT NULL,
    name_en    text NOT NULL
);

CREATE TABLE records (
    record_id  text        PRIMARY KEY,
    service_id text        NOT NULL REFERENCES services (service_id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE record_versions (
    version_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_id       text        NOT NULL REFERENCES records (record_id),
    content_hash    text        NOT NULL,   -- SHA-256 of the record's canonical JSON (computed in Python)
    text_hash       text        NOT NULL,   -- SHA-256 of embed_text: same text + same model = same vector
    hash_version    integer     NOT NULL,
    record          jsonb       NOT NULL,   -- the original record, every field
    embed_text      text        NOT NULL,   -- exactly what is embedded
    category        text,
    subcategory     text,
    heading         text,
    lang            text,
    source_url      text        NOT NULL,
    url_kind        text        NOT NULL CHECK (url_kind IN ('info', 'portal')),
    official        boolean,
    source_verified boolean     NOT NULL,
    verified_at     date,
    embedding       vector(1024) NOT NULL,
    embedding_model text        NOT NULL,
    source_commit   text        NOT NULL CHECK (source_commit ~ '^[0-9a-f]{40}$'),
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text        NOT NULL,
    change_note     text,
    -- A build reuses the version of a record that already exists for the same content and model.
    UNIQUE (record_id, content_hash, embedding_model),
    -- Target of the release_members foreign key: a member's version belongs to the member's record.
    UNIQUE (version_id, record_id)
);

-- Embedding reuse: a new version whose text and model are already stored copies that vector.
CREATE INDEX record_versions_text_hash_idx ON record_versions (text_hash, embedding_model);

CREATE TABLE releases (
    release_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status          text        NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'validated', 'rejected')),
    source_commit   text        NOT NULL CHECK (source_commit ~ '^[0-9a-f]{40}$'),
    manifest        jsonb       NOT NULL,
    registry        jsonb       NOT NULL,
    registry_sha256 text        NOT NULL,   -- of the registry file's raw bytes
    thresholds      jsonb       NOT NULL,
    gate_report     jsonb,                  -- written once, with the verdict
    embedding_model text        NOT NULL,
    note            text,
    override_reason text,                   -- written once, with the verdict
    created_at      timestamptz NOT NULL DEFAULT now(),
    created_by      text        NOT NULL
);

CREATE TABLE release_services (
    release_id bigint NOT NULL REFERENCES releases (release_id),
    service_id text   NOT NULL REFERENCES services (service_id),
    status     text   NOT NULL CHECK (status IN ('live', 'hidden')),
    PRIMARY KEY (release_id, service_id)
);

CREATE TABLE release_members (
    release_id bigint NOT NULL REFERENCES releases (release_id),
    version_id bigint NOT NULL,
    record_id  text   NOT NULL,
    PRIMARY KEY (release_id, version_id),
    UNIQUE (release_id, record_id),         -- one version per record per release
    FOREIGN KEY (version_id, record_id) REFERENCES record_versions (version_id, record_id)
);

CREATE TABLE active_release (
    singleton   boolean     PRIMARY KEY DEFAULT true CHECK (singleton),  -- at most one row
    release_id  bigint      NOT NULL REFERENCES releases (release_id),
    switched_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE publications (
    publication_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    release_id          bigint      NOT NULL REFERENCES releases (release_id),  -- active after this switch
    previous_release_id bigint      REFERENCES releases (release_id),           -- active before it (NULL: none)
    action              text        NOT NULL CHECK (action IN ('publish', 'rollback')),
    at                  timestamptz NOT NULL DEFAULT now(),
    actor               text        NOT NULL
);

-- ---------------------------------------------------------------------------------------------
-- Guard triggers. They are plain functions that run with the caller's rights and fire for every
-- role, the table owner included. Names of the triggers are fixed (see the header).
-- ---------------------------------------------------------------------------------------------

-- Append-only tables: any UPDATE, DELETE or TRUNCATE is refused.
CREATE FUNCTION public.gsn_reject_change() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$;

-- releases: a release is created as a draft and then changes exactly once, from draft to
-- validated or rejected, when the gate has judged it. Only status, gate_report and override_reason
-- may change in that step; everything else is frozen from the first moment (comparing the whole
-- row as JSON means a column added later is frozen by default). Releases are never removed.
CREATE FUNCTION public.releases_guard() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'the releases table is frozen: releases are never removed';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'release % is frozen', OLD.release_id;
    ELSIF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'a release must be created as a draft (not %)', NEW.status;
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF OLD.status <> 'draft' THEN
        RAISE EXCEPTION 'release % is frozen', OLD.release_id;
    END IF;
    IF NEW.status NOT IN ('validated', 'rejected')
       OR (to_jsonb(NEW) - 'status' - 'gate_report' - 'override_reason')
          IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'gate_report' - 'override_reason') THEN
        RAISE EXCEPTION
            'release % is frozen except for one change of status (draft to validated or rejected) with its gate_report and override_reason',
            OLD.release_id;
    END IF;
    RETURN NEW;
END;
$$;

-- release_members and release_services: rows are added (and, for members, removed) only while the
-- parent release is a draft; they are never updated. The parent row is read FOR SHARE, which
-- waits for a concurrent status change of the release (a plain read would race with it) and then
-- sees its committed result, so a member cannot slip into a release that has just been validated.
CREATE FUNCTION public.release_children_guard() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_release_id bigint;
    v_status     text;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION '% is frozen: rows are never truncated', TG_TABLE_NAME;
    ELSIF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION '% rows are never updated', TG_TABLE_NAME;
    ELSIF TG_OP = 'INSERT' THEN
        v_release_id := NEW.release_id;
    ELSE
        v_release_id := OLD.release_id;
    END IF;

    SELECT r.status INTO v_status
    FROM public.releases AS r
    WHERE r.release_id = v_release_id
    FOR SHARE OF r;

    -- A missing parent (v_status is null) is left to the foreign key, which gives the clearer error.
    IF v_status IS NOT NULL AND v_status <> 'draft' THEN
        RAISE EXCEPTION 'release % is frozen', v_release_id;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

-- active_release: only a validated release may be made active, and the row is never removed. (The
-- table is written by publish_release / rollback_release only; this stops the owner's mistakes.)
CREATE FUNCTION public.active_release_guard() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'active_release cannot be truncated; it is changed by publish_release() and rollback_release()';
    ELSIF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'active_release cannot be deleted; it is changed by publish_release() and rollback_release()';
    END IF;

    SELECT r.status INTO v_status FROM public.releases AS r WHERE r.release_id = NEW.release_id;
    IF v_status IS DISTINCT FROM 'validated' THEN
        RAISE EXCEPTION 'release % is not validated', NEW.release_id;
    END IF;
    RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.gsn_reject_change() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.releases_guard() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.release_children_guard() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.active_release_guard() FROM PUBLIC;

-- Append-only tables.
CREATE TRIGGER services_guard_row
    BEFORE UPDATE OR DELETE ON services
    FOR EACH ROW EXECUTE FUNCTION public.gsn_reject_change();
CREATE TRIGGER services_guard_truncate
    BEFORE TRUNCATE ON services
    FOR EACH STATEMENT EXECUTE FUNCTION public.gsn_reject_change();

CREATE TRIGGER records_guard_row
    BEFORE UPDATE OR DELETE ON records
    FOR EACH ROW EXECUTE FUNCTION public.gsn_reject_change();
CREATE TRIGGER records_guard_truncate
    BEFORE TRUNCATE ON records
    FOR EACH STATEMENT EXECUTE FUNCTION public.gsn_reject_change();

CREATE TRIGGER record_versions_guard_row
    BEFORE UPDATE OR DELETE ON record_versions
    FOR EACH ROW EXECUTE FUNCTION public.gsn_reject_change();
CREATE TRIGGER record_versions_guard_truncate
    BEFORE TRUNCATE ON record_versions
    FOR EACH STATEMENT EXECUTE FUNCTION public.gsn_reject_change();

CREATE TRIGGER publications_guard_row
    BEFORE UPDATE OR DELETE ON publications
    FOR EACH ROW EXECUTE FUNCTION public.gsn_reject_change();
CREATE TRIGGER publications_guard_truncate
    BEFORE TRUNCATE ON publications
    FOR EACH STATEMENT EXECUTE FUNCTION public.gsn_reject_change();

-- State rules.
CREATE TRIGGER releases_guard_row
    BEFORE INSERT OR UPDATE OR DELETE ON releases
    FOR EACH ROW EXECUTE FUNCTION public.releases_guard();
CREATE TRIGGER releases_guard_truncate
    BEFORE TRUNCATE ON releases
    FOR EACH STATEMENT EXECUTE FUNCTION public.releases_guard();

CREATE TRIGGER release_members_guard_row
    BEFORE INSERT OR UPDATE OR DELETE ON release_members
    FOR EACH ROW EXECUTE FUNCTION public.release_children_guard();
CREATE TRIGGER release_members_guard_truncate
    BEFORE TRUNCATE ON release_members
    FOR EACH STATEMENT EXECUTE FUNCTION public.release_children_guard();

CREATE TRIGGER release_services_guard_row
    BEFORE INSERT OR UPDATE OR DELETE ON release_services
    FOR EACH ROW EXECUTE FUNCTION public.release_children_guard();
CREATE TRIGGER release_services_guard_truncate
    BEFORE TRUNCATE ON release_services
    FOR EACH STATEMENT EXECUTE FUNCTION public.release_children_guard();

CREATE TRIGGER active_release_guard_row
    BEFORE INSERT OR UPDATE OR DELETE ON active_release
    FOR EACH ROW EXECUTE FUNCTION public.active_release_guard();
CREATE TRIGGER active_release_guard_truncate
    BEFORE TRUNCATE ON active_release
    FOR EACH STATEMENT EXECUTE FUNCTION public.active_release_guard();

-- ---------------------------------------------------------------------------------------------
-- Publish and rollback (spec section 6). Each performs the active_release switch, the
-- publications row and the audit_log row in one transaction, so none can happen without the
-- others. They are SECURITY DEFINER (they run as gsn_migrate, which owns the tables): the worker,
-- who may not write active_release, publications or the audit log's chain fields, calls them.
-- Names are schema-qualified and search_path is pinned, so a caller cannot redirect them.
--
-- Concurrency: both functions start by locking the active_release TABLE in SHARE ROW EXCLUSIVE
-- mode, which conflicts with itself and with the row locks of INSERT/UPDATE but not with plain
-- reads. Locking the table (not just the row) also covers the first publication, when the table
-- is empty and there is no row to lock. Two concurrent calls therefore run one after the other
-- (the second waits for the first to commit), each reading the state the other left behind, so the
-- "previous" release recorded is always the one that was really active. The engine's reads are not
-- blocked.
-- ---------------------------------------------------------------------------------------------

CREATE FUNCTION public.publish_release(p_release_id bigint, p_actor text) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_status   text;
    v_previous bigint;
BEGIN
    LOCK TABLE public.active_release IN SHARE ROW EXCLUSIVE MODE;

    IF p_actor IS NULL OR btrim(p_actor) = '' THEN
        RAISE EXCEPTION 'an actor is required';
    END IF;

    SELECT r.status INTO v_status FROM public.releases AS r WHERE r.release_id = p_release_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'release % does not exist', p_release_id;
    END IF;
    IF v_status <> 'validated' THEN
        RAISE EXCEPTION 'release % is not validated', p_release_id;
    END IF;

    SELECT a.release_id INTO v_previous FROM public.active_release AS a WHERE a.singleton;
    IF v_previous = p_release_id THEN
        RAISE EXCEPTION 'release % is already active', p_release_id;
    END IF;

    IF v_previous IS NULL THEN
        INSERT INTO public.active_release (singleton, release_id, switched_at)
        VALUES (true, p_release_id, now());
    ELSE
        UPDATE public.active_release
        SET release_id = p_release_id, switched_at = now()
        WHERE singleton;
    END IF;

    INSERT INTO public.publications (release_id, previous_release_id, action, actor)
    VALUES (p_release_id, v_previous, 'publish', p_actor);

    INSERT INTO public.audit_log (actor, action, target, details)
    VALUES (p_actor, 'release.publish', 'release:' || p_release_id, jsonb_build_object('previous', v_previous));
END;
$$;

-- Undoes the most recent switch: makes the latest publication's previous release active again and
-- returns its id. A second rollback therefore re-applies the release the first one undid. With no
-- earlier release (nothing published yet, or the latest publication was the very first) it refuses.
CREATE FUNCTION public.rollback_release(p_actor text) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    v_target  bigint;
    v_current bigint;
BEGIN
    LOCK TABLE public.active_release IN SHARE ROW EXCLUSIVE MODE;

    IF p_actor IS NULL OR btrim(p_actor) = '' THEN
        RAISE EXCEPTION 'an actor is required';
    END IF;

    SELECT p.previous_release_id INTO v_target
    FROM public.publications AS p
    ORDER BY p.publication_id DESC
    LIMIT 1;
    SELECT a.release_id INTO v_current FROM public.active_release AS a WHERE a.singleton;
    IF v_target IS NULL OR v_current IS NULL THEN
        RAISE EXCEPTION 'nothing to roll back';
    END IF;

    UPDATE public.active_release
    SET release_id = v_target, switched_at = now()
    WHERE singleton;

    INSERT INTO public.publications (release_id, previous_release_id, action, actor)
    VALUES (v_target, v_current, 'rollback', p_actor);

    INSERT INTO public.audit_log (actor, action, target, details)
    VALUES (p_actor, 'release.rollback', 'release:' || v_target, jsonb_build_object('previous', v_current));

    RETURN v_target;
END;
$$;

REVOKE EXECUTE ON FUNCTION public.publish_release(bigint, text) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.rollback_release(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.publish_release(bigint, text) TO gsn_worker;
GRANT EXECUTE ON FUNCTION public.rollback_release(text) TO gsn_worker;

-- ---------------------------------------------------------------------------------------------
-- What the engine reads. The views run with their owner's rights (the default), so the engine
-- needs no access to the tables behind them: only the active release is visible, never drafts,
-- rejected releases, gate reports or old versions. They are security_barrier views: a condition
-- the engine writes (a cast that fails on some value, say) is applied only to rows the view
-- already lets through, so it cannot be used to probe rows outside the active release.
-- Records of a service that is `hidden` in the release are not shown.
-- ---------------------------------------------------------------------------------------------

CREATE VIEW public.engine_active_release WITH (security_barrier = true) AS
SELECT r.release_id, r.registry, r.registry_sha256, r.embedding_model
FROM public.active_release AS a
JOIN public.releases AS r ON r.release_id = a.release_id;

CREATE VIEW public.engine_active_services WITH (security_barrier = true) AS
SELECT s.service_id, s.name_bn, s.name_en
FROM public.active_release AS a
JOIN public.release_services AS rs ON rs.release_id = a.release_id AND rs.status = 'live'
JOIN public.services AS s ON s.service_id = rs.service_id;

CREATE VIEW public.engine_active_versions WITH (security_barrier = true) AS
SELECT v.version_id, v.record_id, rec.service_id, v.embed_text, v.record, v.category, v.subcategory,
       v.heading, v.lang, v.source_url, v.url_kind, v.official, v.source_verified, v.verified_at,
       v.embedding, v.embedding_model
FROM public.active_release AS a
JOIN public.release_members AS m ON m.release_id = a.release_id
JOIN public.record_versions AS v ON v.version_id = m.version_id AND v.record_id = m.record_id
JOIN public.records AS rec ON rec.record_id = v.record_id
JOIN public.release_services AS rs
    ON rs.release_id = a.release_id AND rs.service_id = rec.service_id AND rs.status = 'live';

-- ---------------------------------------------------------------------------------------------
-- Grants (spec section 3.1). New tables start with no rights for anyone but the owner; revoking
-- from PUBLIC is stated anyway. Identity columns need no sequence grant.
-- ---------------------------------------------------------------------------------------------

REVOKE ALL ON services, records, record_versions, releases, release_services, release_members,
              active_release, publications FROM PUBLIC;
REVOKE ALL ON engine_active_release, engine_active_services, engine_active_versions FROM PUBLIC;

-- The worker builds drafts and reads what it needs to evaluate them. It cannot write
-- active_release or publications (publish_release / rollback_release do), and it can change a
-- release only by giving it its verdict.
GRANT SELECT, INSERT ON services, records, record_versions, releases, release_services, release_members
    TO gsn_worker;
GRANT UPDATE (status, gate_report, override_reason) ON releases TO gsn_worker;
GRANT DELETE ON release_members TO gsn_worker;  -- draft releases only (release_members_guard_row)
GRANT SELECT ON active_release, publications TO gsn_worker;

-- The engine reads the active release through the views, and nothing else.
GRANT SELECT ON engine_active_release, engine_active_services, engine_active_versions TO gsn_engine;
