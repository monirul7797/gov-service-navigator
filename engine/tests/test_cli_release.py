"""``gsn release ...`` and ``gsn eval``: argument parsing and the mapping of outcomes to exit codes.

The build, the store and the repository are replaced by fakes here; the real thing runs in
``tests/integration`` (database) and in the compose smoke step (container and repository).
"""
import json
import os
import shutil
import subprocess
from datetime import date, datetime, timezone

import psycopg
import pytest

import gsn.cli as cli
from gsn import store
from gsn.embed import FakeEmbedder
from gsn.evals import GateResult
from gsn.gitsource import GitSource, GitSourceError
from gsn.release import commands
from gsn.release.build import BuildResult, dhaka_today

COMMIT = "0123456789abcdef0123456789abcdef01234567"
REAL_MODEL = "BAAI/bge-m3@5617a9f61b028005a4858fdac845db406aefb181"
SECRET = "top-secret-db-password"

GATE = GateResult(True, [], [], {"alpha": {"PASS": 4, "PASS (warn)": 0, "OBSERVE": 0, "FAIL": 0}})


def _result(status="validated", release_id=3, problems=None, gate=GATE):
    return BuildResult(
        release_id=None if status == "integrity_failed" else release_id, status=status,
        problems=problems or [], gate=None if status == "integrity_failed" else gate, embedded=6, reused_versions=0,
        records=6, services=["alpha"], copied_vectors=0, overridden=False, commit=COMMIT,
        embedding_model="fake-sha256-v1",
    )


class FakeConn:
    def __init__(self, kwargs):
        self.kwargs = kwargs
        self.closed = False

    def close(self):
        self.closed = True


class FakeRepo:
    """Stands in for ``GitSource``; class attributes are set per test."""

    secrets: list[str] = []
    error: str | None = None
    created: list = []

    def __init__(self, git_dir, ref="HEAD"):
        if self.error:
            raise GitSourceError(self.error)
        self.git_dir, self.ref, self.commit = git_dir, ref, COMMIT
        FakeRepo.created.append(self)

    def secret_paths_in_history(self):
        return list(self.secrets)


