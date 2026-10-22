"""Release content comes from a git commit, never from the working tree.

``GitSource`` reads files out of one pinned commit of a repository's git directory. It is built for a
container where ``.git`` is mounted read-only and owned by another user, so every git call is made
with a fixed, hardened command line: an argv list (no shell), an explicit ``--git-dir``, no
fsmonitor, no hooks, no system or global config and no terminal prompts. Failures raise
``GitSourceError`` naming the ref or path involved; the process environment is never put in a message.

``ContentSource`` is the small interface the release code depends on, so tests can use ``DictSource``.
"""
from __future__ import annotations

import os
import re
import subprocess
from typing import Protocol

_FULL_SHA = re.compile(r"[0-9a-f]{40}")
# Files that hold API keys or passwords and must never be reachable from a mounted repository.
_SECRET_PATH = re.compile(r"(^|/)setenv[^/]*\.bat$|(^|/)\.env$")
_TIMEOUT_SECONDS = 120
_MAX_DETAIL = 300


class GitSourceError(Exception):
    """git could not be run, or a ref or path could not be resolved. Names the ref or path involved."""


class ContentSource(Protocol):
    """Where release files are read from: a 40-hex ``commit`` and the bytes of a path at that commit."""

    commit: str

    def read(self, path: str) -> bytes:
        """Return the file's bytes; raise ``FileNotFoundError(path)`` if the commit has no such file."""
        ...


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
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

    def _run(self, args: list[str], what: str) -> subprocess.CompletedProcess[bytes]:
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
        try:
            return subprocess.run(
                argv,
                capture_output=True,
                env=_git_env(),
                stdin=subprocess.DEVNULL,
                timeout=_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # OSError includes FileNotFoundError (no git binary): never let that pass for a missing path.
            raise GitSourceError(f"could not run git for {what} ({type(exc).__name__})") from None

    def _resolve(self, ref: str) -> str:
        if not ref or ref.startswith("-"):
            raise GitSourceError(f"invalid git ref {ref!r}")
        what = f"ref {ref!r}"
        proc = self._run(["rev-parse", "--verify", f"{ref}^{{commit}}"], what)
        sha = proc.stdout.decode("ascii", "replace").strip()
        if proc.returncode != 0:
            raise GitSourceError(f"cannot resolve {what} to a commit in {self.git_dir}{_detail(proc)}")
        if not _FULL_SHA.fullmatch(sha):
            raise GitSourceError(f"{what} resolved to {sha!r}, which is not a 40-hex commit id")
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
        raise GitSourceError(f"cannot read {what}{_detail(proc)}")

    def secret_paths_in_history(self) -> list[str]:
        """Every path ever committed on any ref that looks like a secrets file, sorted and unique.

        Read NUL-delimited (``-z``): git would otherwise quote a non-ASCII name and hide its suffix.
        """
        proc = self._run(["log", "--all", "--name-only", "--format=", "-z"], "secret-path scan")
        if proc.returncode != 0:
            raise GitSourceError(f"cannot list history in {self.git_dir}{_detail(proc)}")
        paths = {p for p in proc.stdout.decode("utf-8", "replace").split("\0") if p}
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
