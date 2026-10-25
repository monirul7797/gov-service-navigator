"""Closing check of the audit hash chain: it runs LAST (pytest collects the test files alphabetically, and
``zz`` sorts after every other module), after all other integration tests have written their audit rows.

Whatever the earlier tests did through the real code paths (release builds, gate verdicts, publishes,
rollbacks, direct inserts by each role, concurrent writers, the pg_temp attack tests), the whole log must
still be one intact chain. This is what makes the audit rows written by CI's stack run verified, not just
the ones the audit tests wrote themselves. Keep this file's name sorting last.
"""


def test_every_audit_row_written_during_the_run_is_chained_and_the_chain_verifies(connect):
    web, worker = connect("web"), connect("worker")
    total, unchained = web.execute(
        "SELECT count(*), count(*) FILTER (WHERE chain_seq IS NULL) FROM public.audit_log"
    ).fetchone()
    assert unchained == 0, "an audit row without a chain position"

    ok, rows_checked, first_bad_seq = worker.execute(
        "SELECT ok, rows_checked, first_bad_seq FROM public.audit_verify()"
    ).fetchone()
    assert (ok, first_bad_seq) == (True, None), f"the audit chain is broken at chain_seq {first_bad_seq}"
    assert rows_checked == total  # every row of the log was checked

    # the head and the log agree
    head_seq, head_hash = connect("migrate").execute(
        "SELECT last_seq, last_hash FROM public.audit_chain"
    ).fetchone()
    last_seq, last_hash = web.execute(
        "SELECT chain_seq, row_hash FROM public.audit_log ORDER BY chain_seq DESC LIMIT 1"
    ).fetchone()
    assert (head_seq, head_hash) == (last_seq, last_hash) and head_seq == total
