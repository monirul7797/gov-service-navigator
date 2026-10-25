"""Release content comes from a git commit, never from the working tree.

``GitSource`` reads files out of one pinned commit of a repository's git directory. It is built for a
container where ``.git`` is mounted read-only and owned by another user, so every git call is made
with a fixed, hardened command line: an argv list (no shell), an explicit ``--git-dir``, no
fsmonitor, no hooks, plumbing commands only, no replace refs, no system or global config, no terminal
prompts and none of the parent's inherited ``GIT_*`` variables. Failures raise
``GitSourceError`` naming the ref or path involved; the process environment is never put in a message.

Two kinds of failure are told apart. A *refusal* is a plain ``GitSourceError``: the owner asked for
something git cannot give (an unknown or option-like ref, a directory that is not a repository, a path
that is a directory). A *runtime failure* is ``GitRuntimeError`` (a subclass, so code that catches the
base class still catches it): git could not run (no binary, a timeout, an ``OSError``) or answered in
a way a working repository never does (corrupt object, a history scan that fails after the ref
resolved). The CLI maps a refusal to exit 2 and a runtime failure to exit 1, and the release inputs
never turn a runtime failure into an integrity problem.

``ContentSource`` is the small interface the release code depends on, so tests can use ``DictSource``.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Protocol

_FULL_SHA = re.compile(r"[0-9a-f]{40}")
# Files that hold API keys or passwords and must never be reachable from a mounted repository.
# Case-insensitive: a Windows checkout may have committed them as ``SETENV_X.BAT`` or ``.ENV``.
_SECRET_PATH = re.compile(r"(^|/)setenv[^/]*\.bat$|(^|/)\.env$", re.IGNORECASE)
_TIMEOUT_SECONDS = 120
_MAX_DETAIL = 300


class GitSourceError(Exception):
    """git could not be run, or a ref or path could not be resolved. Names the ref or path involved."""


class GitRuntimeError(GitSourceError):
    """git failed at run time rather than refusing the request: it could not be run (missing binary,
    timeout, ``OSError``) or it broke while reading a repository that had already resolved the ref."""


class ContentSource(Protocol):
    """Where release files are read from: a 40-hex ``commit`` and the bytes of a path at that commit."""

    commit: str

    def read(self, path: str) -> bytes:
        """Return the file's bytes; raise ``FileNotFoundError(path)`` if the commit has no such file."""
        ...


def _git_env() -> dict[str, str]:
    """The child environment: the parent's, minus every inherited ``GIT_*`` variable, plus ours.

    Inherited ``GIT_OBJECT_DIRECTORY``, ``GIT_ALTERNATE_OBJECT_DIRECTORIES``, ``GIT_CONFIG_COUNT``,
    ``GIT_CONFIG_PARAMETERS`` and the like could redirect object reads or inject config.
    ``GIT_NO_REPLACE_OBJECTS`` stops ``refs/replace`` in the (possibly poisoned) repository from making
    git serve one commit's content under another commit's id.
    """
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("GIT_")}
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def _detail(proc: subprocess.CompletedProcess[bytes]) -> str:
    text = proc.stderr.decode("utf-8", "replace").strip()
    return f": {text[:_MAX_DETAIL]}" if text else ""


class GitSource:
    """Reads files from one commit of ``git_dir``; ``ref`` (branch, tag, short SHA, ``HEAD``) is
    resolved once to a full 40-hex SHA, so later commits never change what ``read`` returns."""

    def __init__(self, git_dir: str, ref: str = "HEAD") -> None:
        self.git_dir = git_dir
        self.ref = ref
        self.commit = self._resolve(ref)

    def _run(self, args: list[str], what: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
        argv = [
            "git",
            f"--git-dir={self.git_dir}",
            "-c",
            f"safe.directory={self.git_dir}",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ]
        # ``input=`` and ``stdin=`` are mutually exclusive in subprocess.run.
        feed = {"stdin": subprocess.DEVNULL} if stdin is None else {"input": stdin}
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                env=_git_env(),
                timeout=_TIMEOUT_SECONDS,
                check=False,
                **feed,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # OSError includes FileNotFoundError (no git binary): never let that pass for a missing path.
            raise GitRuntimeError(f"could not run git for {what} ({type(exc).__name__})") from None

    def _resolve(self, ref: str) -> str:
        if not ref or ref.startswith("-"):
            raise GitSourceError(f"invalid git ref {ref!r}")
        what = f"ref {ref!r}"
        proc = self._run(["rev-parse", "--verify", f"{ref}^{{commit}}"], what)
        sha = proc.stdout.decode("ascii", "replace").strip()
        if proc.returncode != 0:
            raise GitSourceError(f"cannot resolve {what} to a commit in {self.git_dir}{_detail(proc)}")
        if not _FULL_SHA.fullmatch(sha):
            raise GitRuntimeError(f"{what} resolved to {sha!r}, which is not a 40-hex commit id")
        return sha

    def read(self, path: str) -> bytes:
        spec = f"{self.commit}:{path}"
        what = f"path {path!r} at commit {self.commit}"
        proc = self._run(["cat-file", "blob", spec], what)
        if proc.returncode == 0:
            return proc.stdout
        # Why did it fail? rev-parse --quiet exits 1 (silently) only when the object does not exist.
        probe = self._run(["rev-parse", "--verify", "--quiet", spec], what)
        if probe.returncode == 1:
            raise FileNotFoundError(path)
        if probe.returncode == 0:
            raise GitSourceError(f"{what} is not a file")
        # cat-file failed, yet the object is neither there (0) nor reported missing (1): git itself broke
        raise GitRuntimeError(f"cannot read {what}{_detail(proc)}")

    def secret_paths_in_history(self) -> list[str]:
        """Every path that looks like a secrets file in any commit the repository still holds.

        Plumbing only, so the repository's own ``log.*`` or ``diff.*`` config cannot hide anything:
        ``rev-list --all --reflog`` lists every commit reachable from a ref or a reflog entry (an
        amended-away commit is still in the mounted object store), and ``diff-tree -m --root`` lists
        the paths each one touches, including merge commits (against each parent) and root commits.
        Paths are read NUL-delimited (``-z``): git would otherwise quote a non-ASCII name and hide its
        suffix. Result is sorted and unique.
        """
        what = "secret-path scan"
        listing = self._run(["rev-list", "--all", "--reflog"], what)
        if listing.returncode != 0:
            raise GitRuntimeError(f"cannot list commits in {self.git_dir}{_detail(listing)}")
        if not listing.stdout.strip():
            return []
        touched = self._run(
            ["diff-tree", "--stdin", "-r", "-m", "--root", "--name-only", "-z", "--no-commit-id"],
            what,
            stdin=listing.stdout,
        )
        if touched.returncode != 0:
            raise GitRuntimeError(f"cannot list changed paths in {self.git_dir}{_detail(touched)}")
        paths = {p for p in touched.stdout.decode("utf-8", "replace").split("\0") if p}
        return sorted(p for p in paths if _SECRET_PATH.search(p))


class DictSource:
    """In-memory ``ContentSource`` for tests."""

    def __init__(self, commit: str, files: dict[str, bytes]) -> None:
        self.commit = commit
        self._files = dict(files)

    def read(self, path: str) -> bytes:
        try:
            return self._files[path]
        except KeyError:
            raise FileNotFoundError(path) from None