class World:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        for name in ("GSN_DB_HOST", "GSN_DB_PORT", "GSN_DB_NAME", "GSN_ACTOR", "GSN_REPO_GIT", "GSN_EMBEDDER"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("GSN_DB_USER", "gsn_worker")
        monkeypatch.setenv("GSN_DB_PASSWORD", SECRET)
        self.connections: list[FakeConn] = []
        self.builds: list[tuple] = []
        self.embedders: list = []
        self.result = _result()
        FakeRepo.secrets, FakeRepo.error, FakeRepo.created = [], None, []
        monkeypatch.setattr(cli.psycopg, "connect", self._connect)
        monkeypatch.setattr(commands, "GitSource", FakeRepo)
        monkeypatch.setattr(cli, "get_embedder", self._get_embedder)
        monkeypatch.setattr(cli, "build_release", self._build)

    def _connect(self, **kwargs):
        conn = FakeConn(kwargs)
        self.connections.append(conn)
        return conn

    def _get_embedder(self, env):
        embedder = FakeEmbedder()
        self.embedders.append((embedder, dict(env)))
        return embedder

    def _build(self, conn, source, embedder, **kwargs):
        self.builds.append((conn, source, embedder, kwargs))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def run(self, capsys, *argv):
        code = cli.main(list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err


@pytest.fixture
def world(monkeypatch):
    return World(monkeypatch)


# --- build: exit codes --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status, code, first_line",
    [
        ("validated", 0, "release_id=3 status=validated"),
        ("rejected", 3, "release_id=3 status=rejected"),
        ("integrity_failed", 4, "release_id=- status=integrity_failed"),
    ],
)
def test_build_exit_codes_validated_rejected_integrity(world, capsys, status, code, first_line):
    world.result = _result(status, problems=["alpha record alpha-2: text is missing or empty"])
    exit_code, out, err = world.run(capsys, "release", "build")
    assert exit_code == code
    assert out.splitlines()[0] == first_line


def test_build_prints_the_integrity_problems_after_the_first_line(world, capsys):
    world.result = _result("integrity_failed", problems=["problem one", "problem two"])
    _, out, _ = world.run(capsys, "release", "build")
    assert out.splitlines()[0] == "release_id=- status=integrity_failed"
    assert "problem one" in out and "problem two" in out


def test_build_passes_its_arguments_and_the_environment_through(world, capsys, monkeypatch):
    monkeypatch.setenv("GSN_ACTOR", "alice")
    monkeypatch.setenv("GSN_REPO_GIT", "/mounted.git")
    code, _, _ = world.run(capsys, "release", "build", "--commit", "main", "--note", "first", "--override", "why")
    assert code == 0
    conn, source, embedder, kwargs = world.builds[0]
    assert (source.git_dir, source.ref) == ("/mounted.git", "main")
    assert isinstance(embedder, FakeEmbedder)
    assert kwargs["actor"] == "alice" and kwargs["note"] == "first" and kwargs["override"] == "why"
    assert isinstance(kwargs["today"], date) and abs((kwargs["today"] - dhaka_today()).days) <= 1
    assert conn.kwargs["user"] == "gsn_worker" and conn.kwargs["autocommit"] is True
    assert conn.kwargs["password"] == SECRET and conn.kwargs["host"] == "postgres"


def test_build_defaults(world, capsys):
    world.run(capsys, "release", "build")
    _, source, _, kwargs = world.builds[0]
    assert (source.git_dir, source.ref) == ("/repo.git", "HEAD")
    assert kwargs["actor"] == "owner" and kwargs["note"] is None and kwargs["override"] is None


def test_build_reads_the_embedder_from_the_environment(world, capsys, monkeypatch):
    monkeypatch.setenv("GSN_EMBEDDER", "fake")
    world.run(capsys, "release", "build")
    assert world.embedders[0][1]["GSN_EMBEDDER"] == "fake"


def test_the_connection_is_closed_after_the_command(world, capsys):
    world.run(capsys, "release", "build")
    assert [c.closed for c in world.connections] == [True]


def test_the_connection_is_closed_when_the_build_fails(world, capsys):
    world.result = RuntimeError("boom")
    assert world.run(capsys, "release", "build")[0] == 1
    assert [c.closed for c in world.connections] == [True]


# --- build: refusals (exit 2) -------------------------------------------------------------------


def test_build_refuses_when_history_has_secret_paths(world, capsys):
    FakeRepo.secrets = ["setenv_deepseek.bat", "tools/.env"]
    code, out, err = world.run(capsys, "release", "build")
    assert code == 2 and out == ""
    assert "setenv_deepseek.bat" in err and "tools/.env" in err
    # nothing else started: no model load, no database connection, no build
    assert world.embedders == [] and world.connections == [] and world.builds == []


def test_bad_ref_is_exit_2_with_message(world, capsys):
    FakeRepo.error = "cannot resolve ref 'nope' to a commit in /repo.git"
    code, out, err = world.run(capsys, "release", "build", "--commit", "nope")
    assert code == 2 and out == ""
    assert "cannot resolve ref 'nope'" in err
    assert world.embedders == [] and world.connections == [] and world.builds == []


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    argv = ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
            "-c", "commit.gpgsign=false", *args]
    subprocess.run(argv, check=True, capture_output=True, env=env)


