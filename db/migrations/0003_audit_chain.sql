-- 0003: a tamper-evident hash chain over audit_log, and a tidy-up of its sequence grant (spec
-- section 8.2 and 8.3).
--
-- Every new audit row stores its position in the chain (chain_seq), the hash of the row before it
-- (prev_hash) and its own hash (row_hash), all set by a BEFORE INSERT trigger. Changing, removing or
-- reordering a row, or cutting rows off the end, breaks the chain, and audit_verify() reports the first
-- position where it breaks. The append-only triggers of 0001 still refuse UPDATE, DELETE and TRUNCATE;
-- the chain is the second line of defence: it makes a change visible even after someone switched those
-- triggers off. (It cannot stop a superuser who rewrites the whole chain and the head. Keeping the
-- latest hash somewhere off the server is milestone M4.)
--
-- EVERY audit_log row must be chained. A database is always created fresh, so a row without a
-- chain_seq can only have been appended around the trigger (a forged append that a later off-server
-- export of the head would never see); verification reports it as a break.
--
-- Hash format (hash_version 1). row_hash = sha256 of the UTF-8 text of this JSON array in PostgreSQL's
-- jsonb text form:
--
--   [hex(prev_hash) or null, chain_seq, id, at as UTC with microseconds ("YYYY-MM-DDTHH:MM:SS.UUUUUUZ"),
--    actor, action, target or null, details]
--
-- The trigger function and the verification core each spell this expression out (keep them
-- identical); tests/integration/test_audit_chain.py recomputes it independently in Python.
--
-- Callers cannot choose any chain column: the column-level INSERT grants of 0001 (actor, action, target,
-- details) do not include them, and the trigger overwrites whatever a more privileged inserter gives.
--
-- Name resolution (see the header of 0002): every function pins `search_path = pg_catalog, pg_temp`
-- (pg_temp explicitly LAST, and no `public`, so that no function created there can capture a call),
-- writes every type name schema-qualified (pg_catalog.text, ...) and every relation public.-qualified
-- (or, in the verification core, resolved from a regclass, whose text form is then always
-- schema-qualified), so a caller's temporary tables and domains cannot stand in for the real ones.

-- ---------------------------------------------------------------------------------------------
-- The chain head: one row holding the position and hash of the latest chained row. No login role has
-- any privilege on it; only the SECURITY DEFINER functions below (which run as gsn_migrate, the
-- owner) read and write it.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE public.audit_chain (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),  -- at most one row
    last_seq  bigint  NOT NULL,                                    -- chain_seq of the latest chained row (0: none yet)
    last_hash bytea                                                -- its row_hash (NULL: none yet)
);

INSERT INTO public.audit_chain (singleton, last_seq, last_hash) VALUES (true, 0, NULL);

REVOKE ALL ON public.audit_chain FROM PUBLIC;

-- The columns stay NULL only on a row that bypassed the trigger (see above).
ALTER TABLE public.audit_log
    ADD COLUMN chain_seq    bigint UNIQUE,
    ADD COLUMN prev_hash    bytea,
    ADD COLUMN row_hash     bytea,
    ADD COLUMN hash_version smallint;

-- ---------------------------------------------------------------------------------------------
-- The chain trigger. SECURITY DEFINER so that the worker, which has neither SELECT on audit_log nor
-- any right on audit_chain, can still append.
-- ---------------------------------------------------------------------------------------------

CREATE FUNCTION public.audit_log_chain() RETURNS pg_catalog.trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_last_seq  pg_catalog.int8;
    v_last_hash pg_catalog.bytea;
