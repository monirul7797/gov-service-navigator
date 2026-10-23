"""Shared helpers of the release integration tests (not a test module: nothing here is collected).

``test_releases_schema.py`` and ``test_00_first_publication.py`` both import this module; ``conftest.py``
puts this directory on ``sys.path`` so the import also works under ``--import-mode=importlib``.

Every helper takes the ``connect`` fixture (or an open connection) and creates its own uniquely named
service and record ids, because the stack tests share one database across runs. Vectors are text
literals cast to ``vector`` (the Python side never needs a pgvector package).
"""
import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import date

from psycopg.types.json import Jsonb

DIM = 1024
UNIT_VECTOR = "[" + ",".join(["0.03125"] * DIM) + "]"  # (1/32)^2 * 1024 = 1: unit length
COMMIT = "0123456789abcdef0123456789abcdef01234567"
MODEL = "fake-sha256-v1"


def new_tag():
    return uuid.uuid4().hex[:8]


def new_actor():
    """A unique actor per test: audit_log rows can never be deleted, so tests only add rows."""
    return f"itest-{uuid.uuid4().hex}"


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Built:
    release_id: int
    service_id: str
    record_ids: tuple
    version_ids: tuple
    tag: str


def add_version(conn, record_id, *, text, content_hash=None, fields_hash=None, url_kind="portal",
                source_commit=COMMIT, embedding=UNIT_VECTOR, model=MODEL, source_url=None,
                verified_at=date(2026, 7, 12)):
    """Insert one ``record_versions`` row (as whatever role ``conn`` is) and return its ``version_id``."""
    row = conn.execute(
        "INSERT INTO record_versions (record_id, content_hash, fields_hash, text_hash, hash_version, record, "
        "embed_text, category, subcategory, heading, lang, source_url, url_kind, official, source_verified, "
        "verified_at, embedding, embedding_model, source_commit, created_by, change_note) "
        "VALUES (%s, %s, %s, %s, 1, %s, %s, 'process_steps', NULL, 'Heading', 'en', %s, %s, true, true, %s, "
        "%s::vector, %s, %s, 'itest', NULL) RETURNING version_id",
        (
            record_id,
            content_hash or sha256_hex(text),
            fields_hash or sha256_hex(f"fields of {record_id}"),
            sha256_hex(text),
            Jsonb({"id": record_id, "text": text}),
            text,
            source_url or f"https://example.test/{record_id}",
            url_kind,
            verified_at,
            embedding,
            model,
            source_commit,
        ),
    ).fetchone()
    return row[0]


def add_record(conn, service_id, record_id, *, text):
    """Insert a record and its first version; returns ``(record_id, version_id)``."""
    conn.execute("INSERT INTO records (record_id, service_id) VALUES (%s, %s)", (record_id, service_id))
    return record_id, add_version(conn, record_id, text=text)


def add_release(conn, service_id, tag, members, *, service_status="live"):
    """A draft release of one service holding ``members`` (``(version_id, record_id)`` pairs); returns its id."""
    release_id = conn.execute(
        "INSERT INTO releases (source_commit, manifest, registry, registry_sha256, thresholds, "
        "embedding_model, note, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, 'itest') RETURNING release_id",
        (
            COMMIT,
            Jsonb({"services": [{"service_id": service_id}]}),
            Jsonb({"services": {service_id: {"names": ["a", "b"]}}}),
            "f" * 64,
            Jsonb({"known_failures": []}),
            MODEL,
            f"itest {tag}",
        ),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO release_services (release_id, service_id, status) VALUES (%s, %s, %s)",
        (release_id, service_id, service_status),
    )
    for version_id, record_id in members:
        conn.execute(
            "INSERT INTO release_members (release_id, version_id, record_id) VALUES (%s, %s, %s)",
            (release_id, version_id, record_id),
        )
    return release_id


def make_draft_release(connect, n_records=2, *, service_status="live"):
    """A service with ``n_records`` records (one version each) and a draft release holding them, as the worker."""
    worker = connect("worker")
    tag = new_tag()
    service_id = f"svc_{tag}"
    record_ids, version_ids = [], []
    with worker.transaction():
        worker.execute(
            "INSERT INTO services (service_id, name_bn, name_en) VALUES (%s, %s, %s)",
            (service_id, f"সেবা {tag}", f"Service {tag}"),
        )
        for i in range(n_records):
            record_id, version_id = add_record(worker, service_id, f"rec_{tag}_{i}", text=f"text {i} {tag}")
            record_ids.append(record_id)
            version_ids.append(version_id)
        release_id = add_release(worker, service_id, tag, list(zip(version_ids, record_ids)),
                                 service_status=service_status)
    return Built(release_id, service_id, tuple(record_ids), tuple(version_ids), tag)


def set_status(conn, release_id, status, *, override_reason=None):
    conn.execute(
        "UPDATE releases SET status = %s, gate_report = %s, override_reason = %s WHERE release_id = %s",
        (status, Jsonb({"status": status}), override_reason, release_id),
    )


def make_validated_release(connect, n_records=2, *, service_status="live"):
    """``make_draft_release`` and then the gate's verdict: the release becomes ``validated``."""
    built = make_draft_release(connect, n_records, service_status=service_status)
    set_status(connect("worker"), built.release_id, "validated")
    return built


def publish(conn, release_id, actor):
    conn.execute("SELECT publish_release(%s, %s)", (release_id, actor))


def rollback(conn, actor):
    return conn.execute("SELECT rollback_release(%s)", (actor,)).fetchone()[0]


def active_release_id(conn):
    row = conn.execute("SELECT release_id FROM active_release").fetchone()
    return row[0] if row else None


def last_publication_id(conn):
    return conn.execute("SELECT coalesce(max(publication_id), 0) FROM publications").fetchone()[0]


def publications_after(conn, publication_id):
    return conn.execute(
        "SELECT release_id, previous_release_id, action, actor FROM publications "
        "WHERE publication_id > %s ORDER BY publication_id",
        (publication_id,),
    ).fetchall()


def audit_rows(conn, actor):
    return conn.execute(
        "SELECT action, target, details FROM audit_log WHERE actor = %s ORDER BY id", (actor,)
    ).fetchall()


def wait_until_a_statement_waits_for_a_lock(watcher, text, timeout=10):
    """Poll until another session's statement containing ``text`` is blocked on a lock; the number of such sessions."""
    deadline = time.monotonic() + timeout
    waiting = 0
    while time.monotonic() < deadline and not waiting:
        waiting = watcher.execute(
            "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
            "AND wait_event_type = 'Lock' AND query LIKE %s AND pid <> pg_backend_pid()",
            (f"%{text}%",),
        ).fetchone()[0]
        time.sleep(0.05)
    return waiting
