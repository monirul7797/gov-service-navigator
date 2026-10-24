"""``gsn models fetch``, and how a missing model or missing ML packages end a command (exit code 2).

The download itself is replaced by a stub (``test_embed.py`` covers ``fetch_model``); the real thing runs in
the release container (docs/DEVELOPMENT.md "Model download"). ``models fetch`` needs no database.
"""
import pytest

import gsn.cli as cli
from gsn import parity
from gsn.embed import MlPackagesMissingError, ModelMissingError

REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
SNAPSHOT = f"/models/hub/models--BAAI--bge-m3/snapshots/{REVISION}"
FETCH_COMMAND = "docker compose --profile tools run --rm -e HF_HUB_OFFLINE=0 release models fetch"
SECRET = "top-secret-db-password"


class FakeConn:
    def close(self):
        pass


class World:
    def __init__(self, monkeypatch):
        for name in ("GSN_DB_HOST", "GSN_DB_PORT", "GSN_DB_NAME", "GSN_DB_USER", "GSN_DB_PASSWORD", "HF_HUB_OFFLINE"):
            monkeypatch.delenv(name, raising=False)
        self.fetches = []
        self.connections = []
        self.fetch_result = SNAPSHOT  # an Exception instance is raised instead
        monkeypatch.setattr(cli, "fetch_model", self._fetch_model)
        monkeypatch.setattr(cli, "snapshot_bytes", lambda path: 2271145830 if path == SNAPSHOT else -1)
        monkeypatch.setattr(cli.psycopg, "connect", self._connect)

    def _fetch_model(self, *args, **kwargs):
        self.fetches.append((args, kwargs))
        if isinstance(self.fetch_result, Exception):
            raise self.fetch_result
        return self.fetch_result

    def _connect(self, **kwargs):
        self.connections.append(kwargs)
        return FakeConn()

    def run(self, capsys, *argv):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


def test_fetch_is_refused_while_the_hub_is_offline_and_says_which_command_to_run(world, capsys, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    code, out, err = world.run(capsys, "models", "fetch")
    assert code == 2 and out == ""
    assert "HF_HUB_OFFLINE" in err and FETCH_COMMAND in err
    assert world.fetches == []


@pytest.mark.parametrize("value", ["1", "ON", "yes", "True"])
def test_every_value_the_hub_reads_as_offline_is_refused(world, capsys, monkeypatch, value):
    monkeypatch.setenv("HF_HUB_OFFLINE", value)
    assert world.run(capsys, "models", "fetch")[0] == 2
    assert world.fetches == []


@pytest.mark.parametrize("value", ["0", "", None])
def test_fetch_runs_when_the_hub_is_online_and_needs_no_database(world, capsys, monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("HF_HUB_OFFLINE", value)
    code, out, err = world.run(capsys, "models", "fetch")
    assert code == 0 and err == ""
    assert world.fetches == [((), {})]
    assert world.connections == []  # no GSN_DB_* variable is set, and none is needed


def test_fetch_prints_the_snapshot_relative_to_the_cache_root_and_the_total_bytes(world, capsys):
    code, out, _ = world.run(capsys, "models", "fetch")
    assert code == 0
    assert f"models--BAAI--bge-m3/snapshots/{REVISION}" in out
    assert "/models/hub" not in out  # relative to the cache root, not the absolute path
    assert "2271145830" in out
    assert f"BAAI/bge-m3@{REVISION}" in out


def test_a_download_that_fails_is_exit_1(world, capsys):
    world.fetch_result = OSError("network is unreachable")
    code, out, err = world.run(capsys, "models", "fetch")
    assert code == 1 and "network is unreachable" in err


def test_fetch_without_the_ml_packages_is_exit_2(world, capsys):
    world.fetch_result = MlPackagesMissingError("needs huggingface_hub: run it in the release container")
    code, out, err = world.run(capsys, "models", "fetch")
    assert code == 2 and "release container" in err and out == ""


def test_models_needs_a_subcommand(world):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["models"])
    assert excinfo.value.code == 2


# --- a missing model or missing packages while a command runs --------------------------------------------


@pytest.fixture
def parity_world(world, monkeypatch):
    monkeypatch.setenv("GSN_DB_USER", "gsn_worker")
    monkeypatch.setenv("GSN_DB_PASSWORD", SECRET)
    world.error = None

    def run_parity(conn, release_id, source, embedder):
        raise world.error

    monkeypatch.setattr(parity, "run_parity", run_parity)
    return world


def test_a_missing_model_is_exit_2_with_one_line_that_names_the_fetch_command(parity_world, capsys):
    parity_world.error = ModelMissingError("run: gsn models fetch")
    code, out, err = parity_world.run(capsys, "parity", "--release", "7")
    assert code == 2 and out == ""
    lines = err.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("error: ")
    assert FETCH_COMMAND in lines[0] and SECRET not in err


def test_missing_ml_packages_are_exit_2_with_the_explanation(parity_world, capsys):
    parity_world.error = MlPackagesMissingError("the real embedder needs sentence-transformers: use the release container")
    code, out, err = parity_world.run(capsys, "parity", "--release", "7")
    assert code == 2 and out == "" and "release container" in err
    assert "Traceback" not in err