BEGIN
    -- Lock the chain head, then read it. The lock serialises concurrent inserters: the second one
    -- waits here until the first has committed (or rolled back), and then sees the head the first one
    -- left behind. Without it two inserters would read the same head and chain two rows to the same
    -- predecessor. The chain therefore follows the order in which the locks were taken (the order the
    -- rows commit in), NOT the order of the identity column `id`, which is assigned before this
    -- trigger runs and so may differ. Verification walks chain_seq, never id. The lock is held to the
    -- end of the inserting transaction; long transactions should write their audit row last.
    SELECT c.last_seq, c.last_hash
    INTO v_last_seq, v_last_hash
    FROM public.audit_chain AS c
    WHERE c.singleton
    FOR UPDATE;
    IF NOT FOUND THEN
        -- Fail closed: an audit row is never written outside the chain.
        RAISE EXCEPTION 'audit_chain has no head row; refusing to write an unchained audit row';
    END IF;

    NEW.chain_seq := v_last_seq + 1;
    NEW.prev_hash := v_last_hash;
    NEW.hash_version := 1;
    NEW.row_hash := sha256(convert_to(
        jsonb_build_array(
            encode(NEW.prev_hash, 'hex'), NEW.chain_seq, NEW.id,
            to_char(NEW.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            NEW.actor, NEW.action, NEW.target, NEW.details
        )::pg_catalog.text,
        'UTF8'
    ));

    UPDATE public.audit_chain AS c
    SET last_seq = NEW.chain_seq, last_hash = NEW.row_hash
    WHERE c.singleton;

    RETURN NEW;
END;
$$;

CREATE TRIGGER audit_log_chain_insert
    BEFORE INSERT ON public.audit_log
    FOR EACH ROW EXECUTE FUNCTION public.audit_log_chain();

-- ---------------------------------------------------------------------------------------------
-- audit_verify_rows(): the verification core. It checks the rows of ANY table shaped like audit_log
-- against a chain head given as parameters, and returns one row:
--   ok             true when every check below passes
--   rows_checked   how many rows were checked and found good: all chained rows when ok, otherwise the
--                  rows before the first bad position (never negative)
--   first_bad_seq  the first chain position at which something is wrong (NULL when ok; otherwise
--                  always >= 1)
-- For every position 1..n, in chain_seq order, the row must exist (no gap: chain_seq = position), have
-- hash_version 1, point at the previous row's row_hash (the first at NULL) and carry the hash
-- recomputed from its own columns. Then the head (p_head_seq, p_head_hash) must agree with the rows:
-- p_head_seq = n and p_head_hash = the last row's hash. Where a break is reported:
--   * a changed row, or one with a wrong link: its own position;
--   * a removed row in the middle: the missing position;
--   * rows removed from the end (the head is ahead): n + 1;
--   * a head that disagrees with the rows: min(head_seq, n) + 1 when the counts differ, otherwise n
--     (1 when n = 0); a missing head (NULL parameters) counts as a disagreeing head;
--   * a row WITHOUT a chain_seq (an unchained row, which cannot be given a position): the position it
--     would take, 1 + the number of chained rows with a smaller id, so a forged append is reported at
--     n + 1, right after the last chained row. (The id order of the chain is only approximately the
--     chain order, see the trigger, so for a row in the middle this is the nearest position.)
-- With several problems the lowest position is reported, and the numbers are clamped (a head behind the
-- start of the chain, e.g. a negative last_seq, still gives first_bad_seq >= 1 and rows_checked >= 0).
--
-- It is the one place the hash is checked, so the integration tests tamper with COPIES of audit_log
-- (and pass the head they took with the copy) rather than with the protected table itself.
--
-- SECURITY DEFINER because audit_log is not readable by the worker, but EXECUTE goes to nobody except
-- the owner: it takes a table name, and a login role must not be able to point a definer function at
-- arbitrary tables. audit_verify() below is the entry point for the worker and the web service.
-- The table is passed as a regclass and written into the dynamic statement with format('%s', regclass),
-- whose output is already quoted; the name is then resolved again under the pinned search_path and the
-- function refuses to continue unless that finds the very same table (a temporary table in the
-- caller's pg_temp can never stand in for a real one). STABLE, so every statement of it, and of its
-- caller, reads the same snapshot: rows and head cannot disagree because of a concurrent insert.
-- ---------------------------------------------------------------------------------------------

CREATE FUNCTION public.audit_verify_rows(
    p_rows      pg_catalog.regclass,
    p_head_seq  pg_catalog.int8,
    p_head_hash pg_catalog.bytea
)
RETURNS TABLE (ok pg_catalog.bool, rows_checked pg_catalog.int8, first_bad_seq pg_catalog.int8)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    v_table pg_catalog.text := format('%s', p_rows);
BEGIN
    IF v_table::pg_catalog.regclass IS DISTINCT FROM p_rows THEN
        RAISE EXCEPTION 'table % does not resolve to itself under the pinned search_path', v_table;
    END IF;

    RETURN QUERY EXECUTE format($sql$
        WITH chained AS (
            SELECT l.id,
                   l.chain_seq,
                   l.hash_version,
                   l.prev_hash,
                   l.row_hash,
                   row_number() OVER w AS pos,
                   lag(l.row_hash) OVER w AS previous_row_hash,
                   sha256(convert_to(
                       jsonb_build_array(
                           encode(l.prev_hash, 'hex'), l.chain_seq, l.id,
                           to_char(l.at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
                           l.actor, l.action, l.target, l.details
                       )::pg_catalog.text,
                       'UTF8'
                   )) AS computed_hash
            FROM %1$s AS l
            WHERE l.chain_seq IS NOT NULL
            WINDOW w AS (ORDER BY l.chain_seq)
        ),
        tally AS (
            SELECT count(*) AS n,
                   (SELECT t.row_hash FROM chained AS t ORDER BY t.pos DESC LIMIT 1) AS last_row_hash
            FROM chained
        ),
        bad_row AS (
            SELECT min(c.pos) AS seq
            FROM chained AS c
            WHERE c.chain_seq <> c.pos
               OR c.hash_version IS DISTINCT FROM 1
               OR c.prev_hash IS DISTINCT FROM c.previous_row_hash
               OR c.row_hash IS DISTINCT FROM c.computed_hash
        ),
        unchained AS (
            SELECT (SELECT count(*) FROM chained AS c WHERE c.id < u.id) + 1 AS seq
            FROM (SELECT min(l.id) AS id FROM %1$s AS l WHERE l.chain_seq IS NULL) AS u
            WHERE u.id IS NOT NULL
        ),
        bad_head AS (
            SELECT CASE
                       WHEN $1 IS DISTINCT FROM t.n THEN least($1, t.n) + 1
                       WHEN $2 IS DISTINCT FROM t.last_row_hash THEN greatest(t.n, 1)
                   END AS seq
            FROM tally AS t
        ),
        verdict AS (
            SELECT least((SELECT r.seq FROM bad_row AS r),
                         (SELECT u.seq FROM unchained AS u),
                         (SELECT d.seq FROM bad_head AS d)) AS raw_seq
        ),
        shown AS (
            SELECT v.raw_seq IS NULL AS is_ok,
                   CASE WHEN v.raw_seq IS NOT NULL THEN greatest(v.raw_seq, 1) END AS bad_seq
            FROM verdict AS v
        )
        SELECT s.is_ok,
               CASE WHEN s.is_ok THEN t.n ELSE least(s.bad_seq - 1, t.n) END,
               s.bad_seq
        FROM shown AS s, tally AS t
    $sql$, v_table) USING p_head_seq, p_head_hash;
END;
$$;

-- ---------------------------------------------------------------------------------------------
-- audit_verify(): the entry point. Checks the real log against the real head and returns the core's
-- answer, so the worker (`gsn audit verify`) and the web service can verify without being able to read
-- the audit contents. A missing head row gives NULL head values, which the core reports as a break.
-- ---------------------------------------------------------------------------------------------

CREATE FUNCTION public.audit_verify()
RETURNS TABLE (ok pg_catalog.bool, rows_checked pg_catalog.int8, first_bad_seq pg_catalog.int8)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $$
    SELECT v.ok, v.rows_checked, v.first_bad_seq
    FROM (SELECT 1) AS one
    LEFT JOIN public.audit_chain AS h ON h.singleton,
    LATERAL public.audit_verify_rows('public.audit_log'::pg_catalog.regclass, h.last_seq, h.last_hash) AS v;
$$;

-- ---------------------------------------------------------------------------------------------
-- Grants. The trigger function is executed by the trigger machinery, not by callers. The core is for
-- the owner alone. audit_verify() is for the worker (`gsn audit verify`) and the web service.
-- ---------------------------------------------------------------------------------------------

REVOKE EXECUTE ON FUNCTION public.audit_log_chain() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.audit_verify_rows(pg_catalog.regclass, pg_catalog.int8, pg_catalog.bytea) FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.audit_verify() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.audit_verify() TO gsn_worker, gsn_web;

-- Spec section 8.3: id is an identity column, which draws from its sequence without any privilege on it,
-- so the USAGE that 0001 granted is not needed.
REVOKE USAGE ON SEQUENCE public.audit_log_id_seq FROM gsn_web, gsn_worker;
