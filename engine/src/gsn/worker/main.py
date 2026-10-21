"""Worker loop. In M0 it only proves the database is reachable by running a heartbeat query."""
from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import psycopg

log = logging.getLogger("gsn.worker")

BACKOFF_START_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0


def run_worker(
    connect: Callable[[], Any],
    sleep: Callable[[float], None] = time.sleep,
    heartbeat_seconds: float = 60.0,
    max_beats: int | None = None,
) -> None:
    """Connect (retrying while the database is not ready), then heartbeat forever.

    Connection attempts that fail with ``psycopg.OperationalError`` are retried with a delay that
    starts at 1 s, doubles each time and is capped at 30 s. After the connection is up, any error
    propagates so the process exits and the container restarts it. ``max_beats`` stops the loop
    after that many heartbeats (used by tests); ``None`` means run until stopped.
    """
    delay = BACKOFF_START_SECONDS
    while True:
        try:
            conn = connect()
            break
        except psycopg.OperationalError as exc:
            log.warning(
                "database not ready, retrying",
                extra={"retry_in_seconds": delay, "error": str(exc).strip()},
            )
            sleep(delay)
            delay = min(delay * 2, BACKOFF_CAP_SECONDS)

    try:
        log.info("worker ready")
        beat = 0
        while max_beats is None or beat < max_beats:
            conn.execute("SELECT 1")
            beat += 1
            log.info("heartbeat", extra={"beat": beat})
            sleep(heartbeat_seconds)
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            close()
