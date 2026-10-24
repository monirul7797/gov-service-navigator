"""``gsn parity``: argument parsing and the mapping of outcomes to exit codes.

``run_parity`` itself is tested in ``test_parity.py``; here it is replaced by a stub (except in the
model-mismatch test, where the real function runs up to its refusal), and the connection by a fake.
"""
import json

import psycopg
import pytest

import gsn.cli as cli
from gsn import parity, store
from gsn.embed import FakeEmbedder

COMMIT = "0123456789abcdef0123456789abcdef01234567"
REAL_MODEL = "BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181"
SECRET = "top-secret-db-password"


class FakeConn:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.closed = False

    def close(self):
        self.closed = True


class World:
    def __init__(self, monkeypatch, *, stub=True):
        for name in ("GSN_DB_HOST", "GSN_DB_PORT", "GSN_DB_NAME", "GSN_ACTOR", "GSN_REPO_GIT", "GSN_EMBEDDER"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("GSN_DB_USER", "gsn_worker")
        monkeypatch.setenv("GSN_DB_PASSWORD", SECRET)
        self.connections = []
        self.calls = []
        self.embedders = []
        self.report = {"ok": True, "gold": [], "vectors": [], "retrieve": [], "problems": [],
                       "summary": {"z": 1, "a": 2}}
        monkeypatch.setattr(cli.psycopg, "connect", self._connect)
        monkeypatch.setattr(cli, "get_embedder", self._get_embedder)
        if stub:
            monkeypatch.setattr(parity, "run_parity", self._run_parity)

    def _connect(self, **kwargs):
        conn = FakeConn(kwargs)
        self.connections.append(conn)
        return conn

    def _get_embedder(self, env):
        embedder = FakeEmbedder()
        self.embedders.append((embedder, dict(env)))
        return embedder

    def _run_parity(self, conn, release_id, source, embedder):
        self.calls.append((conn, release_id, source, embedder))
        if isinstance(self.report, Exception):
            raise self.report
        return self.report

    def run(self, capsys, *argv):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def test_parity_prints_the_report_as_json_and_exits_0_when_it_is_ok(world, capsys):
    code, out, err = world.run(capsys, "parity", "--release", "7")
    assert code == 0 and err == ""
    assert json.loads(out) == world.report


def test_the_printed_report_has_sorted_keys(world, capsys):
    _, out, _ = world.run(capsys, "parity", "--release", "7")
    assert out.strip() == parity.format_report(world.report)
    assert list(json.loads(out)) == sorted(world.report)
    assert list(json.loads(out)["summary"]) == ["a", "z"]


def test_parity_exits_5_when_the_report_is_not_ok_and_still_prints_it(world, capsys):
    world.report = {**world.report, "ok": False, "problems": ["vectors: 1 failing"]}
    code, out, _ = world.run(capsys, "parity", "--release", "7")
    assert code == 5
    assert json.loads(out)["problems"] == ["vectors: 1 failing"]


def test_parity_runs_the_release_from_the_arguments_with_the_environments_embedder(world, capsys, monkeypatch):
    monkeypatch.setenv("GSN_EMBEDDER", "fake")
    world.run(capsys, "parity", "--release", "7")
    conn, release_id, source, embedder = world.calls[0]
    assert release_id == 7 and source is None                 # None: the release's own commit, from the repository
    assert isinstance(embedder, FakeEmbedder) and world.embedders[0][1]["GSN_EMBEDDER"] == "fake"
    assert conn.kwargs["user"] == "gsn_worker" and conn.kwargs["autocommit"] is True


def test_the_connection_is_closed_after_the_command_and_after_a_failure(world, capsys):
    world.run(capsys, "parity", "--release", "7")
    world.report = RuntimeError("boom")
    assert world.run(capsys, "parity", "--release", "7")[0] == 1
    assert [c.closed for c in world.connections] == [True, True]


def test_the_release_argument_is_required_and_must_be_a_number(world, capsys):
    for argv in (["parity"], ["parity", "--release", "seven"]):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        assert excinfo.value.code == 2
    assert world.calls == [] and world.connections == []


def test_a_refusal_is_exit_2_with_the_message_on_stderr_and_nothing_on_stdout(world, capsys):
    world.report = parity.ParityError("release 7 was built with X, but the embedder is Y")
    code, out, err = world.run(capsys, "parity", "--release", "7")
    assert code == 2 and out == "" and "release 7 was built with X" in err


def test_a_model_mismatch_in_the_real_run_is_exit_2(monkeypatch, capsys):
    world = World(monkeypatch, stub=False)                    # the real run_parity, up to its first refusal
    monkeypatch.setattr(store, "release_row", lambda conn, rid: {
        "release_id": rid, "embedding_model": REAL_MODEL, "source_commit": COMMIT})
    searched = []
    monkeypatch.setattr(store, "search", lambda *a, **k: searched.append(a))
    code, out, err = world.run(capsys, "parity", "--release", "7")
    assert code == 2 and out == ""
    assert "fake-sha256-v1" in err and REAL_MODEL in err and SECRET not in err
    assert searched == []


@pytest.mark.parametrize("error", [psycopg.OperationalError("server closed the connection"), RuntimeError("bug")])
def test_a_runtime_error_is_exit_1(world, capsys, error):
    world.report = error
    code, out, err = world.run(capsys, "parity", "--release", "7")
    assert code == 1 and out == "" and str(error) in err and SECRET not in out + err


def test_missing_database_settings_are_exit_2_and_never_print_the_password(world, capsys, monkeypatch):
    monkeypatch.delenv("GSN_DB_USER")
    code, out, err = world.run(capsys, "parity", "--release", "7")
    assert code == 2 and "GSN_DB_USER" in err and SECRET not in out + err
    assert world.calls == [] and world.connections == []


def test_an_embedder_that_cannot_be_made_is_exit_2(world, capsys, monkeypatch):
    def refuse(env):
        raise ValueError("GSN_EMBEDDER must be 'fake' or 'bge-m3', got 'x'")

    monkeypatch.setattr(cli, "get_embedder", refuse)
    code, out, err = world.run(capsys, "parity", "--release", "7")
    assert code == 2 and "GSN_EMBEDDER must be" in err and world.calls == []
    assert [c.closed for c in world.connections] == [True]
