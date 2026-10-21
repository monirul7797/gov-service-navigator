"""Repo-safety checks: LF endings for Linux-bound files, .dockerignore/.gitignore/.gitattributes coverage.

The ignore/attribute checks look at ACTIVE lines only (not blank, not a comment) and require
exact membership, so a protecting rule that is commented out, deleted or merely implied by a
longer pattern is noticed. The check functions take the file text, so the tests at the bottom
can prove, on temporary copies, that each guard really fails when a rule is disabled.
"""
import fnmatch
import os
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LF_GLOBS = ["**/*.sh", "**/*.sql", "**/Dockerfile", "**/Caddyfile"]
# Directory names (glob patterns) that are never scanned: VCS, third-party/generated trees,
# tool state and the untracked review bundles at the repo root.
SKIP_DIRS = {
    ".git",
    "node_modules",
    "target",
    ".svelte-kit",
    "build",
    ".venv",
    ".claude",
    ".superpowers",
    "*_review_bundle*",
    "freshness_v21_final_review*",
}

DOCKERIGNORE_REQUIRED = [
    ".git",
    ".env",
    "setenv*.bat",
    ".venv",
    ".superpowers",
    "data",
    "*_review_bundle*",
    "*.zip",
    "freshness_v21_final_review*",
    "**/.env.*",
    "**/setenv*.bat",
    "**/node_modules",
    "**/target",
    "**/.svelte-kit",
    "**/build",
    "**/__pycache__",
]
GITIGNORE_REQUIRED = [
    "setenv.bat",  # existing rule kept
    ".env",
    "target/",
    "node_modules/",
    ".svelte-kit/",
    "frontend/*/build/",
    ".venv/",
]
GITATTRIBUTES_REQUIRED = [
    "* text=auto",
    "*.sh text eol=lf",
    "*.sql text eol=lf",
    "Dockerfile text eol=lf",
    "Caddyfile text eol=lf",
]
# Secret / bulk-data entries that must never be re-included by a "!" negation. Each maps to
# concrete names a negation pattern could match.
DOCKERIGNORE_SECRETS = {
    ".env": [".env"],
    "setenv*.bat": ["setenv.bat", "setenv_deepseek.bat"],
    "data": ["data"],
}
GITIGNORE_SECRETS = {
    ".env": [".env"],
    "setenv.bat": ["setenv.bat"],
}


# --- scanning for CRLF ---------------------------------------------------------------------


def _is_skipped_dir(name):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in SKIP_DIRS)


def find_crlf_files(root):
    """Relative POSIX paths of files under ``root`` matching LF_GLOBS whose bytes contain CRLF."""
    root = Path(root)
    # "**/x" and "x" both mean "a file called x at any depth", so match on the basename.
    patterns = [glob.rsplit("/", 1)[-1] for glob in LF_GLOBS]
    offenders = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _is_skipped_dir(d)]  # prune: never descend
        for name in filenames:
            if any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns):
                path = Path(dirpath) / name
                if b"\r\n" in path.read_bytes():
                    offenders.append(path.relative_to(root).as_posix())
    return sorted(offenders)


# --- guards on ignore/attribute files (operate on the file text) ---------------------------


def active_lines(text, collapse_whitespace=False):
    """Stripped lines that are neither blank nor comments."""
    lines = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if collapse_whitespace:
            line = " ".join(line.split())
        lines.add(line)
    return lines


def missing_entries(text, required, collapse_whitespace=False):
    present = active_lines(text, collapse_whitespace)
    return [entry for entry in required if entry not in present]


def reincluded_secrets(text, secrets):
    """Active "!" negations that would re-include one of the ``secrets`` (or a path under it)."""
    found = []
    for line in sorted(active_lines(text)):
        if not line.startswith("!"):
            continue
        pattern = line[1:].strip()
        while pattern.startswith("**/"):
            pattern = pattern[3:]
        pattern = pattern.strip("/")
        for entry, names in secrets.items():
            under_entry = pattern == entry or pattern.startswith(entry + "/")
            if under_entry or any(fnmatch.fnmatchcase(name, pattern) for name in names):
                found.append(line)
                break
    return found


