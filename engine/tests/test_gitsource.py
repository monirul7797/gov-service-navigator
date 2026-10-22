import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gsn import gitsource
from gsn.gitsource import ContentSource, DictSource, GitSource, GitSourceError

_FULL_SHA = re.compile(r"[0-9a-f]{40}")


def _clean_env() -> dict[str, str]:
    """Environment for the tests' own git calls: no system/global config, no inherited GIT_* state."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    return env


def _git(repo: Path, *args: str) -> str:
    argv = [
        "git",
        "-C",
        str(repo),
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "core.autocrlf=false",
        *args,
    ]
    done = subprocess.run(argv, check=True, capture_output=True, env=_clean_env())
    return done.stdout.decode("utf-8").strip()


def _commit(repo: Path, files: dict[str, bytes], message: str = "commit") -> str:
    for rel, data in files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        _git(repo, "add", "--", rel)
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A throw-away repository on branch ``main`` with one commit (``data/a.txt``)."""
    if shutil.which("git") is None:
        pytest.skip("git executable not found on PATH; GitSource tests need the host git")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, {"data/a.txt": b"committed\n"}, "first")
    return repo


def _git_dir(repo: Path) -> str:
    return str(repo / ".git")


def _subcommand(argv: list[str]) -> str:
    """The git subcommand of an argv that starts ``git --git-dir=... -c k=v ... <subcommand> ...``."""
    i = 1
    while argv[i].startswith("--git-dir=") or argv[i] == "-c":
        i += 2 if argv[i] == "-c" else 1
    return argv[i]


def test_resolves_head_branch_and_short_sha_to_full_sha(tmp_repo):
    sha = _git(tmp_repo, "rev-parse", "HEAD")
    assert _FULL_SHA.fullmatch(sha)
    assert GitSource(_git_dir(tmp_repo)).commit == sha
    assert GitSource(_git_dir(tmp_repo), "HEAD").commit == sha
    assert GitSource(_git_dir(tmp_repo), "main").commit == sha
    assert GitSource(_git_dir(tmp_repo), sha[:8]).commit == sha


def test_unknown_ref_raises_gitsourceerror_naming_the_ref(tmp_repo, monkeypatch):
    monkeypatch.setenv("GSN_DB_WORKER_PASSWORD", "s3cret-value-123")
    with pytest.raises(GitSourceError) as exc:
        GitSource(_git_dir(tmp_repo), "no-such-branch")
    assert "no-such-branch" in str(exc.value)
    assert "s3cret-value-123" not in str(exc.value)


@pytest.mark.parametrize("ref", ["--all", "-x", ""])
def test_option_like_or_empty_ref_is_rejected(tmp_repo, ref):
    with pytest.raises(GitSourceError):
        GitSource(_git_dir(tmp_repo), ref)


def test_reads_committed_bytes_not_working_tree(tmp_repo):
    committed = "জন্ম\r\nline 2\r\n".encode("utf-8") + b"\x00\xff"
    sha = _commit(tmp_repo, {"data/b.bin": committed}, "add b")
    source = GitSource(_git_dir(tmp_repo))
    assert source.commit == sha
    # Edit the working tree after the commit; also add an untracked file.
    (tmp_repo / "data" / "a.txt").write_bytes(b"edited after commit\n")
    (tmp_repo / "data" / "b.bin").write_bytes(b"edited")
    (tmp_repo / "data" / "untracked.txt").write_bytes(b"x")
    assert source.read("data/a.txt") == b"committed\n"
    assert source.read("data/b.bin") == committed  # raw bytes: no CRLF or text conversion
    with pytest.raises(FileNotFoundError):
        source.read("data/untracked.txt")


def test_pinned_commit_does_not_follow_later_commits(tmp_repo):
    source = GitSource(_git_dir(tmp_repo))
    _commit(tmp_repo, {"data/a.txt": b"second\n"}, "second")
    assert source.read("data/a.txt") == b"committed\n"
    assert GitSource(_git_dir(tmp_repo)).read("data/a.txt") == b"second\n"


def test_missing_path_raises_filenotfounderror(tmp_repo):
    source = GitSource(_git_dir(tmp_repo))
    with pytest.raises(FileNotFoundError) as exc:
        source.read("data/nope.txt")
    assert exc.value.args == ("data/nope.txt",)


def test_directory_path_is_not_a_file(tmp_repo):
    with pytest.raises(GitSourceError, match="data"):
        GitSource(_git_dir(tmp_repo)).read("data")


