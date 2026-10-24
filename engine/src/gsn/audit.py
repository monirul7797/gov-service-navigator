"""Verify the ``audit_log`` hash chain (spec section 8.2) through the SQL function ``audit_verify()``.

The function runs with its owner's rights, so the worker can verify the chain without being able to read
the audit contents. Everything about the hash format lives in migration ``0003_audit_chain.sql``; this
module only asks the database and words the answer.

Every ``audit_log`` row must be chained. A row without a chain position (one appended around the trigger)
is reported as a break, at the position it would take: right after the last chained row for an append.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class AuditStatus:
    """The verdict of ``audit_verify()``.

    ``first_bad_seq`` is the first chain position (``chain_seq``) at which something is wrong, or None when
    the chain is intact; it is always at least 1. For a changed or missing row it is that row's position;
    for rows cut off the end or a head that disagrees with the rows, the position where they part ways
    (one past the last row when rows are missing at the end); for a row that has no chain position at all
    (an unchained row), the position it would take: one past the number of chained rows with a smaller id,
    so a forged append is reported right after the last chained row.

    ``rows_checked`` counts the rows checked and found good: all chained rows when the chain is intact,
    otherwise the rows before ``first_bad_seq`` (never negative).
    """

    ok: bool
    rows_checked: int
    first_bad_seq: int | None


def verify(conn: psycopg.Connection) -> AuditStatus:
    """Ask the database to verify the whole chain (one read-only statement)."""
    row = conn.execute("SELECT ok, rows_checked, first_bad_seq FROM public.audit_verify()").fetchone()
    if row is None:  # the function always returns exactly one row
        raise RuntimeError("audit_verify() returned no row")
    ok, rows_checked, first_bad_seq = row
    return AuditStatus(
        ok=bool(ok),
        rows_checked=int(rows_checked),
        first_bad_seq=None if first_bad_seq is None else int(first_bad_seq),
    )


def format_status(status: AuditStatus) -> str:
    """The one summary line ``gsn audit verify`` prints; "chain_seq N" is the position described on ``AuditStatus``."""
    if status.ok:
        return f"audit chain ok: {status.rows_checked} rows checked"
    where = "" if status.first_bad_seq is None else f" at chain_seq {status.first_bad_seq}"
    return f"audit chain BROKEN{where} ({status.rows_checked} rows checked)"
