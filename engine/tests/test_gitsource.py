import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gsn import gitsource
from gsn.gitsource import ContentSource, DictSource, GitRuntimeError, GitSource, GitSourceError

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


def _init_repo(repo: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git executable not found on PATH; GitSource tests need the host git")
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


@pytest.fixture
def tmp_repo(tmp_path: Path) -> Path:
    """A throw-away repository on branch ``main`` with one commit (``data/a.txt``)."""
    repo = _init_repo(tmp_path / "repo")
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


# --- refusals and runtime failures are told apart (final review M2) --------------------------------------


def test_a_runtime_failure_is_still_a_gitsourceerror():
    assert issubclass(GitRuntimeError, GitSourceError)  # code that catches the base class keeps working


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError(2, "No such file or directory"),  # no git binary
        PermissionError(13, "Permission denied"),
        OSError(5, "Input/output error"),
        subprocess.TimeoutExpired(["git"], 120),
        subprocess.SubprocessError("odd"),
        ValueError("embedded null byte"),
    ],
    ids=lambda failure: type(failure).__name__,
)
def test_git_that_cannot_be_run_is_a_runtime_error(tmp_repo, monkeypatch, failure):
    source = GitSource(_git_dir(tmp_repo))

    def broken(*args, **kwargs):
        raise failure

    monkeypatch.setattr(gitsource.subprocess, "run", broken)
    with pytest.raises(GitRuntimeError) as read_error:
        source.read("data/a.txt")
    assert "data/a.txt" in str(read_error.value) and type(failure).__name__ in str(read_error.value)
    with pytest.raises(GitRuntimeError):
        source.secret_paths_in_history()
    with pytest.raises(GitRuntimeError):
        GitSource(_git_dir(tmp_repo))


def _completed(returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["git"], returncode, stdout, stderr)


def _fake_git(monkeypatch, answers: dict[str, subprocess.CompletedProcess]) -> None:
    """Every git call whose subcommand is a key of ``answers`` gets that answer; the rest run for real."""
    real_run = subprocess.run

    def fake(argv, **kwargs):
        sub = _subcommand(argv)
        return answers[sub] if sub in answers else real_run(argv, **kwargs)

    monkeypatch.setattr(gitsource.subprocess, "run", fake)


def test_an_unexpected_git_error_while_reading_an_object_is_a_runtime_error(tmp_repo, monkeypatch):
    source = GitSource(_git_dir(tmp_repo))
    # cat-file fails, and the follow-up probe is neither "found" (0) nor "does not exist" (1): git itself broke
    _fake_git(monkeypatch, {
        "cat-file": _completed(128, stderr=b"error: inflate: data stream error"),
        "rev-parse": _completed(128, stderr=b"fatal: bad object"),
    })
    with pytest.raises(GitRuntimeError) as exc:
        source.read("data/a.txt")
    assert "data/a.txt" in str(exc.value) and "inflate" in str(exc.value)


def test_an_unusable_answer_for_a_ref_that_resolved_is_a_runtime_error(tmp_repo, monkeypatch):
    _fake_git(monkeypatch, {"rev-parse": _completed(0, stdout=b"not-a-sha\n")})
    with pytest.raises(GitRuntimeError, match="not a 40-hex"):
        GitSource(_git_dir(tmp_repo))


def test_a_failing_rev_list_is_a_runtime_error(tmp_repo, monkeypatch):
    source = GitSource(_git_dir(tmp_repo))  # the ref resolved, so the repository is fine: a later failure is not a refusal
    _fake_git(monkeypatch, {"rev-list": _completed(128, stderr=b"fatal: oops")})
    with pytest.raises(GitRuntimeError, match="oops"):
        source.secret_paths_in_history()


def test_a_failing_diff_tree_is_a_runtime_error(tmp_repo, monkeypatch):
    source = GitSource(_git_dir(tmp_repo))
    _fake_git(monkeypatch, {
        "rev-list": _completed(0, stdout=source.commit.encode() + b"\n"),
        "diff-tree": _completed(128, stderr=b"fatal: oops"),
    })
    with pytest.raises(GitRuntimeError, match="oops"):
        source.secret_paths_in_history()


