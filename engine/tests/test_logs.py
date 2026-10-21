import json
import logging
from datetime import datetime, timedelta

import pytest

from gsn.logs import configure


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _last_record(capsys):
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_json_line_per_record(capsys):
    configure("worker")
    logging.getLogger("x").info("hello", extra={"beat": 1})
    rec = _last_record(capsys)
    assert rec["service"] == "worker" and rec["msg"] == "hello" and rec["level"] == "INFO" and rec["beat"] == 1


def test_ts_is_iso8601_utc(capsys):
    configure("engine")
    logging.getLogger("x").warning("w")
    rec = _last_record(capsys)
    parsed = datetime.fromisoformat(rec["ts"])
    assert parsed.utcoffset() == timedelta(0)
    assert rec["level"] == "WARNING"


def test_configure_twice_does_not_duplicate_lines(capsys):
    configure("engine")
    configure("engine")
    logging.getLogger("x").info("once")
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1


def test_message_args_are_formatted_and_exception_included(capsys):
    configure("engine")
    log = logging.getLogger("x")
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("failed %s", "step")
    rec = _last_record(capsys)
    assert rec["msg"] == "failed step"
    assert "RuntimeError: boom" in rec["exc"]


def test_non_ascii_message_is_kept_as_text(capsys):
    configure("engine")
    logging.getLogger("x").info("সেবা")
    assert _last_record(capsys)["msg"] == "সেবা"


def test_unserialisable_extra_does_not_crash(capsys):
    configure("engine")
    logging.getLogger("x").info("m", extra={"obj": object()})
    assert _last_record(capsys)["msg"] == "m"


def test_uvicorn_color_message_extra_is_not_logged(capsys):
    # uvicorn passes an ANSI-coloured copy of its message as extra={"color_message": ...}; it is
    # terminal decoration and must not leak into the JSON line.
    configure("engine")
    logging.getLogger("uvicorn.error").info(
        "Started server process [%d]", 7, extra={"color_message": "Started server process [\x1b[36m%d\x1b[0m]"}
    )
    rec = _last_record(capsys)
    assert rec["msg"] == "Started server process [7]"
    assert "color_message" not in rec
    assert rec["service"] == "engine"
