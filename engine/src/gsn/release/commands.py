"""The ``gsn release`` and ``gsn eval`` commands other than ``build``: publish, rollback, list, show, eval.

Each function takes an open connection and returns plain Python values; ``gsn.cli`` turns them into
text and exit codes. A refusal the owner can act on (no such release, a fake-model release published
by accident, a model mismatch) is a ``CommandError``; a refusal by the database is the store's
``StoreError``. Both carry a message that is safe to print.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import timezone

import psycopg

from gsn import store
from gsn.embed import Embedder
from gsn.gitsource import ContentSource, GitSource
from gsn.release import build
from gsn.release.inputs import InputError, load_inputs

DEFAULT_REPO_GIT = "/repo.git"
FAKE_MODEL_PREFIX = "fake-"

# Columns of a release row that ``show`` leaves out: the manifest and the whole concept registry are
# frozen copies of committed files (large), and the commit already identifies them.
_BULKY_COLUMNS = ("manifest", "registry")


class CommandError(Exception):
    """The command cannot be carried out as asked; the message says why and is safe to show."""


def repo_git_dir() -> str:
    """The repository's git directory: ``GSN_REPO_GIT``, else ``/repo.git`` (where compose mounts it)."""
    return os.environ.get("GSN_REPO_GIT") or DEFAULT_REPO_GIT


def open_repository(ref: str) -> GitSource:
    """Resolve ``ref`` in the mounted repository, after checking its history for secret files.

    The ``release`` container mounts the whole ``.git`` directory, so a ``setenv*.bat`` or ``.env`` that
    was ever committed would be readable from inside. That is refused before anything is read.
    Raises ``GitSourceError`` for a bad ref or an unreadable repository and ``CommandError`` for secrets.
    """
    source = GitSource(repo_git_dir(), ref)
    secrets = source.secret_paths_in_history()
    if secrets:
        listed = "\n".join(f"  {path}" for path in secrets)
        raise CommandError(
            "refusing to read this repository: its history contains files that look like secrets "
            f"(setenv*.bat, .env):\n{listed}\nremove them from the history first, or mount a clean clone"
        )
    return source


def _release(conn: psycopg.Connection, release_id: int) -> dict:
    row = store.release_row(conn, release_id)
    if row is None:
        raise CommandError(f"release {release_id} does not exist")
    return row


# --- publish and rollback -----------------------------------------------------------------------


def publish(conn: psycopg.Connection, release_id: int, actor: str, allow_fake: bool) -> None:
    """Make a validated release the active one. A release built with a fake embedder (its vectors carry
    no meaning) is refused unless ``allow_fake``; every other refusal comes from the database function."""
    row = _release(conn, release_id)
    model = row["embedding_model"]
    if model.startswith(FAKE_MODEL_PREFIX) and not allow_fake:
        raise CommandError(
            f"release {release_id} was built with the fake embedder ({model}); its answers would be "
            "meaningless. Publish it only for a test, with --allow-fake"
        )
    store.publish(conn, release_id, actor)


def rollback(conn: psycopg.Connection, actor: str) -> int:
    """Undo the latest switch; returns the release that is active now."""
    return store.rollback(conn, actor)


# --- list and show ------------------------------------------------------------------------------


def list_releases(conn: psycopg.Connection) -> list[dict]:
    return store.list_releases(conn)


def _created(value: object) -> str:
    if hasattr(value, "astimezone"):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    return str(value)


def format_release_list(rows: Sequence[dict]) -> str:
    """A fixed-width table, oldest release first; the active release is marked in the ACTIVE column."""
    if not rows:
        return "no releases yet"
    table = [("ID", "STATUS", "ACTIVE", "COMMIT", "CREATED (UTC)", "MODEL")]
    table += [
        (
            str(row["release_id"]), row["status"], "yes" if row["is_active"] else "-", row["source_commit"][:12],
            _created(row["created_at"]), row["embedding_model"],
        )
        for row in rows
    ]
    widths = [max(len(line[i]) for line in table) for i in range(len(table[0]) - 1)]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths)) + "  " + line[-1] for line in table
    )


