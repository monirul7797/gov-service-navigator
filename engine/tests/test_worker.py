import logging

import psycopg
import pytest

from gsn.worker.main import run_worker


class FakeConn:
    def __init__(self, fail_on_execute_call=None):
        self.executed = []
        self._fail_on = fail_on_execute_call

    def execute(self, sql):
        self.executed.append(sql)
        if self._fail_on is not None and len(self.executed) == self._fail_on:
            raise psycopg.OperationalError("connection lost")


def _connector(failures, conn):
    state = {"calls": 0}

    def connect():
        state["calls"] += 1
        if state["calls"] <= failures:
            raise psycopg.OperationalError("db not ready")
        return conn

    return connect


def test_retries_with_backoff_then_heartbeats():
    conn = FakeConn()
    sleeps = []
    run_worker(_connector(2, conn), sleep=sleeps.append, heartbeat_seconds=60.0, max_beats=2)
    assert sleeps == [1.0, 2.0, 60.0, 60.0]
    assert conn.executed == ["SELECT 1", "SELECT 1"]


def test_backoff_caps_at_30_seconds():
    conn = FakeConn()
    sleeps = []
    run_worker(_connector(7, conn), sleep=sleeps.append, heartbeat_seconds=60.0, max_beats=1)
    assert sleeps[:7] == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert sleeps[7:] == [60.0]


def test_error_after_ready_propagates():
    conn = FakeConn(fail_on_execute_call=2)
    with pytest.raises(psycopg.OperationalError):
        run_worker(lambda: conn, sleep=lambda _s: None, heartbeat_seconds=1.0, max_beats=5)
    assert conn.executed == ["SELECT 1", "SELECT 1"]


def test_non_operational_connect_error_is_not_swallowed():
    def connect():
        raise ValueError("bad config")

    sleeps = []
    with pytest.raises(ValueError):
        run_worker(connect, sleep=sleeps.append, max_beats=1)
    assert sleeps == []


def test_logs_ready_and_numbered_heartbeats(caplog):
    caplog.set_level(logging.INFO)
    run_worker(lambda: FakeConn(), sleep=lambda _s: None, heartbeat_seconds=1.0, max_beats=2)
    msgs = [r.getMessage() for r in caplog.records]
    assert "worker ready" in msgs
    beats = [r.beat for r in caplog.records if r.getMessage() == "heartbeat"]
    assert beats == [1, 2]
    assert msgs.index("worker ready") < msgs.index("heartbeat")


def test_connection_failures_are_logged(caplog):
    caplog.set_level(logging.INFO)
    run_worker(_connector(1, FakeConn()), sleep=lambda _s: None, max_beats=1)
    retries = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(retries) == 1
    assert retries[0].retry_in_seconds == 1.0
    assert "db not ready" in retries[0].error
