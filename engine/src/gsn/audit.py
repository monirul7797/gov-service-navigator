"""Verify the ``audit_log`` hash chain (spec section 8.2) through the SQL function ``audit_verify()``.

The function runs with its owner's rights, so the worker can verify the chain without being able to read
the audit contents. Everything about the hash format lives in migration ``0003_audit_chain.sql``; this
module only asks the database and words the answer.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg


@dataclass(frozen=True)
class AuditStatus:
    """The verdict of ``audit_verify()``.

    ``rows_checked`` counts the rows checked and found good: all chained rows when the chain is intact,
    otherwise the rows before the first broken position ``first_bad_seq``.
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
    """The one summary line ``gsn audit verify`` prints."""
    if status.ok:
        return f"audit chain ok: {status.rows_checked} rows checked"
    where = "" if status.first_bad_seq is None else f" at chain_seq {status.first_bad_seq}"
    return f"audit chain BROKEN{where} ({status.rows_checked} rows checked)"
