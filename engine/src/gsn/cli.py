"""Command line entry point: ``gsn worker``, ``gsn engine``, ``gsn release ...``, ``gsn eval`` and
``gsn audit verify``.

``gsn release``, ``gsn eval`` and ``gsn audit`` are run by hand (in the one-off ``release`` container) and
print plain text for the owner. Exit codes: 0 success (a build that is validated, an intact audit chain,
or any other command that worked), 1 runtime error (database down, a bug; for ``audit verify`` also a
broken chain), 2 usage or configuration error or a refusal (a bad argument or ref, a missing ``GSN_DB_*``
variable, an embedder that cannot be made, secret files in the repository's history, a database refusal
such as "release 7 is not validated"), 3 a build that finished but was rejected (or an eval whose gate
would reject), 4 integrity problems (nothing written).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import traceback
from collections.abc import Callable, Sequence

import psycopg

from gsn import audit, logs, store
from gsn.db import connect_kwargs
from gsn.embed import Embedder, get_embedder
from gsn.gitsource import GitSourceError
from gsn.release import commands
from gsn.release.build import INTEGRITY_FAILED, build_release, dhaka_today, format_build_summary
from gsn.release.commands import CommandError
from gsn.worker.main import run_worker

CONNECT_TIMEOUT_SECONDS = 10

EXIT_REJECTED = 3
EXIT_INTEGRITY = 4


def _exit_on_sigterm(_signum: int, _frame: object) -> None:
    # Python running as a container's main process ignores SIGTERM unless a handler is installed,
    # which would make ``docker stop`` wait for the 10 s kill timeout.
    raise SystemExit(0)


def _serve_engine(host: str, port: int) -> None:
    """Run the engine app under uvicorn with every log line going through ``gsn.logs``.

    ``log_config=None`` stops uvicorn from installing its own plain-text handlers, so its startup and
    error messages propagate to the JSON root handler. The access log is off: its lines carry the
    request's query string (which can hold personal data) and /healthz would repeat every 10 s.
    """
    import uvicorn  # imported here so ``gsn worker`` does not load the web stack

    from gsn.engine.app import app

    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)


class UsageError(Exception):
    """A configuration or usage problem the owner fixes by changing the command or the environment."""


def _non_empty(text: str) -> str:
    if not text.strip():
        raise argparse.ArgumentTypeError("must not be empty")
    return text


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gsn", description="Government Service Navigator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("worker", help="run the background worker")
    engine = sub.add_parser("engine", help="run the engine HTTP service")
    engine.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    engine.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")

    release = sub.add_parser("release", help="build, publish and inspect data releases")
    rsub = release.add_subparsers(dest="release_command", required=True, metavar="COMMAND")
    build = rsub.add_parser("build", help="build and judge a release from a commit of the repository")
    build.add_argument("--commit", default="HEAD", metavar="REF", help="branch, tag or SHA to build (default: HEAD)")
    build.add_argument("--note", help="free text stored with the release")
    build.add_argument("--override", type=_non_empty, metavar="REASON",
                       help="validate a release whose gold queries failed; the reason is stored and audited")
    publish = rsub.add_parser("publish", help="make a validated release the active one")
    publish.add_argument("release_id", type=int, metavar="ID")
    publish.add_argument("--allow-fake", action="store_true",
                         help="publish a release built with the fake embedder (tests only)")
    rsub.add_parser("rollback", help="undo the latest publish or rollback")
    rsub.add_parser("list", help="list all releases")
    show = rsub.add_parser("show", help="print one release as JSON, with its changes against the active release")
    show.add_argument("release_id", type=int, metavar="ID")

    evaluate = sub.add_parser("eval", help="run the gold queries against a release")
    evaluate.add_argument("--release", dest="release_id", type=int, required=True, metavar="ID")
    evaluate.add_argument("--json", action="store_true", help="print the full result as JSON")

    audit_parser = sub.add_parser("audit", help="inspect the audit log")
    asub = audit_parser.add_subparsers(dest="audit_command", required=True, metavar="COMMAND")
    verify_help = (
        "check the audit log's hash chain; every row must be chained (exit 0 when intact, 1 when broken, "
        "naming the first bad chain position; an unchained row counts as a break at the position it would take)"
    )
    asub.add_parser("verify", help=verify_help, description=verify_help)
    return parser


def _db_kwargs() -> dict:
    try:
        return connect_kwargs(os.environ)
    except ValueError as exc:  # names the missing variable, never a value
        raise UsageError(str(exc)) from None


def _make_embedder() -> Embedder:
    try:
        return get_embedder(os.environ)
    except (NotImplementedError, ValueError) as exc:
        raise UsageError(f"cannot make the embedder: {exc}") from None


def _actor() -> str:
    return os.environ.get("GSN_ACTOR") or "owner"


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


def _run_build(args: argparse.Namespace, connect: Callable[[], psycopg.Connection]) -> int:
    # The repository is opened (ref resolved, history scanned for secrets) before the model is loaded or
    # the database touched: a bad ref or a dirty history costs nothing.
    source = commands.open_repository(args.commit)
    conn = connect()
    try:
        result = build_release(
            conn, source, _make_embedder(), actor=_actor(), note=args.note, override=args.override,
            today=dhaka_today(),
        )
    finally:
        conn.close()
    print(format_build_summary(result))
    if result.status == INTEGRITY_FAILED:
        return EXIT_INTEGRITY
    return EXIT_REJECTED if result.status == "rejected" else 0


def _run_eval(args: argparse.Namespace, connect: Callable[[], psycopg.Connection]) -> int:
    conn = connect()
    try:
        result = commands.eval_release(conn, args.release_id, _make_embedder())
    finally:
        conn.close()
    print(_dump(result) if args.json else commands.format_eval(result))
    return 0 if result["passed"] else EXIT_REJECTED


def _run_simple(args: argparse.Namespace, connect: Callable[[], psycopg.Connection]) -> int:
    conn = connect()
    try:
        name = args.release_command
        if name == "publish":
            commands.publish(conn, args.release_id, _actor(), args.allow_fake)
            print(f"release {args.release_id} is now the active release")
        elif name == "rollback":
            print(f"rolled back: release {commands.rollback(conn, _actor())} is the active release again")
        elif name == "list":
            print(commands.format_release_list(commands.list_releases(conn)))
        else:  # show
            print(_dump(commands.show(conn, args.release_id)))
    finally:
        conn.close()
    return 0


def _run_audit_verify(connect: Callable[[], psycopg.Connection]) -> int:
    conn = connect()
    try:
        status = audit.verify(conn)
    finally:
        conn.close()
    print(audit.format_status(status))
    return 0 if status.ok else 1


def _run_guarded(run: Callable[[Callable[[], psycopg.Connection]], int]) -> int:
    """Run a hand-run command with a connection factory and turn every outcome into an exit code (see the module doc)."""
    try:
        kwargs = _db_kwargs()

        def connect() -> psycopg.Connection:
            # autocommit: the store opens its own short transactions (see gsn.store).
            return psycopg.connect(**kwargs, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS)

        return run(connect)
    except (UsageError, CommandError, store.StoreError, GitSourceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except psycopg.Error as exc:  # database down, failing statement: psycopg's message names no password
        print(f"error: database: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # a bug: keep the traceback for the owner to report
        traceback.print_exc()
        print(f"error: unexpected {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _run_release_command(args: argparse.Namespace) -> int:
    """Run ``gsn release ...`` or ``gsn eval``."""

    def run(connect: Callable[[], psycopg.Connection]) -> int:
        if args.command == "eval":
            return _run_eval(args, connect)
        if args.release_command == "build":
            return _run_build(args, connect)
        return _run_simple(args, connect)

    return _run_guarded(run)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command in ("release", "eval"):
        return _run_release_command(args)

    if args.command == "audit":  # only ``verify`` exists
        return _run_guarded(_run_audit_verify)

    if args.command == "engine":
        logs.configure("engine")
        # uvicorn installs its own SIGTERM/SIGINT handling and shuts down gracefully.
        _serve_engine(args.host, args.port)
        return 0

    if args.command == "worker":
        logs.configure("worker")
        try:
            kwargs = connect_kwargs(os.environ)
        except ValueError as exc:
            logging.getLogger("gsn.cli").error("configuration error", extra={"error": str(exc)})
            return 2
        signal.signal(signal.SIGTERM, _exit_on_sigterm)
        # autocommit: the heartbeat must not leave a transaction open between beats.
        try:
            run_worker(
                lambda: psycopg.connect(**kwargs, autocommit=True, connect_timeout=CONNECT_TIMEOUT_SECONDS)
            )
        except Exception:
            # One JSON line (with an "exc" field) instead of a raw traceback; non-zero so Docker restarts us.
            logging.getLogger("gsn.cli").exception("worker stopped")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
