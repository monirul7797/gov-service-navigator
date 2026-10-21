"""Repo-safety checks: LF endings for Linux-bound files, .dockerignore and .gitignore coverage."""
import fnmatch
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LF_GLOBS = ["**/*.sh", "**/*.sql", "**/Dockerfile", "**/Caddyfile"]
# Third-party / generated trees are never scanned (".venv" is the project virtual environment).
SKIP_DIRS = {".git", "node_modules", "target", ".svelte-kit", "build", ".venv"}


def _lf_only_files():
    # "**/x" and "x" both mean "a file called x at any depth", so match on the basename.
    patterns = [glob.rsplit("/", 1)[-1] for glob in LF_GLOBS]
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]  # prune: never descend
        for name in filenames:
            if any(fnmatch.fnmatchcase(name, pat) for pat in patterns):
                yield Path(dirpath) / name


def test_lf_only_files_have_no_crlf():
    offenders = [
        str(path.relative_to(ROOT))
        for path in _lf_only_files()
        if b"\r\n" in path.read_bytes()
    ]
    assert offenders == []


def test_gitattributes_forces_lf():
    text = (ROOT / ".gitattributes").read_text()
    for pat in ["*.sh", "*.sql", "Dockerfile", "Caddyfile"]:
        assert f"{pat} text eol=lf" in text


def test_dockerignore_excludes_secrets_and_bulk():
    lines = set((ROOT / ".dockerignore").read_text().split())
    for entry in [
        ".git",
        ".env",
        "setenv*.bat",
        ".venv",
        ".superpowers",
        "data",
        "*_review_bundle*",
        "*.zip",
        "freshness_v21_final_review*",
        "**/node_modules",
        "**/target",
        "**/.svelte-kit",
        "**/build",
        "**/__pycache__",
    ]:
        assert entry in lines, f"{entry!r} missing from .dockerignore"


def test_gitignore_covers_new_artifacts():
    text = (ROOT / ".gitignore").read_text()
    for entry in [".env", "target/", "node_modules/", ".svelte-kit/", "frontend/*/build/", ".venv/"]:
        assert entry in text, f"{entry!r} missing from .gitignore"
    assert "setenv.bat" in text  # existing rule kept
