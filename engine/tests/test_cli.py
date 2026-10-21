import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request

import psycopg
import pytest

import gsn.cli as cli


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def _env(monkeypatch, **values):
    for name in ("GSN_DB_HOST", "GSN_DB_PORT", "GSN_DB_NAME", "GSN_DB_USER", "GSN_DB_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_worker_subcommand_connects_with_parts_and_autocommit(monkeypatch):
    _env(monkeypatch, GSN_DB_USER="gsn_worker", GSN_DB_PASSWORD="p@ss/w:rd#%?")
    seen = {}

    def fake_connect(**kwargs):
        seen.update(kwargs)
        return object()

    def fake_run_worker(connect):
        connect()

    monkeypatch.setattr(cli.psycopg, "connect", fake_connect)
    monkeypatch.setattr(cli, "run_worker", fake_run_worker)
    monkeypatch.setattr(cli.signal, "signal", lambda *_a: None)

    assert cli.main(["worker"]) == 0
    assert seen["host"] == "postgres" and seen["port"] == 5432 and seen["dbname"] == "gsn"
    assert seen["user"] == "gsn_worker" and seen["password"] == "p@ss/w:rd#%?"
    assert seen["autocommit"] is True and seen["connect_timeout"] == cli.CONNECT_TIMEOUT_SECONDS


def test_worker_with_missing_config_exits_2_and_never_logs_secrets(monkeypatch, capsys):
    _env(monkeypatch, GSN_DB_PASSWORD="top-secret-value")
    called = []
    monkeypatch.setattr(cli, "run_worker", lambda connect: called.append(connect))

    assert cli.main(["worker"]) == 2
    assert called == []
    out = capsys.readouterr().out
    assert "top-secret-value" not in out
    assert "GSN_DB_USER" in json.loads(out.strip().splitlines()[-1])["error"]


def test_subcommand_is_required():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_crash_after_ready_is_logged_as_one_json_line_and_exits_1(monkeypatch, capsys):
    _env(monkeypatch, GSN_DB_USER="gsn_worker", GSN_DB_PASSWORD="top-secret-value")

    def crashing_run_worker(connect):
        raise psycopg.OperationalError("connection lost")

    monkeypatch.setattr(cli, "run_worker", crashing_run_worker)
    monkeypatch.setattr(cli.signal, "signal", lambda *_a: None)

    assert cli.main(["worker"]) == 1
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["level"] == "ERROR" and rec["service"] == "worker" and rec["msg"] == "worker stopped"
    assert "OperationalError: connection lost" in rec["exc"]
    assert "top-secret-value" not in out


def test_sigterm_exit_is_not_reported_as_a_crash(monkeypatch, capsys):
    _env(monkeypatch, GSN_DB_USER="gsn_worker", GSN_DB_PASSWORD="p")

    def stopped_run_worker(connect):
        raise SystemExit(0)

    monkeypatch.setattr(cli, "run_worker", stopped_run_worker)
    monkeypatch.setattr(cli.signal, "signal", lambda *_a: None)

    with pytest.raises(SystemExit) as exc:
        cli.main(["worker"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == ""


def test_engine_subcommand_logs_json_and_runs_uvicorn_without_its_own_log_config(monkeypatch, capsys):
    import uvicorn

    seen = {}

    def fake_run(app, **kwargs):
        seen["app"] = app
        seen.update(kwargs)
        # what uvicorn itself logs while serving must come out as JSON through our root handler
        logging.getLogger("uvicorn.error").info("Uvicorn running on http://%s:%d", kwargs["host"], kwargs["port"])

    monkeypatch.setattr(uvicorn, "run", fake_run)

    assert cli.main(["engine", "--host", "0.0.0.0", "--port", "8123"]) == 0
    assert seen["host"] == "0.0.0.0" and seen["port"] == 8123
    assert seen["log_config"] is None  # uvicorn's default config would print plain-text lines
    assert seen["access_log"] is False  # request lines carry query strings; /healthz would be noise
    assert seen["app"].title == "GSN engine"
    rec = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rec["service"] == "engine" and rec["msg"] == "Uvicorn running on http://0.0.0.0:8123"


def test_engine_subcommand_defaults_to_loopback(monkeypatch):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: seen.update(kwargs))
    assert cli.main(["engine"]) == 0
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8000


def test_engine_process_prints_only_json_lines_and_serves_healthz():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {**os.environ, "GSN_VERSION": "itest", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "gsn.cli", "engine", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    body = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and body is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                    body = json.loads(response.read())
            except OSError:
                time.sleep(0.2)
    finally:
        proc.terminate()
        output, _ = proc.communicate(timeout=30)
    assert body == {"status": "ok", "service": "engine", "version": "itest"}
    lines = [ln for ln in output.decode("utf-8").splitlines() if ln.strip()]
    records = [json.loads(ln) for ln in lines]  # raises on any plain-text line
    assert records and all(rec["service"] == "engine" for rec in records)
    assert any("Uvicorn running on" in rec["msg"] for rec in records)
    assert "\x1b" not in output.decode("utf-8")
    assert not any("GET /healthz" in rec["msg"] for rec in records)
