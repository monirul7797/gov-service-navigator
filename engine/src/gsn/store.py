"""The release store: every SQL statement the release CLI runs as ``gsn_worker``.

Keeping the SQL in one module means the grants of the worker role (migration 0002) can be audited
against exactly one file, and the build logic above it (``gsn.release.build``) never writes a query.

Conventions:

* Every function takes an open psycopg connection and returns plain Python values. Rows are read
  through explicit row factories, so the result does not depend on the connection's own.
* The connection should be in autocommit mode (as ``gsn.cli`` opens it). A function that writes does
  its work inside ``conn.transaction()``. On an autocommit connection that is one real transaction,
  committed when the function returns. On a connection that is not in autocommit mode it joins the
  transaction the caller already has open (a savepoint: nothing is committed until the caller commits)
  or, when none is open, begins and commits its own.
* Vectors travel as pgvector text (``'[x,y,...]'::vector``, each component written with ``repr``) and
  come back by parsing the text pgvector prints. No pgvector or numpy package is involved.
* A write that PostgreSQL refuses (a state rule such as "release 7 is frozen", a constraint, a missing
  privilege or release) raises ``StoreError`` carrying the server's message text, never connection
  details. Only an error the server reports with a SQLSTATE can be a refusal, and not one that
  describes the session or the system instead of the statement (classes 08 connection, 40 transaction
  rollback, 53 resources, 57 operator intervention, 58 system, XX internal): a dropped connection, a
  server shutdown, a cancelled query or a deadlock reaches the caller as psycopg's own exception, and
  so does every error of the read functions. A crash is therefore never mistaken for a refusal.
* Release content is never edited: versions are appended, a release is assembled as a draft, judged
  once (``set_gate_result``) and switched on or off only by the SQL functions ``publish_release`` and
  ``rollback_release`` (``publish`` and ``rollback`` here). The worker has no other way to touch
  ``active_release`` or ``publications``.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row, tuple_row
from psycopg.types.json import Jsonb

from gsn.embed import DIMENSIONS
from gsn.hashing import HASH_VERSION
from gsn.release.fields import VersionFields
from gsn.release.inputs import ServiceEntry
from gsn.retrieval import Hit

__all__ = [
    "Draft", "Hit", "NewVersion", "StoreError", "active_release_id", "audit", "create_draft", "existing_vectors",
    "existing_versions", "list_releases", "live_services", "member_hashes", "publish", "release_row", "rollback",
    "search", "set_gate_result", "stored_record_services", "stored_service_names", "stored_vectors",
    "vector_from_text", "vector_to_text",
]

_VERDICTS = ("validated", "rejected")


class StoreError(Exception):
    """A draft is inconsistent, or PostgreSQL refused a write; the message is safe to show the owner."""


@dataclass(frozen=True)
class NewVersion:
    """A record version that does not exist yet: the record, its derived columns and its vector."""

    record_id: str
    service_id: str
    record: dict
    embed_text: str
    content_hash: str
    fields_hash: str
    text_hash: str
    fields: VersionFields
    embedding: list[float]


@dataclass(frozen=True)
class Draft:
    """Everything one draft release is made of.

    ``member_version_ids`` holds the REUSED members (``record_id -> version_id`` of a version that is
    already stored). Every entry of ``new_versions`` is inserted and also becomes a member. A record
    id may therefore appear in only one of the two.
    """

    source_commit: str
    manifest_json: dict
    registry: dict
    registry_sha256: str
    thresholds_json: dict
    embedding_model: str
    note: str | None
    created_by: str
    services: list[ServiceEntry]
    member_version_ids: dict[str, int]
    new_versions: list[NewVersion]


# --- plumbing ----------------------------------------------------------------------------------


# SQLSTATE classes that describe the session or the system rather than the statement. The server sends
# them with a code too (a terminated backend reports 57P01 before it closes the connection), but they
# are crashes to report, not refusals to explain.
_NOT_REFUSALS = frozenset({"08", "40", "53", "57", "58", "XX"})


def _is_refusal(exc: psycopg.Error) -> bool:
    """True if the server turned the statement down: it sent a SQLSTATE, and not one of a failure class."""
    state = exc.sqlstate
    return state is not None and state[:2] not in _NOT_REFUSALS


@contextmanager
def _refusals_as_store_errors() -> Iterator[None]:
    """Turn a refusal by the server into ``StoreError(<the server's message>)``; let anything else through.

    Only the primary message text is kept: psycopg's own exception text can carry connection details,
    and the traceback chain is dropped (``from None``) for the same reason. An error that is not a
    refusal (see ``_is_refusal``: no SQLSTATE, or a connection / resource / shutdown class) is re-raised
    unchanged, so callers never report a crash as a refusal.
    """
    try:
        yield
    except psycopg.Error as exc:
        if not _is_refusal(exc):
            raise
        raise StoreError(exc.diag.message_primary or f"database error (SQLSTATE {exc.sqlstate})") from None


def _fetch(conn: psycopg.Connection, sql: str, params: Any = None, *, row_factory: Any = tuple_row) -> list:
    with conn.cursor(row_factory=row_factory) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def _run(conn: psycopg.Connection, sql: str, params: Any = None) -> int:
    """Execute a statement that returns no rows; the number of rows it changed."""
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def vector_to_text(vector: Sequence[float]) -> str:
    """The pgvector text form of a vector: ``[x,y,...]`` with ``repr`` of each component."""
    if len(vector) != DIMENSIONS:
        raise ValueError(f"a vector has {DIMENSIONS} components, got {len(vector)}")
    parts = []
    for component in vector:
        if not math.isfinite(component):
            raise ValueError("vector components must be finite numbers")
        parts.append(repr(float(component)))
    return "[" + ",".join(parts) + "]"


def vector_from_text(text: str) -> list[float]:
    """Parse the text pgvector prints for a ``vector`` value (``[x,y,...]``)."""
    text = text.strip()
    if not (text.startswith("[") and text.endswith("]")):
        raise ValueError("not a pgvector value")
    return [float(part) for part in text[1:-1].split(",")]


# --- what is already stored --------------------------------------------------------------------


def stored_record_services(conn: psycopg.Connection) -> dict[str, str]:
    """``record_id -> service_id`` of every stored record (a record never changes service)."""
    return dict(_fetch(conn, "SELECT record_id, service_id FROM records"))


def stored_service_names(conn: psycopg.Connection) -> dict[str, tuple[str, str]]:
    """``service_id -> (name_bn, name_en)`` of every stored service (names never change)."""
    return {sid: (bn, en) for sid, bn, en in _fetch(conn, "SELECT service_id, name_bn, name_en FROM services")}


def existing_versions(
    conn: psycopg.Connection, keys: Iterable[tuple[str, str, str]], model: str
) -> dict[str, int]:
    """``record_id -> version_id`` for each ``(record_id, content_hash, fields_hash)`` stored for ``model``.

    A version is identified by the record's content AND the columns derived from it, so a record whose
    content is unchanged but whose derived columns are (a new manifest mapping, a new ``url_kind`` rule)
    is not found. A record given two different keys is a ``ValueError``: the result could hold only one
    of them.
    """
    wanted: dict[str, tuple[str, str]] = {}
    for record_id, chash, fhash in keys:
        if wanted.setdefault(record_id, (chash, fhash)) != (chash, fhash):
            raise ValueError(f"record {record_id!r} was given two different content or fields hashes")
    if not wanted:
        return {}
    rows = _fetch(
        conn,
        "SELECT v.record_id, v.version_id FROM record_versions AS v "
        "JOIN unnest(%s::text[], %s::text[], %s::text[]) AS k (record_id, content_hash, fields_hash) "
        "ON k.record_id = v.record_id AND k.content_hash = v.content_hash AND k.fields_hash = v.fields_hash "
        "WHERE v.embedding_model = %s",
        (list(wanted), [h[0] for h in wanted.values()], [h[1] for h in wanted.values()], model),
    )
    return {record_id: version_id for record_id, version_id in rows}


def existing_vectors(conn: psycopg.Connection, text_hashes: Iterable[str], model: str) -> dict[str, list[float]]:
    """``text_hash -> vector`` for each text that already has a stored vector from ``model``.

    The same text and model always give the same vector, so any stored one will do (the oldest is used).
    """
    hashes = sorted(set(text_hashes))
    if not hashes:
        return {}
    rows = _fetch(
        conn,
        "SELECT DISTINCT ON (text_hash) text_hash, embedding::text FROM record_versions "
        "WHERE text_hash = ANY(%s) AND embedding_model = %s ORDER BY text_hash, version_id",
        (hashes, model),
    )
    return {text_hash: vector_from_text(vector) for text_hash, vector in rows}


# --- building a draft --------------------------------------------------------------------------


def create_draft(conn: psycopg.Connection, draft: Draft) -> int:
    """Insert a draft release and everything it needs, in one transaction; returns its ``release_id``.

    Missing services and records are inserted (``ON CONFLICT DO NOTHING``: an existing one is kept),
    then the new versions (their ``change_note`` is the draft's ``note``, which may be ``None``; a
    reused version keeps the note it was created with), the release itself (always as a ``draft``), its
    services and its members (the reused versions plus the new ones). If anything fails, nothing is
    left behind.

    A record that already exists under another service, or a member of a service the release does not
    list, is a ``StoreError`` (``DO NOTHING`` would otherwise let the first one pass unnoticed).

    Judging the draft is a separate step: do not call ``set_gate_result`` inside the transaction that
    created it. Inserting members takes a ``FOR SHARE`` lock on the release row until the transaction
    ends, and changing that row's status while holding it can deadlock against another session that
    holds the same share lock.
    """
    overlap = sorted(set(draft.member_version_ids) & {v.record_id for v in draft.new_versions})
    if overlap:
        raise StoreError("a record is either reused or new, not both: " + ", ".join(overlap))
    with _refusals_as_store_errors(), conn.transaction():
        return _insert_draft(conn, draft)


def _insert_draft(conn: psycopg.Connection, draft: Draft) -> int:
    listed = {entry.service_id for entry in draft.services}
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(e.service_id, e.name_bn, e.name_en) for e in draft.services],
        )
        cur.executemany(
            "INSERT INTO records (record_id, service_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(v.record_id, v.service_id) for v in draft.new_versions],
        )

    member_ids = [*draft.member_version_ids, *(v.record_id for v in draft.new_versions)]
    stored = dict(_fetch(conn, "SELECT record_id, service_id FROM records WHERE record_id = ANY(%s)", (member_ids,)))
    for v in draft.new_versions:
        if stored.get(v.record_id) != v.service_id:
            raise StoreError(
                f"record {v.record_id} is stored under service {stored.get(v.record_id)}, "
                f"not {v.service_id}: a record never changes service"
            )
    for record_id, service_id in sorted(stored.items()):
        if service_id not in listed:
            raise StoreError(f"record {record_id} belongs to service {service_id}, which the release does not list")

    members = [(version_id, record_id) for record_id, version_id in draft.member_version_ids.items()]
    for v in draft.new_versions:
        f = v.fields
        (version_id,) = _fetch(
            conn,
            "INSERT INTO record_versions (record_id, content_hash, fields_hash, text_hash, hash_version, record, "
            "embed_text, category, subcategory, heading, lang, source_url, url_kind, official, source_verified, "
            "verified_at, embedding, embedding_model, source_commit, created_by, change_note) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s) "
            "RETURNING version_id",
            (
                v.record_id, v.content_hash, v.fields_hash, v.text_hash, HASH_VERSION, Jsonb(v.record), v.embed_text,
                f.category, f.subcategory, f.heading, f.lang, f.source_url, f.url_kind, f.official,
                f.source_verified, f.verified_at, vector_to_text(v.embedding), draft.embedding_model,
                draft.source_commit, draft.created_by, draft.note,
            ),
        )[0]
        members.append((version_id, v.record_id))

    (release_id,) = _fetch(
        conn,
        "INSERT INTO releases (source_commit, manifest, registry, registry_sha256, thresholds, embedding_model, "
        "note, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING release_id",
        (
            draft.source_commit, Jsonb(draft.manifest_json), Jsonb(draft.registry), draft.registry_sha256,
            Jsonb(draft.thresholds_json), draft.embedding_model, draft.note, draft.created_by,
        ),
    )[0]
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO release_services (release_id, service_id, status) VALUES (%s, %s, %s)",
            [(release_id, e.service_id, e.status) for e in draft.services],
        )
        cur.executemany(
            "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
            [(release_id, version_id, record_id) for version_id, record_id in members],
        )
    return int(release_id)


def set_gate_result(
    conn: psycopg.Connection, release_id: int, status: str, gate_report: dict, override_reason: str | None
) -> None:
    """Give a draft its verdict: ``validated`` or ``rejected``, with the gate report and, when the
    owner overrode a failing gate, the reason. A release is judged once; after that it is frozen.

    Run it as its own transaction, never inside the one that called ``create_draft`` (see there).
    """
    if status not in _VERDICTS:
        raise ValueError(f"status must be one of {', '.join(_VERDICTS)}, got {status!r}")
    with _refusals_as_store_errors(), conn.transaction():
        changed = _run(
            conn,
            "UPDATE releases SET status = %s, gate_report = %s, override_reason = %s WHERE release_id = %s",
            (status, Jsonb(gate_report), override_reason, release_id),
        )
        if changed != 1:
            raise StoreError(f"release {release_id} does not exist")


# --- searching a release -----------------------------------------------------------------------

_SEARCH_SQL = """
SELECT record_id, service_id, 1 - distance AS sim, embed_text, category, subcategory, heading, source_url
FROM (
    SELECT v.record_id, rec.service_id, v.embedding <=> %(query)s::vector AS distance,
           v.embed_text, v.category, v.subcategory, v.heading, v.source_url
    FROM release_members AS m
    JOIN record_versions AS v ON v.version_id = m.version_id AND v.record_id = m.record_id
    JOIN records AS rec ON rec.record_id = v.record_id
    WHERE m.release_id = %(release_id)s AND rec.service_id = %(service_id)s
) AS candidates
ORDER BY distance, record_id COLLATE "C"
LIMIT %(k)s
"""


def search(conn: psycopg.Connection, release_id: int, service_id: str, vector: Sequence[float], k: int) -> list[Hit]:
    """At most ``k`` records of one service in one release, nearest to ``vector`` first.

    Only members of that release and service are considered. ``sim`` is ``1 - cosine distance``;
    equal distances come back in ``record_id`` order, so the result is deterministic. That order is
    by code point (``COLLATE "C"``), like ``sorted()`` and ``gsn.retrieval``, whatever the database's
    default collation does with hyphens and case. ``Hit.text`` is the record's ``embed_text``. (The
    search is exact: there is no approximate index.)
    """
    if k < 0:
        raise ValueError(f"k must not be negative, got {k}")
    query = vector_to_text(vector)
    if k == 0:
        return []
    rows = _fetch(conn, _SEARCH_SQL, {"query": query, "release_id": release_id, "service_id": service_id, "k": k})
    return [Hit(record_id, sid, sim, text, category, subcategory, heading, url)
            for record_id, sid, sim, text, category, subcategory, heading, url in rows]


# --- looking at releases -----------------------------------------------------------------------


def release_row(conn: psycopg.Connection, release_id: int) -> dict | None:
    """Every column of one release, plus ``is_active``; ``None`` if there is no such release."""
    rows = _fetch(
        conn,
        "SELECT r.*, (a.release_id IS NOT NULL) AS is_active FROM releases AS r "
        "LEFT JOIN active_release AS a ON a.release_id = r.release_id WHERE r.release_id = %s",
        (release_id,),
        row_factory=dict_row,
    )
    return rows[0] if rows else None


def list_releases(conn: psycopg.Connection) -> list[dict]:
    """All releases, oldest first: ``release_id``, ``status``, ``source_commit``, ``created_at``,
    ``embedding_model`` and ``is_active``."""
    return _fetch(
        conn,
        "SELECT r.release_id, r.status, r.source_commit, r.created_at, r.embedding_model, "
        "(a.release_id IS NOT NULL) AS is_active FROM releases AS r "
        "LEFT JOIN active_release AS a ON a.release_id = r.release_id ORDER BY r.release_id",
        row_factory=dict_row,
    )


def member_hashes(conn: psycopg.Connection, release_id: int) -> dict[str, str]:
    """``record_id -> content_hash`` of the version each record has in the release."""
    return dict(_fetch(
        conn,
        "SELECT m.record_id, v.content_hash FROM release_members AS m "
        "JOIN record_versions AS v ON v.version_id = m.version_id AND v.record_id = m.record_id "
        "WHERE m.release_id = %s ORDER BY m.record_id",
        (release_id,),
    ))


def live_services(conn: psycopg.Connection, release_id: int) -> list[str]:
    """The services the release serves (status ``live``), by id."""
    return [row[0] for row in _fetch(
        conn,
        "SELECT service_id FROM release_services WHERE release_id = %s AND status = 'live' ORDER BY service_id",
        (release_id,),
    )]


def active_release_id(conn: psycopg.Connection) -> int | None:
    """The release the engine is serving, or ``None`` if nothing has been published."""
    rows = _fetch(conn, "SELECT release_id FROM active_release")
    return rows[0][0] if rows else None


def stored_vectors(conn: psycopg.Connection, release_id: int) -> dict[str, list[float]]:
    """``record_id -> vector`` as stored for the release's members (single-precision values)."""
    return {
        record_id: vector_from_text(vector)
        for record_id, vector in _fetch(
            conn,
            "SELECT m.record_id, v.embedding::text FROM release_members AS m "
            "JOIN record_versions AS v ON v.version_id = m.version_id AND v.record_id = m.record_id "
            "WHERE m.release_id = %s ORDER BY m.record_id",
            (release_id,),
        )
    }


