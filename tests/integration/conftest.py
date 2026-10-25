"""Fixtures for the stack-level integration tests (run by the ``itest`` service in compose.test.yaml).

The tests talk to the other containers over the compose network. Database settings come from
separate environment variables, never a URL, so a password with ``@ / : # % ?`` needs no escaping:

    GSN_DB_HOST (default postgres), GSN_DB_PORT (5432), GSN_DB_NAME (gsn) and one password per role:
    GSN_DB_MIGRATE_PASSWORD, GSN_DB_WEB_PASSWORD, GSN_DB_ENGINE_PASSWORD, GSN_DB_WORKER_PASSWORD.
"""
import os
import sys
from pathlib import Path

import psycopg
import pytest

# Helper modules next to the tests (release_helpers.py) are imported by name. pytest's default
# import mode adds each test directory to sys.path; --import-mode=importlib does not, so do it here.
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROLES = ("migrate", "web", "engine", "worker")


@pytest.fixture
def connect():
    """``connect(role)`` opens an autocommit connection as ``gsn_<role>``; all are closed afterwards.

    A failed connection raises ``ConnectionError`` with no password in the message or traceback
    (see ``test_connection_errors.py``).

    Autocommit means a statement that PostgreSQL refuses does not leave the session in an aborted
    transaction, so one test can try several forbidden statements on one connection.
    """
    opened = []

    def _connect(role):
        assert role in ROLES, role
        password = os.environ.get(f"GSN_DB_{role.upper()}_PASSWORD")
        assert password, f"GSN_DB_{role.upper()}_PASSWORD is not set"
        try:
            connection = psycopg.connect(
                host=os.environ.get("GSN_DB_HOST") or "postgres",
                port=int(os.environ.get("GSN_DB_PORT") or "5432"),
                dbname=os.environ.get("GSN_DB_NAME") or "gsn",
                user=f"gsn_{role}",
                password=password,
                connect_timeout=10,
                autocommit=True,
            )
        except psycopg.Error as exc:
            # Never let the original exception reach pytest: its traceback shows psycopg's own
            # `Connection.connect` frame, whose `conninfo` argument contains `password=<the real
            # password>` and would be printed in the test log. psycopg's message itself has no
            # password. `from None` also drops the chained exception (and its traceback).
            raise ConnectionError(f"cannot connect as gsn_{role}: {exc}") from None
        opened.append(connection)
        return connection

    yield _connect
    for connection in opened:
        connection.close()
