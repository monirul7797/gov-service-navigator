"""A failed database login must never print the password.

pytest's long traceback shows the arguments of every frame, and psycopg's ``Connection.connect``
frame has a ``conninfo`` argument that contains ``password=<the real password>``. The ``connect``
fixture therefore converts connection errors into a plain ``ConnectionError`` raised ``from None``,
and the ``itest`` job runs with ``--tb=short``. These tests use a random canary instead of a real
password and look for it in every form an error can be rendered.
"""
import os
import traceback
import uuid

import psycopg
import pytest


def _canary():
    # URL-special characters on purpose: the password must survive without escaping.
    return f"canary-{uuid.uuid4().hex}@/:#%?"


def _renderings(excinfo):
    """Everything a test log could show for an exception: str, repr and the pytest/traceback forms."""
    value = excinfo.value
    return [
        str(value),
        repr(value),
        "".join(traceback.format_exception(value)),
        str(excinfo.getrepr(style="long", funcargs=True, showlocals=False)),
        str(excinfo.getrepr(style="short", funcargs=True, showlocals=False)),
        str(excinfo.getrepr(style="native")),
    ]


def test_failed_login_error_contains_no_password(connect, monkeypatch):
    canary = _canary()
    monkeypatch.setenv("GSN_DB_WEB_PASSWORD", canary)  # wrong on purpose: the server rejects it
    with pytest.raises(ConnectionError) as excinfo:
        connect("web")
    assert str(excinfo.value).startswith("cannot connect as gsn_web: ")
    assert excinfo.value.__cause__ is None and excinfo.value.__suppress_context__
    for text in _renderings(excinfo):
        assert canary not in text


def test_control_a_raw_psycopg_failure_does_show_the_password_in_long_tracebacks(monkeypatch):
    # Proves the check above can detect a leak: without the fixture's conversion, the same failed
    # login does put the canary into pytest's long traceback (the reviewed leak). If psycopg or
    # pytest ever stop doing that, this control fails and the leak tests above need a new look.
    canary = _canary()
    with pytest.raises(psycopg.OperationalError) as excinfo:
        psycopg.connect(
            host=os.environ.get("GSN_DB_HOST") or "postgres",
            port=int(os.environ.get("GSN_DB_PORT") or "5432"),
            dbname=os.environ.get("GSN_DB_NAME") or "gsn",
            user="gsn_web",
            password=canary,
            connect_timeout=10,
        )
    assert canary in str(excinfo.getrepr(style="long", funcargs=True, showlocals=False))