def show(conn: psycopg.Connection, release_id: int) -> dict:
    """One release without its bulky blobs, with its record and service counts and the records added,
    removed and changed compared with the active release. A record is *changed* when the release holds a
    different version of it (same record id, different version id): that covers a new content, and also a
    derivation-only change such as a new ``verified_at_field`` mapping, where the content is the same."""
    shown = {key: value for key, value in _release(conn, release_id).items() if key not in _BULKY_COLUMNS}
    mine = store.member_version_ids(conn, release_id)
    active = store.active_release_id(conn)
    base = store.member_version_ids(conn, active) if active is not None else {}
    shown["records"] = len(mine)
    shown["live_services"] = store.live_services(conn, release_id)
    shown["active_release_id"] = active
    shown["diff_vs_active"] = {
        "added": sorted(set(mine) - set(base)),
        "removed": sorted(set(base) - set(mine)),
        "changed": sorted(record_id for record_id in set(mine) & set(base) if mine[record_id] != base[record_id]),
    }
    return shown


# --- eval ---------------------------------------------------------------------------------------


def _in_manifest_order(row: dict, live: Sequence[str]) -> list[str]:
    """The live services in the order of the release's own (frozen) manifest, which is the order the
    build judged them in, so ``gsn eval`` lists its verdicts exactly as the stored gate report does.
    ``store.live_services`` sorts by id, which is a different order. A live service the manifest does
    not name (it cannot happen for a release this code built) goes last, in id order."""
    wanted = set(live)
    ordered = [
        entry["service_id"]
        for entry in (row.get("manifest") or {}).get("services", [])
        if isinstance(entry, dict) and entry.get("service_id") in wanted
    ]
    return list(dict.fromkeys(ordered)) + sorted(wanted - set(ordered))


def eval_release(
    conn: psycopg.Connection, release_id: int, embedder: Embedder, *, source: ContentSource | None = None
) -> dict:
    """Run the gold queries of the release's live services against it and report the gate.

    The queries are read from the release's own commit (``source`` for tests; otherwise the mounted
    repository), the known failures from the thresholds frozen with the release. The embedder must be
    the one that built the release: similarities from two different models cannot be compared.
    """
    row = _release(conn, release_id)
    if embedder.model_id != row["embedding_model"]:
        raise CommandError(
            f"release {release_id} was built with {row['embedding_model']}, but the embedder is "
            f"{embedder.model_id}: scores from different models cannot be compared. Set GSN_EMBEDDER to "
            "match the release"
        )
    live = _in_manifest_order(row, store.live_services(conn, release_id))
    if source is None:
        source = open_repository(row["source_commit"])
    try:
        inputs = load_inputs(source)
    except InputError as exc:
        raise CommandError(f"the inputs of commit {row['source_commit']} cannot be read:\n" + "\n".join(
            f"  - {problem}" for problem in exc.problems)) from None
    missing = [service_id for service_id in live if service_id not in inputs.gold]
    if missing:
        raise CommandError(f"commit {row['source_commit']} has no gold queries for {', '.join(missing)}")
    known_failures = (row["thresholds"] or {}).get("known_failures", {})
    verdicts, gate = build.evaluate(conn, release_id, embedder, live, inputs.gold, known_failures)
    return {
        "release_id": release_id,
        "source_commit": row["source_commit"],
        "embedding_model": row["embedding_model"],
        **build.report_fields(verdicts, gate),
    }


def format_eval(result: dict) -> str:
    """A short text summary of an ``eval_release`` result."""
    lines = [f"release {result['release_id']} (commit {result['source_commit'][:12]}, {result['embedding_model']})"]
    for service_id, counts in result["counts"].items():
        lines.append(build.counts_line(service_id, counts))
    if result["known_failures_seen"]:
        lines.append(f"known failures (accepted): {build.listed(result['known_failures_seen'])}")
    if result["unexpected_failures"]:
        lines.append(f"unexpected failures: {build.listed(result['unexpected_failures'])}")
    lines.append("gate: passed" if result["passed"] else "gate: FAILED - a release with these results would be rejected")
    return "\n".join(lines)