def _problems(text, required, secrets=None, collapse_whitespace=False):
    problems = [
        f"missing active entry {entry!r}"
        for entry in missing_entries(text, required, collapse_whitespace)
    ]
    if secrets:
        problems += [
            f"negation {line!r} re-includes a secret/bulk path" for line in reincluded_secrets(text, secrets)
        ]
    return problems


def check_dockerignore(text):
    return _problems(text, DOCKERIGNORE_REQUIRED, DOCKERIGNORE_SECRETS)


def check_gitignore(text):
    return _problems(text, GITIGNORE_REQUIRED, GITIGNORE_SECRETS)


def check_gitattributes(text):
    return _problems(text, GITATTRIBUTES_REQUIRED, collapse_whitespace=True)


def _read(name):
    return (ROOT / name).read_text(encoding="utf-8-sig")


# --- checks on the real repository ---------------------------------------------------------


def test_lf_only_files_have_no_crlf():
    assert find_crlf_files(ROOT) == []


def test_gitattributes_forces_lf():
    assert check_gitattributes(_read(".gitattributes")) == []


def test_dockerignore_excludes_secrets_and_bulk():
    assert check_dockerignore(_read(".dockerignore")) == []


def test_gitignore_covers_new_artifacts():
    assert check_gitignore(_read(".gitignore")) == []


# --- proof that the guards fail when a rule is disabled (temporary copies only) ------------

CHECKS = {
    ".dockerignore": (check_dockerignore, DOCKERIGNORE_REQUIRED),
    ".gitignore": (check_gitignore, GITIGNORE_REQUIRED),
    ".gitattributes": (check_gitattributes, GITATTRIBUTES_REQUIRED),
}


def _edited_copy(tmp_path, name, edit):
    """Copy a real repo file into tmp_path, apply ``edit(lines) -> lines``, return the new text."""
    copy = tmp_path / name
    shutil.copy(ROOT / name, copy)
    lines = copy.read_text(encoding="utf-8-sig").splitlines()
    copy.write_text("\n".join(edit(lines)) + "\n", encoding="utf-8", newline="\n")
    return copy.read_text(encoding="utf-8")


def _comment_out(entry):
    def edit(lines):
        assert [line.strip() for line in lines].count(entry) == 1, f"{entry!r} not in file once"
        return [("# " + line) if line.strip() == entry else line for line in lines]

    return edit


def _delete(entry):
    def edit(lines):
        assert [line.strip() for line in lines].count(entry) == 1, f"{entry!r} not in file once"
        return [line for line in lines if line.strip() != entry]

    return edit


def _append(*extra):
    return lambda lines: lines + list(extra)


@pytest.mark.parametrize(
    "name, entry",
    [(name, entry) for name, (_, required) in CHECKS.items() for entry in required],
)
def test_guard_fails_when_a_rule_is_commented_out(tmp_path, name, entry):
    check, _ = CHECKS[name]
    text = _edited_copy(tmp_path, name, _comment_out(entry))
    assert check(text) == [f"missing active entry {entry!r}"]


@pytest.mark.parametrize("name, entry", [(".gitignore", ".env"), (".dockerignore", ".env"), (".dockerignore", "data")])
def test_guard_fails_when_a_rule_is_deleted_even_if_longer_patterns_remain(tmp_path, name, entry):
    # ".env.*" and "!.env.example" (or "data/..." style lines) still contain the text ".env"/"data".
    check, _ = CHECKS[name]
    text = _edited_copy(tmp_path, name, _delete(entry))
    assert check(text) == [f"missing active entry {entry!r}"]


def test_guard_ignores_indented_comment_as_a_rule(tmp_path):
    text = _edited_copy(tmp_path, ".dockerignore", lambda lines: [
        ("    # setenv*.bat" if line.strip() == "setenv*.bat" else line) for line in lines
    ])
    assert check_dockerignore(text) == ["missing active entry 'setenv*.bat'"]