def test_refusals_are_not_runtime_errors(tmp_repo, tmp_path):
    git_dir = _git_dir(tmp_repo)
    source = GitSource(git_dir)
    cases = {
        "an unknown ref": lambda: GitSource(git_dir, "no-such-branch"),
        "an option-like ref": lambda: GitSource(git_dir, "--all"),
        "an empty ref": lambda: GitSource(git_dir, ""),
        "a directory read as a file": lambda: source.read("data"),
        "a directory that is not a repository": lambda: GitSource(str(tmp_path / "nowhere.git")),
    }
    for what, call in cases.items():
        with pytest.raises(GitSourceError) as exc:
            call()
        assert not isinstance(exc.value, GitRuntimeError), what


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

    plumbing = {"rev-parse", "cat-file", "rev-list", "diff-tree"}  # no porcelain such as `log`
    assert {_subcommand(argv) for argv, _ in calls} == plumbing
    for argv, kwargs in calls:
        assert isinstance(argv, list) and argv[0] == "git"
        assert not kwargs.get("shell")
        assert f"--git-dir={git_dir}" in argv
        for setting in (f"safe.directory={git_dir}", "core.fsmonitor=false", "core.hooksPath=/dev/null"):
            assert argv[argv.index(setting) - 1] == "-c", setting
        # `_subcommand` only finds the subcommand if every hardening option precedes it
        assert _subcommand(argv) in plumbing
        env = kwargs["env"]
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == os.devnull
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_inherited_git_variables_never_reach_git(tmp_repo, monkeypatch):
    inherited = {
        "GIT_DIR": str(tmp_repo / "elsewhere"),
        "GIT_OBJECT_DIRECTORY": str(tmp_repo / "objects-elsewhere"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(tmp_repo / "alt"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "true",
        "GIT_CONFIG_PARAMETERS": "'core.hooksPath=/tmp/evil'",
        "GIT_EXEC_PATH": str(tmp_repo / "exec"),
        "GIT_TERMINAL_PROMPT": "1",
        "GIT_NO_REPLACE_OBJECTS": "",
    }
    for name, value in inherited.items():
        monkeypatch.setenv(name, value)
    real_run = subprocess.run
    envs: list[dict[str, str]] = []

    def spy(argv, **kwargs):
        envs.append(kwargs["env"])
        return real_run(argv, **kwargs)

    monkeypatch.setattr(gitsource.subprocess, "run", spy)
    source = GitSource(_git_dir(tmp_repo))
    source.read("data/a.txt")
    source.secret_paths_in_history()
    assert envs
    for env in envs:
        assert {k for k in env if k.upper().startswith("GIT_")} == {
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
            "GIT_TERMINAL_PROMPT",
            "GIT_NO_REPLACE_OBJECTS",
        }
        assert env["GIT_TERMINAL_PROMPT"] == "0" and env["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_bogus_git_object_directory_in_the_environment_does_not_affect_reads(tmp_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_OBJECT_DIRECTORY", str(tmp_path / "no-such-objects"))
    monkeypatch.setenv("GIT_ALTERNATE_OBJECT_DIRECTORIES", str(tmp_path / "no-such-alternates"))
    source = GitSource(_git_dir(tmp_repo))
    assert source.read("data/a.txt") == b"committed\n"
    assert source.secret_paths_in_history() == []


def test_replace_ref_cannot_make_read_return_another_commits_bytes(tmp_repo):
    first = _git(tmp_repo, "rev-parse", "HEAD")
    second = _commit(tmp_repo, {"data/a.txt": b"second\n"}, "second")
    # Poison the repository: "second" now *looks like* "first" to any git that honours replace refs.
    _git(tmp_repo, "replace", second, first)
    source = GitSource(_git_dir(tmp_repo), "main")
    assert source.commit == second
    assert source.read("data/a.txt") == b"second\n"  # the pinned commit's own bytes, not the replacement's


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


def test_secret_path_names_match_case_insensitively(tmp_repo):
    _commit(tmp_repo, {"SETENV_X.BAT": b"k", "deploy/.ENV": b"k", ".env.example": b"x", "Docs/Setenv.Bat.md": b"x"})
    assert GitSource(_git_dir(tmp_repo)).secret_paths_in_history() == ["SETENV_X.BAT", "deploy/.ENV"]


def test_secret_added_only_in_a_merge_commit_is_found(tmp_repo):
    _git(tmp_repo, "checkout", "-q", "-b", "side")
    _commit(tmp_repo, {"side.txt": b"s"}, "side")
    _git(tmp_repo, "checkout", "-q", "main")
    _commit(tmp_repo, {"main.txt": b"m"}, "main")
    _git(tmp_repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    (tmp_repo / ".env").write_bytes(b"k")  # appears in neither parent, only in the merge itself
    _git(tmp_repo, "add", "-f", "--", ".env")
    _git(tmp_repo, "commit", "-q", "-m", "merge side")
    assert len(_git(tmp_repo, "rev-list", "--parents", "-n", "1", "HEAD").split()) == 3  # a real merge
    assert GitSource(_git_dir(tmp_repo)).secret_paths_in_history() == [".env"]


def test_secret_in_root_commit_is_found_even_if_repo_config_hides_root_commits(tmp_path):
    repo = _init_repo(tmp_path / "root-repo")
    _git(repo, "config", "log.showRoot", "false")  # porcelain `git log` would honour this repo config
    _commit(repo, {".env": b"k", "ok.txt": b"x"}, "root")
    assert GitSource(_git_dir(repo)).secret_paths_in_history() == [".env"]


def test_secret_in_an_amended_away_commit_is_found_via_the_reflog(tmp_repo):
    _commit(tmp_repo, {"setenv_x.bat": b"k"}, "oops, committed a secret")
    old = _git(tmp_repo, "rev-parse", "HEAD")
    _git(tmp_repo, "rm", "-q", "--", "setenv_x.bat")
    _git(tmp_repo, "commit", "-q", "--amend", "--no-edit", "--allow-empty")
    assert _git(tmp_repo, "rev-parse", "HEAD") != old
    assert _git(tmp_repo, "branch", "--contains", old) == ""  # no branch reaches the old commit any more
    assert GitSource(_git_dir(tmp_repo)).secret_paths_in_history() == ["setenv_x.bat"]


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
