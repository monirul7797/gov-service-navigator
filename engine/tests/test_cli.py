import json
import logging

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
