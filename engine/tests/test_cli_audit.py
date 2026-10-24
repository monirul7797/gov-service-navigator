"""``gsn audit verify`` and ``gsn.audit``: the exit codes, the one summary line and the SQL call.

The database is replaced by fakes here; the real ``audit_verify()`` runs in ``tests/integration``.
"""
import psycopg
import pytest

import gsn.cli as cli
from gsn import audit
from gsn.audit import AuditStatus

SECRET = "top-secret-db-password"


class FakeConn:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.closed = False

    def close(self):
        self.closed = True


class World:
    def __init__(self, monkeypatch):
        for name in ("GSN_DB_HOST", "GSN_DB_PORT", "GSN_DB_NAME"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("GSN_DB_USER", "gsn_worker")
        monkeypatch.setenv("GSN_DB_PASSWORD", SECRET)
        self.connections: list[FakeConn] = []
        self.verified: list = []
        self.status: AuditStatus | Exception = AuditStatus(True, 12, None)
        self.connect_error: Exception | None = None
        monkeypatch.setattr(cli.psycopg, "connect", self._connect)
        monkeypatch.setattr(cli.audit, "verify", self._verify)

    def _connect(self, **kwargs):
        if self.connect_error:
            raise self.connect_error
        conn = FakeConn(kwargs)
        self.connections.append(conn)
        return conn

    def _verify(self, conn):
        self.verified.append(conn)
        if isinstance(self.status, Exception):
            raise self.status
        return self.status

    def run(self, capsys, *argv):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def test_audit_verify_exit_codes(world, capsys):
    world.status = AuditStatus(True, 12, None)
    code, out, err = world.run(capsys, "audit", "verify")
    assert (code, out, err) == (0, "audit chain ok: 12 rows checked\n", "")

    world.status = AuditStatus(False, 4, 5)
    code, out, err = world.run(capsys, "audit", "verify")
    assert (code, out, err) == (1, "audit chain BROKEN at chain_seq 5 (4 rows checked)\n", "")


def test_an_empty_chain_is_ok(world, capsys):
    world.status = AuditStatus(True, 0, None)
    assert world.run(capsys, "audit", "verify")[:2] == (0, "audit chain ok: 0 rows checked\n")


def test_verify_connects_with_parts_autocommit_and_closes_the_connection(world, capsys):
    world.run(capsys, "audit", "verify")
    (conn,) = world.connections
    assert conn.kwargs["user"] == "gsn_worker" and conn.kwargs["password"] == SECRET
    assert conn.kwargs["host"] == "postgres" and conn.kwargs["dbname"] == "gsn"
    assert conn.kwargs["autocommit"] is True and conn.kwargs["connect_timeout"] == cli.CONNECT_TIMEOUT_SECONDS
    assert world.verified == [conn] and conn.closed


def test_missing_configuration_is_exit_2_and_names_no_secret(world, capsys, monkeypatch):
    monkeypatch.delenv("GSN_DB_USER")
    code, out, err = world.run(capsys, "audit", "verify")
    assert code == 2 and out == ""
    assert "GSN_DB_USER" in err and SECRET not in err
    assert world.connections == []  # nothing was opened


def test_database_errors_are_exit_1_with_a_one_line_message(world, capsys):
    world.connect_error = psycopg.OperationalError("connection refused")
    code, out, err = world.run(capsys, "audit", "verify")
    assert code == 1 and out == ""
    assert err.strip() == "error: database: connection refused"

    world.connect_error = None
    world.status = psycopg.errors.InsufficientPrivilege("permission denied for function audit_verify")
    code, out, err = world.run(capsys, "audit", "verify")
    assert code == 1 and out == ""
    assert "permission denied for function audit_verify" in err
    assert world.connections[-1].closed  # closed even though the call failed


def test_an_unexpected_error_is_exit_1_and_closes_the_connection(world, capsys):
    world.status = RuntimeError("boom")
    code, out, err = world.run(capsys, "audit", "verify")
    assert code == 1 and out == ""
    assert "unexpected RuntimeError: boom" in err
    assert world.connections[-1].closed


def test_audit_needs_a_subcommand(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["audit"])
    assert stopped.value.code == 2


# --- gsn.audit ---------------------------------------------------------------------------------


class RowConn:
    """Answers ``execute(...).fetchone()`` with one prepared row."""

    def __init__(self, row):
        self.row, self.statements = row, []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        return self

    def fetchone(self):
        return self.row


def test_verify_reads_the_three_columns_of_audit_verify():
    conn = RowConn((True, 7, None))
    assert audit.verify(conn) == AuditStatus(ok=True, rows_checked=7, first_bad_seq=None)
    assert len(conn.statements) == 1
    assert "public.audit_verify()" in conn.statements[0]  # schema-qualified, like the other SQL functions


def test_verify_reports_a_broken_chain():
    assert audit.verify(RowConn((False, 2, 3))) == AuditStatus(False, 2, 3)


def test_verify_treats_an_unexpected_empty_answer_as_an_error():
    with pytest.raises(RuntimeError, match="audit_verify"):
        audit.verify(RowConn(None))


def test_the_status_is_immutable():
    with pytest.raises(AttributeError):
        AuditStatus(True, 1, None).ok = False  # type: ignore[misc]


def test_a_broken_status_without_a_position_still_reads_as_broken():
    assert audit.format_status(AuditStatus(False, 3, None)) == "audit chain BROKEN (3 rows checked)"
