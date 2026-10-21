"""Command line entry point: ``gsn worker``."""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
from collections.abc import Sequence

import psycopg

from gsn import logs
from gsn.db import connect_kwargs
from gsn.worker.main import run_worker

CONNECT_TIMEOUT_SECONDS = 10


def _exit_on_sigterm(_signum: int, _frame: object) -> None:
    # Python running as a container's main process ignores SIGTERM unless a handler is installed,
    # which would make ``docker stop`` wait for the 10 s kill timeout.
    raise SystemExit(0)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="gsn", description="Government Service Navigator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("worker", help="run the background worker")
    args = parser.parse_args(argv)

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
