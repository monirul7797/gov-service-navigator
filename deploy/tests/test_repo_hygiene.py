"""Repo-safety checks: LF endings for Linux-bound files, .dockerignore/.gitignore/.gitattributes coverage.

The ignore/attribute checks look at ACTIVE lines only (not blank, not a comment) and require
exact membership, so a protecting rule that is commented out, deleted or merely implied by a
longer pattern is noticed. The check functions take the file text, so the tests at the bottom
can prove, on temporary copies, that each guard really fails when a rule is disabled.
"""
import fnmatch
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

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
    "**/*.egg-info",
    "**/.pytest_cache",
    ".venv-constraints",  # throw-away venv of the constraints-regeneration recipe
    "constraints.raw",
]
GITIGNORE_REQUIRED = [
    "setenv.bat",  # existing rule kept
    ".env",
    "target/",
    "node_modules/",
    ".svelte-kit/",
    "frontend/*/build/",
    ".venv/",
    "*.egg-info/",
    ".venv-constraints/",
    "constraints.raw",
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


# --- the shell scripts PostgreSQL runs at first start must be executable in git -----------------
#
# The postgres image runs every ``*.sh`` in /docker-entrypoint-initdb.d directly when it is executable,
# and SOURCES it into the entrypoint's own shell when it is not (which then inherits ``set -e`` and
# the script's variables). The folder is mounted read-only from the checkout, so what the container
# sees is the mode recorded in git: a checkout on Windows cannot show it, ``git ls-files -s`` does.

INIT_DIR = "db/init"
EXECUTABLE_MODE = "100755"


def non_executable_scripts(ls_files_stage):
    """``path (mode)`` of every ``*.sh`` in ``git ls-files -s`` output whose recorded mode is not 100755."""
    offenders = []
    for line in ls_files_stage.splitlines():
        meta, _, path = line.partition("\t")
        mode = meta.split(" ", 1)[0]
        if path.endswith(".sh") and mode != EXECUTABLE_MODE:
            offenders.append(f"{path} ({mode})")
    return offenders


def _tracked_init_scripts():
    try:
        result = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-s", "--", INIT_DIR],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        pytest.skip("git is not installed: the recorded file modes cannot be read")
    if result.returncode != 0:
        pytest.skip(f"not a git checkout: {result.stderr.strip()}")
    return result.stdout


def test_db_init_scripts_are_recorded_as_executable_in_git():
    listing = _tracked_init_scripts()
    scripts = [line.partition("\t")[2] for line in listing.splitlines() if line.endswith(".sh")]
    # not vacuous: both init scripts are tracked (test_compose_exposure.py pins their names and order)
    assert scripts == [f"{INIT_DIR}/10-roles.sh", f"{INIT_DIR}/20-guard.sh"]
    assert non_executable_scripts(listing) == []


@pytest.mark.parametrize(
    "mode, offends",
    [("100755", False), ("100644", True), ("120000", True), ("100664", True)],
)
def test_the_mode_check_flags_anything_but_100755(mode, offends):
    listing = f"{mode} fb2ac5add22a4d0e75c787e62cc361cf49950d04 0\tdb/init/10-roles.sh\n"
    assert non_executable_scripts(listing) == ([f"db/init/10-roles.sh ({mode})"] if offends else [])


def test_the_mode_check_ignores_files_that_are_not_shell_scripts():
    listing = "100644 58573a5aa60a65051ebe48fece11f6c7d17109a1 0\tdb/init/README.md\n"
    assert non_executable_scripts(listing) == []


# --- pytest imports test modules by file path, so same-named modules in different folders coexist --
#
# In the default ("prepend") import mode a test module is imported under its bare file name, so
# engine/tests/test_store.py and tests/integration/test_store.py (or any future pair) cannot be
# collected in one run: "import file mismatch". engine/pyproject.toml therefore sets
# ``--import-mode=importlib``; ``python -m pytest engine/tests deploy/tests tools/tests`` (the documented
# command and the CI step) finds that file first, so the option applies to deploy/tests and tools/tests too.

ENGINE_PYPROJECT = ROOT / "engine" / "pyproject.toml"


def _pytest_collect(tmp_path, *extra):
    """Collect two same-named test files from two folders with the settings of engine/pyproject.toml."""
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "test_same_name.py").write_text(f"def test_in_{folder}():\n    pass\n", encoding="utf-8")
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", "-c", str(ENGINE_PYPROJECT), "--rootdir", str(tmp_path),
            "-p", "no:cacheprovider", "--collect-only", "-q", *extra, str(tmp_path / "a"), str(tmp_path / "b"),
        ],
        capture_output=True, text=True, check=False, cwd=tmp_path,
    )


def test_pytest_options_in_engine_pyproject_import_by_path():
    options = tomllib.loads(ENGINE_PYPROJECT.read_text(encoding="utf-8"))["tool"]["pytest"]["ini_options"]
    addopts = options.get("addopts", [])
    addopts = addopts.split() if isinstance(addopts, str) else addopts
    assert "--import-mode=importlib" in addopts