def test_guard_fails_on_commented_out_gitattributes_with_extra_spaces(tmp_path):
    text = _edited_copy(tmp_path, ".gitattributes", lambda lines: [
        ("#   *.sh   text   eol=lf" if line.strip() == "*.sh text eol=lf" else line) for line in lines
    ])
    assert check_gitattributes(text) == ["missing active entry '*.sh text eol=lf'"]


def test_guard_accepts_gitattributes_with_irregular_spacing(tmp_path):
    text = _edited_copy(tmp_path, ".gitattributes", lambda lines: [
        ("*.sh    text    eol=lf" if line.strip() == "*.sh text eol=lf" else line) for line in lines
    ])
    assert check_gitattributes(text) == []


@pytest.mark.parametrize(
    "negation",
    ["!.env", "!setenv*.bat", "!data", "!data/chunks/visa.jsonl", "!**/.env", "!.env*", "!*.bat", "!*"],
)
def test_dockerignore_guard_fails_on_negation_of_secret_or_bulk(tmp_path, negation):
    text = _edited_copy(tmp_path, ".dockerignore", _append(negation))
    assert check_dockerignore(text) == [f"negation {negation!r} re-includes a secret/bulk path"]


@pytest.mark.parametrize("negation", ["!.env", "!setenv.bat", "!.env*", "!*"])
def test_gitignore_guard_fails_on_negation_of_secret(tmp_path, negation):
    text = _edited_copy(tmp_path, ".gitignore", _append(negation))
    assert check_gitignore(text) == [f"negation {negation!r} re-includes a secret/bulk path"]


def test_guard_allows_unrelated_negation_and_ignores_commented_negation(tmp_path):
    # the real .gitignore already has "!.env.example"; a commented-out "!.env" is not a rule
    text = _edited_copy(tmp_path, ".gitignore", _append("# !.env", "!keep-me.txt"))
    assert check_gitignore(text) == []


def test_real_gitignore_still_allows_env_example():
    assert "!.env.example" in active_lines(_read(".gitignore"))


# --- the CRLF scanner itself ---------------------------------------------------------------


def test_scanner_reports_crlf_in_every_watched_file_type(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "db" / "init").mkdir(parents=True)
    (tmp_path / "sub" / "x.sh").write_bytes(b"#!/bin/sh\r\necho hi\r\n")
    (tmp_path / "Dockerfile").write_bytes(b"FROM scratch\r\n")
    (tmp_path / "sub" / "Caddyfile").write_bytes(b":80 {\r\n}\r\n")
    (tmp_path / "db" / "init" / "q.sql").write_bytes(b"SELECT 1;\r\n")
    # LF-only watched files and CRLF in unwatched types are not reported
    (tmp_path / "db" / "init" / "ok.sql").write_bytes(b"SELECT 1;\n")
    (tmp_path / "sub" / "ok.sh").write_bytes(b"#!/bin/sh\necho hi\n")
    (tmp_path / "sub" / "notes.txt").write_bytes(b"a\r\nb\r\n")
    assert find_crlf_files(tmp_path) == ["Dockerfile", "db/init/q.sql", "sub/Caddyfile", "sub/x.sh"]


def test_scanner_skips_vcs_third_party_and_review_bundle_dirs(tmp_path):
    skipped = [
        ".git/hooks",
        "node_modules/pkg",
        "frontend/app/node_modules/pkg",
        "target/debug",
        ".svelte-kit/output",
        "frontend/app/build",
        ".venv/Lib",
        ".claude/worktrees/w1",
        ".superpowers/sdd",
        "phase3_6_visa_review_bundle/src",
        "freshness_v21_final_review/src",
    ]
    for rel in skipped:
        directory = tmp_path / rel
        directory.mkdir(parents=True)
        (directory / "x.sh").write_bytes(b"echo\r\n")
    (tmp_path / "kept").mkdir()
    (tmp_path / "kept" / "x.sh").write_bytes(b"echo\r\n")
    assert find_crlf_files(tmp_path) == ["kept/x.sh"]