# --- publishing and the audit log --------------------------------------------------------------


def publish(conn: psycopg.Connection, release_id: int, actor: str) -> None:
    """Make a validated release the active one (SQL function ``publish_release``, which also records
    the publication and its audit row). Raises ``StoreError`` with the database's message, e.g.
    ``release 7 is not validated`` or ``release 7 is already active``."""
    with _refusals_as_store_errors(), conn.transaction():
        _run(conn, "SELECT public.publish_release(%s::bigint, %s::text)", (release_id, actor))


def rollback(conn: psycopg.Connection, actor: str) -> int:
    """Undo the latest switch (SQL function ``rollback_release``); returns the release now active.
    Raises ``StoreError`` with the database's message, e.g. ``nothing to roll back``."""
    with _refusals_as_store_errors(), conn.transaction():
        rows = _fetch(conn, "SELECT public.rollback_release(%s::text)", (actor,))
    return int(rows[0][0])


def audit(conn: psycopg.Connection, actor: str, action: str, target: str, details: dict) -> None:
    """Append one row to the audit log."""
    with _refusals_as_store_errors(), conn.transaction():
        _run(
            conn,
            "INSERT INTO audit_log (actor, action, target, details) VALUES (%s, %s, %s, %s)",
            (actor, action, target, Jsonb(details)),
        )