def test_same_named_test_modules_in_two_folders_can_be_collected_together(tmp_path):
    result = _pytest_collect(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "test_in_a" in result.stdout and "test_in_b" in result.stdout


def test_the_collision_is_real_without_the_option(tmp_path):
    # Control for the test above: the same two files do collide in the default import mode.
    result = _pytest_collect(tmp_path, "--import-mode=prepend")
    assert result.returncode != 0
    assert "import file mismatch" in result.stdout + result.stderr


# --- every action a workflow runs is pinned to a full commit SHA ----------------------------------
#
# A tag such as ``actions/checkout@v4`` can be moved to different code after it was reviewed, and a
# workflow runs with the repository's token, so every ``uses:`` names a 40-character commit SHA (the
# release it belongs to goes in a trailing comment; Dependabot keeps both up to date). Actions that
# live in this repository (``./...``) have no ref to pin and are exempt. A ``docker://`` image is not
# exempt: it must be replaced by a pinned action, or this check taught about digests first.

WORKFLOWS_DIR = ROOT / ".github" / "workflows"
PINNED_ACTION = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}")


def uses_values(workflow_text):
    """Every ``uses:`` value of one workflow, job-level (reusable workflows) and step-level."""
    workflow = yaml.safe_load(workflow_text) or {}
    values = []
    for job in (workflow.get("jobs") or {}).values():
        values.append(job.get("uses"))
        values += [step.get("uses") for step in job.get("steps") or []]
    return [str(value) for value in values if value is not None]


def unpinned_actions(workflow_text):
    """The ``uses:`` values that are not ``owner/repo[/path]@<40 lowercase hex>`` (``./`` ones are not reported)."""
    return [
        value for value in uses_values(workflow_text)
        if not value.startswith("./") and not PINNED_ACTION.fullmatch(value)
    ]


def unpinned_in_directory(directory):
    """``file: value`` for every unpinned action in the ``*.yml`` / ``*.yaml`` workflows of ``directory``."""
    problems = []
    for path in sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")]):
        problems += [f"{path.name}: {value}" for value in unpinned_actions(path.read_text(encoding="utf-8"))]
    return problems


def _workflow_with_step(uses):
    return f"jobs:\n  j:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: {uses}\n"


def test_every_action_in_the_workflows_is_pinned_to_a_commit_sha():
    assert unpinned_in_directory(WORKFLOWS_DIR) == []


def test_the_pin_check_really_sees_the_actions_of_both_workflows():
    # Not vacuous: ci.yml and evals.yml exist and the check reads real actions out of each of them.
    for name, action in [("ci.yml", "actions/checkout@"), ("evals.yml", "actions/cache/restore@")]:
        values = uses_values((WORKFLOWS_DIR / name).read_text(encoding="utf-8"))
        assert any(value.startswith(action) for value in values), name
        assert all(PINNED_ACTION.fullmatch(value) for value in values), name


@pytest.mark.parametrize(
    "value",
    [
        "actions/checkout@v4",  # a movable tag
        "actions/checkout@v4.2.3",
        "actions/checkout@main",  # a branch
        "actions/checkout@3d3c42e",  # an abbreviated SHA
        "actions/checkout@" + "3" * 39,  # one character short
        "actions/checkout@" + "3" * 41,  # one character too long
        "actions/checkout@" + "A" * 40,  # upper case: a commit SHA is written in lower case
        "actions/checkout",  # no ref at all (runs the default branch)
        "actions/cache/restore@v6",  # an action in a subfolder
        "docker://alpine:3.20",
    ],
)
def test_an_unpinned_action_is_reported(value):
    assert unpinned_actions(_workflow_with_step(value)) == [value]


@pytest.mark.parametrize(
    "value",
    [
        "actions/checkout@" + "3" * 40 + " # v7.0.1",  # the release goes in a trailing comment
        "actions/cache/restore@" + "0123456789abcdef" * 2 + "01234567",
        "./.github/actions/local-action",  # in this repository: nothing to pin
    ],
)
def test_a_pinned_or_local_action_is_accepted(value):
    assert unpinned_actions(_workflow_with_step(value)) == []


def test_an_unpinned_reusable_workflow_is_reported():
    text = "jobs:\n  j:\n    uses: octo/repo/.github/workflows/w.yml@main\n"
    assert unpinned_actions(text) == ["octo/repo/.github/workflows/w.yml@main"]


def test_guard_fails_when_one_action_of_a_real_workflow_is_unpinned(tmp_path):
    original = (WORKFLOWS_DIR / "ci.yml").read_text(encoding="utf-8")
    mutated = re.sub(r"@[0-9a-f]{40}", "@v7", original, count=1)  # the first pinned action loses its SHA
    assert mutated != original
    (tmp_path / "ci.yml").write_text(mutated, encoding="utf-8", newline="\n")
    (tmp_path / "other.yaml").write_text(original, encoding="utf-8", newline="\n")  # the intact copy is fine
    assert unpinned_in_directory(tmp_path) == ["ci.yml: actions/checkout@v7"]