def test_reads_from_bare_git_dir_only(tmp_repo, tmp_path):
    # Only the git directory is used: delete the working tree, and also try a real bare clone.
    shutil.rmtree(tmp_repo / "data")
    assert not (tmp_repo / "data").exists()
    assert GitSource(str(tmp_repo / ".git")).read("data/a.txt") == b"committed\n"

    bare = tmp_path / "bare.git"
    subprocess.run(
        ["git", "clone", "-q", "--bare", str(tmp_repo), str(bare)],
        check=True,
        capture_output=True,
        env=_clean_env(),
    )
    clone = GitSource(str(bare), "main")
    assert clone.commit == _git(tmp_repo, "rev-parse", "main")
    assert clone.read("data/a.txt") == b"committed\n"


def test_missing_git_binary_is_a_gitsourceerror_not_filenotfound(tmp_repo, monkeypatch):
    source = GitSource(_git_dir(tmp_repo))

    def no_git(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(gitsource.subprocess, "run", no_git)
    with pytest.raises(GitSourceError):
        source.read("data/a.txt")  # must not look like a missing *path*
    with pytest.raises(GitSourceError):
        GitSource(_git_dir(tmp_repo))


def test_every_git_call_is_hardened(tmp_repo, monkeypatch):
    git_dir = _git_dir(tmp_repo)
    _commit(tmp_repo, {"setenv_x.bat": b"x"}, "secret")
    real_run = subprocess.run
    calls: list[tuple[list[str], dict]] = []

    def spy(argv, **kwargs):
        calls.append((argv, kwargs))
        return real_run(argv, **kwargs)

    monkeypatch.setattr(gitsource.subprocess, "run", spy)
    source = GitSource(git_dir)
    source.read("data/a.txt")
    with pytest.raises(FileNotFoundError):
        source.read("data/missing.txt")
    source.secret_paths_in_history()

    assert {_subcommand(argv) for argv, _ in calls} >= {"rev-parse", "cat-file", "log"}
    for argv, kwargs in calls:
        assert isinstance(argv, list) and argv[0] == "git"
        assert not kwargs.get("shell")
        assert f"--git-dir={git_dir}" in argv
        for setting in (f"safe.directory={git_dir}", "core.fsmonitor=false", "core.hooksPath=/dev/null"):
            assert argv[argv.index(setting) - 1] == "-c", setting
        # `_subcommand` only finds the subcommand if every hardening option precedes it
        assert _subcommand(argv) in {"rev-parse", "cat-file", "log"}
        env = kwargs["env"]
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_TERMINAL_PROMPT"] == "0"


def test_secret_paths_found_in_history(tmp_repo):
    _commit(tmp_repo, {"setenv_x.bat": b"k", "deploy/setenv_deepseek.bat": b"k", ".env": b"k", "sub/.env": b"k"})
    _git(tmp_repo, "rm", "-q", "--", "setenv_x.bat", "deploy/setenv_deepseek.bat", ".env", "sub/.env")
    _git(tmp_repo, "commit", "-q", "-m", "delete the secrets again")
    assert not (tmp_repo / "setenv_x.bat").exists()  # gone from the tree, still in history
    assert GitSource(_git_dir(tmp_repo)).secret_paths_in_history() == [
        ".env",
        "deploy/setenv_deepseek.bat",
        "setenv_x.bat",
        "sub/.env",
    ]


def test_secret_paths_scan_covers_all_refs(tmp_repo):
    _git(tmp_repo, "checkout", "-q", "-b", "side")
    _commit(tmp_repo, {"keys/.env": b"k"}, "secret on a side branch")
    _git(tmp_repo, "checkout", "-q", "main")
    assert GitSource(_git_dir(tmp_repo), "main").secret_paths_in_history() == ["keys/.env"]


def test_secret_paths_with_non_ascii_names_are_not_missed(tmp_repo):
    # git would quote this path ("setenv_\340\246...bat") unless it is read NUL-delimited.
    name = "setenv_জন্ম.bat"
    _commit(tmp_repo, {name: b"k"})
    assert GitSource(_git_dir(tmp_repo)).secret_paths_in_history() == [name]


def test_clean_history_reports_no_secret_paths(tmp_repo):
    _commit(
        tmp_repo,
        {
            ".env.example": b"x",
            "setenv.sh": b"x",
            "notsetenv.bat": b"x",
            "a.env": b"x",
            "env": b"x",
            "docs/setenv.bat.md": b"x",
            "deploy/make_env.py": b"x",
        },
    )
    assert GitSource(_git_dir(tmp_repo)).secret_paths_in_history() == []


def test_dictsource_behaves_like_a_source():
    sha = "a" * 40
    files = {"data/a.txt": b"hello"}
    source: ContentSource = DictSource(sha, files)
    assert source.commit == sha
    assert source.read("data/a.txt") == b"hello"
    with pytest.raises(FileNotFoundError) as exc:
        source.read("data/missing.txt")
    assert exc.value.args == ("data/missing.txt",)
    files["data/a.txt"] = b"changed later"  # the source keeps its own copy
    assert source.read("data/a.txt") == b"hello"