@pytest.fixture
def real_repo(tmp_path, monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git executable not found on PATH")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "one")
    monkeypatch.setenv("GSN_REPO_GIT", str(repo / ".git"))
    return repo


def test_an_unknown_ref_in_a_real_repository_is_exit_2(world, capsys, real_repo, monkeypatch):
    monkeypatch.setattr(commands, "GitSource", GitSource)
    code, _, err = world.run(capsys, "release", "build", "--commit", "no-such-branch")
    assert code == 2 and "no-such-branch" in err
    assert world.builds == [] and world.connections == []


def test_secret_paths_in_the_history_of_a_real_repository_are_exit_2(world, capsys, real_repo, monkeypatch):
    monkeypatch.setattr(commands, "GitSource", GitSource)
    (real_repo / "setenv_x.bat").write_text("set KEY=x", encoding="utf-8")
    _git(real_repo, "add", "-f", "setenv_x.bat")
    _git(real_repo, "commit", "-q", "-m", "oops")
    _git(real_repo, "rm", "-q", "setenv_x.bat")
    _git(real_repo, "commit", "-q", "-m", "remove it again")
    code, _, err = world.run(capsys, "release", "build")
    assert code == 2 and "setenv_x.bat" in err
    assert world.builds == [] and world.connections == []


def test_a_clean_real_repository_gets_as_far_as_the_build(world, capsys, real_repo, monkeypatch):
    monkeypatch.setattr(commands, "GitSource", GitSource)
    code, _, _ = world.run(capsys, "release", "build", "--commit", "main")
    assert code == 0 and len(world.builds[0][1].commit) == 40


def test_missing_database_settings_are_exit_2_and_never_print_the_password(world, capsys, monkeypatch):
    monkeypatch.delenv("GSN_DB_USER")
    code, out, err = world.run(capsys, "release", "build")
    assert code == 2 and "GSN_DB_USER" in err and SECRET not in out + err
    assert world.builds == [] and world.connections == []


@pytest.mark.parametrize("error", [NotImplementedError("real embedder arrives in Task C1"), ValueError("bad name")])
def test_an_embedder_that_cannot_be_made_is_exit_2(world, capsys, monkeypatch, error):
    def refuse(env):
        raise error

    monkeypatch.setattr(cli, "get_embedder", refuse)
    code, out, err = world.run(capsys, "release", "build")
    assert code == 2 and str(error) in err
    assert world.builds == []


def test_a_server_refusal_during_the_build_is_exit_2_with_the_servers_message(world, capsys):
    world.result = store.StoreError("record rec-1 is stored under service nid, not bdris: a record never changes service")
    code, _, err = world.run(capsys, "release", "build")
    assert code == 2 and "a record never changes service" in err


# --- build: runtime errors (exit 1) -------------------------------------------------------------


@pytest.mark.parametrize("error", [psycopg.OperationalError("server closed the connection"), RuntimeError("bug")])
def test_a_runtime_error_is_exit_1(world, capsys, error):
    world.result = error
    code, out, err = world.run(capsys, "release", "build")
    assert code == 1 and str(error) in err and SECRET not in out + err


def test_a_database_that_cannot_be_reached_is_exit_1(world, capsys, monkeypatch):
    def down(**kwargs):
        raise psycopg.OperationalError("connection failed: server not reachable")

    monkeypatch.setattr(cli.psycopg, "connect", down)
    code, _, err = world.run(capsys, "release", "build")
    assert code == 1 and "server not reachable" in err and SECRET not in err


# --- eval ---------------------------------------------------------------------------------------


def _store_release(model):
    return {"release_id": 7, "embedding_model": model, "source_commit": COMMIT, "status": "validated",
            "thresholds": {"known_failures": {}}}


def test_eval_refuses_model_mismatch(world, capsys, monkeypatch):
    monkeypatch.setattr(store, "release_row", lambda conn, rid: _store_release(REAL_MODEL))
    searched = []
    monkeypatch.setattr(store, "search", lambda *a, **k: searched.append(a))
    code, out, err = world.run(capsys, "eval", "--release", "7")
    assert code == 2 and out == ""
    assert "fake-sha256-v1" in err and REAL_MODEL in err
    assert searched == [] and FakeRepo.created == []


def _stub_eval(monkeypatch, passed=True):
    result = {"release_id": 7, "source_commit": COMMIT, "embedding_model": "fake-sha256-v1", "passed": passed,
              "counts": {"alpha": {"PASS": 3, "PASS (warn)": 0, "OBSERVE": 0, "FAIL": 0 if passed else 1}},
              "unexpected_failures": [] if passed else ["gq_x"], "known_failures_seen": [], "verdicts": []}
    seen = []
    monkeypatch.setattr(commands, "eval_release", lambda conn, rid, embedder, **k: seen.append((rid, embedder)) or result)
    return seen


def test_eval_json_prints_the_result_and_exits_0_when_the_gate_passes(world, capsys, monkeypatch):
    seen = _stub_eval(monkeypatch)
    code, out, _ = world.run(capsys, "eval", "--release", "7", "--json")
    assert code == 0 and json.loads(out)["release_id"] == 7 and seen[0][0] == 7


def test_eval_exits_3_when_the_gate_would_reject(world, capsys, monkeypatch):
    _stub_eval(monkeypatch, passed=False)
    code, out, _ = world.run(capsys, "eval", "--release", "7")
    assert code == 3 and "gq_x" in out


def test_eval_without_json_prints_a_summary(world, capsys, monkeypatch):
    _stub_eval(monkeypatch)
    code, out, _ = world.run(capsys, "eval", "--release", "7")
    assert code == 0 and out.startswith("release 7") and "alpha:" in out


# --- publish / rollback / list / show -----------------------------------------------------------


def test_publish_requires_allow_fake_for_fake_models(world, capsys, monkeypatch):
    published = []
    monkeypatch.setattr(store, "release_row", lambda conn, rid: _store_release("fake-sha256-v1"))
    monkeypatch.setattr(store, "publish", lambda conn, rid, actor: published.append((rid, actor)))

    code, out, err = world.run(capsys, "release", "publish", "7")
    assert code == 2 and "--allow-fake" in err and published == []

    code, out, err = world.run(capsys, "release", "publish", "7", "--allow-fake")
    assert code == 0 and published == [(7, "owner")] and "release 7" in out


def test_publish_of_a_real_model_release_needs_no_flag_and_uses_the_actor(world, capsys, monkeypatch):
    monkeypatch.setenv("GSN_ACTOR", "alice")
    published = []
    monkeypatch.setattr(store, "release_row", lambda conn, rid: _store_release(REAL_MODEL))
    monkeypatch.setattr(store, "publish", lambda conn, rid, actor: published.append((rid, actor)))
    assert world.run(capsys, "release", "publish", "7")[0] == 0
    assert published == [(7, "alice")]


def test_publish_prints_the_servers_refusal_and_exits_2(world, capsys, monkeypatch):
    monkeypatch.setattr(store, "release_row", lambda conn, rid: _store_release(REAL_MODEL))

    def refuse(conn, rid, actor):
        raise store.StoreError(f"release {rid} is not validated")

    monkeypatch.setattr(store, "publish", refuse)
    code, out, err = world.run(capsys, "release", "publish", "7")
    assert code == 2 and "release 7 is not validated" in err


def test_publish_of_a_missing_release_is_exit_2(world, capsys, monkeypatch):
    monkeypatch.setattr(store, "release_row", lambda conn, rid: None)
    code, _, err = world.run(capsys, "release", "publish", "99")
    assert code == 2 and "release 99 does not exist" in err


def test_rollback_prints_the_release_that_is_active_again(world, capsys, monkeypatch):
    calls = []
    monkeypatch.setattr(store, "rollback", lambda conn, actor: calls.append(actor) or 4)
    code, out, _ = world.run(capsys, "release", "rollback")
    assert code == 0 and calls == ["owner"] and "release 4" in out


def test_rollback_with_nothing_to_undo_is_exit_2(world, capsys, monkeypatch):
    def refuse(conn, actor):
        raise store.StoreError("nothing to roll back")

    monkeypatch.setattr(store, "rollback", refuse)
    code, _, err = world.run(capsys, "release", "rollback")
    assert code == 2 and "nothing to roll back" in err


def test_list_prints_one_line_per_release(world, capsys, monkeypatch):
    rows = [{"release_id": 1, "status": "validated", "source_commit": COMMIT, "embedding_model": "fake-sha256-v1",
             "created_at": datetime(2026, 10, 23, 10, 15, tzinfo=timezone.utc), "is_active": True}]
    monkeypatch.setattr(store, "list_releases", lambda conn: rows)
    code, out, _ = world.run(capsys, "release", "list")
    assert code == 0 and "validated" in out and COMMIT[:12] in out and "yes" in out


def test_show_always_prints_json(world, capsys, monkeypatch):
    shown = {"release_id": 7, "created_at": datetime(2026, 10, 23, tzinfo=timezone.utc),
             "diff_vs_active": {"added": [], "removed": [], "changed": []}}
    seen = []
    monkeypatch.setattr(commands, "show", lambda conn, rid: seen.append(rid) or shown)
    code, out, _ = world.run(capsys, "release", "show", "7")
    assert code == 0 and seen == [7]
    assert json.loads(out)["diff_vs_active"] == {"added": [], "removed": [], "changed": []}


def test_show_of_a_missing_release_is_exit_2(world, capsys, monkeypatch):
    monkeypatch.setattr(store, "release_row", lambda conn, rid: None)
    code, _, err = world.run(capsys, "release", "show", "99")
    assert code == 2 and "release 99 does not exist" in err


# --- usage errors -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["release"],
        ["release", "publish"],
        ["release", "publish", "seven"],
        ["release", "show"],
        ["release", "build", "--override", ""],
        ["release", "build", "--override", "   "],
        ["eval"],
        ["eval", "--release", "x"],
        ["release", "nonsense"],
    ],
)
def test_usage_errors_exit_2_before_anything_runs(world, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 2
    capsys.readouterr()
    assert world.connections == [] and world.builds == []
