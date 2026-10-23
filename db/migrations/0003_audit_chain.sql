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
-- Hash format (hash_version 1). row_hash = sha256 of the UTF-8 text of this JSON array in PostgreSQL's
-- jsonb text form:
--
--   [hex(prev_hash) or null, chain_seq, id, at as UTC with microseconds ("YYYY-MM-DDTHH:MM:SS.UUUUUUZ"),
--    actor, action, target or null, details]
--
-- The trigger function and audit_verify() each spell this expression out (keep them identical);
-- tests/integration/test_audit_chain.py recomputes it independently in Python.
--
-- Callers cannot choose any chain column: the column-level INSERT grants of 0001 (actor, action, target,
-- details) do not include them, and the trigger overwrites whatever a more privileged inserter gives.
--
-- Name resolution (see the header of 0002): every function pins `search_path = pg_catalog, public,
-- pg_temp` (pg_temp explicitly LAST), writes every type name schema-qualified (pg_catalog.text, ...)
-- and every relation public.-qualified, so a caller's temporary tables and domains cannot stand in
-- for the real ones.

-- ---------------------------------------------------------------------------------------------
-- The chain head: one row holding the position and hash of the latest chained row. No login role has
-- any privilege on it; only the two SECURITY DEFINER functions below (which run as gsn_migrate, the
-- owner) read and write it.
-- ---------------------------------------------------------------------------------------------

CREATE TABLE public.audit_chain (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),  -- at most one row
    last_seq  bigint  NOT NULL,                                    -- chain_seq of the latest chained row (0: none yet)
    last_hash bytea                                                -- its row_hash (NULL: none yet)
);

INSERT INTO public.audit_chain (singleton, last_seq, last_hash) VALUES (true, 0, NULL);

REVOKE ALL ON public.audit_chain FROM PUBLIC;

-- Rows written before this migration (only possible in a developer's local database) keep NULL here:
-- they are "pre-chain" and verification starts at chain_seq = 1.
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
SET search_path = pg_catalog, public, pg_temp
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
-- audit_verify(): walks the chain and returns one row.
--   ok             true when every check below passes
--   rows_checked   how many rows were checked and found good: all chained rows when ok, otherwise the
--                  rows before the first bad position
--   first_bad_seq  the first chain_seq at which something is wrong (NULL when ok)
-- For every position 1..n (rows with a NULL chain_seq are pre-chain and skipped) the row must exist
-- (no gap), have hash_version 1, point at the previous row's row_hash (the first at NULL) and carry the
-- hash recomputed from its own columns. Then the head must agree with the rows: last_seq = n and
-- last_hash = the last row's hash. A removed row shows up as a gap (the missing position), removed
-- rows at the end as a head that is ahead (position n + 1), a rewritten head where it and the rows
-- part ways. A missing head row fails verification too.
--
-- One SQL statement, so rows and head are read from the same snapshot even while others insert. It
-- is SECURITY DEFINER so that the worker can verify without being able to read the audit contents.
-- ---------------------------------------------------------------------------------------------

CREATE FUNCTION public.audit_verify()
RETURNS TABLE (ok pg_catalog.bool, rows_checked pg_catalog.int8, first_bad_seq pg_catalog.int8)
LANGUAGE sql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
    WITH chained AS (
        SELECT l.chain_seq,
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
        FROM public.audit_log AS l
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
    bad_head AS (
        SELECT CASE
                   WHEN h.last_seq IS DISTINCT FROM t.n THEN least(h.last_seq, t.n) + 1
                   WHEN h.last_hash IS DISTINCT FROM t.last_row_hash THEN greatest(t.n, 1)
               END AS seq
        FROM tally AS t
        LEFT JOIN public.audit_chain AS h ON h.singleton
    ),
    verdict AS (
        SELECT least((SELECT r.seq FROM bad_row AS r), (SELECT d.seq FROM bad_head AS d)) AS first_bad
    )
    SELECT v.first_bad IS NULL,
           least(coalesce(v.first_bad - 1, t.n), t.n),
           v.first_bad
    FROM verdict AS v, tally AS t;
$$;

-- ---------------------------------------------------------------------------------------------
-- Grants. The trigger function is executed by the trigger machinery, not by callers. audit_verify() is
-- for the worker (`gsn audit verify`) and the web service.
-- ---------------------------------------------------------------------------------------------

REVOKE EXECUTE ON FUNCTION public.audit_log_chain() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.audit_verify() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.audit_verify() TO gsn_worker, gsn_web;

-- Spec section 8.3: id is an identity column, which draws from its sequence without any privilege on it,
-- so the USAGE that 0001 granted is not needed.
REVOKE USAGE ON SEQUENCE public.audit_log_id_seq FROM gsn_web, gsn_worker;
