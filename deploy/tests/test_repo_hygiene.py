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
from collections import Counter
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


def _workflow_files(directory):
    return sorted([*directory.glob("*.yml"), *directory.glob("*.yaml")])


def unpinned_in_directory(directory):
    """``file: value`` for every unpinned action in the ``*.yml`` / ``*.yaml`` workflows of ``directory``."""
    problems = []
    for path in _workflow_files(directory):
        problems += [f"{path.name}: {value}" for value in unpinned_actions(path.read_text(encoding="utf-8"))]
    return problems


USES_LINE = re.compile(r"^[ \t]*(?:-[ \t]+)?uses:[ \t]*(\S+)", re.MULTILINE)


def parser_misses(workflow_text):
    """``uses:`` values that a plain text scan of the file finds but ``uses_values`` does not.

    Guards the check against a workflow shape the parser silently skips; the answer for a sound file is []."""
    scanned = Counter(value.strip("\"'") for value in USES_LINE.findall(workflow_text))
    return sorted((scanned - Counter(uses_values(workflow_text))).elements())


def _workflow_with_step(uses):
    return f"jobs:\n  j:\n    runs-on: ubuntu-24.04\n    steps:\n      - uses: {uses}\n"


def test_every_action_in_the_workflows_is_pinned_to_a_commit_sha():
    assert unpinned_in_directory(WORKFLOWS_DIR) == []


def test_the_pin_check_really_sees_the_actions_of_every_workflow():
    # Not vacuous: both workflows are scanned, each has at least one pinned (non-local) action, and the
    # parser found every ``uses:`` that a plain text scan of the file finds.
    paths = _workflow_files(WORKFLOWS_DIR)
    assert {"ci.yml", "evals.yml"} <= {path.name for path in paths}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        external = [value for value in uses_values(text) if not value.startswith("./")]
        assert any(PINNED_ACTION.fullmatch(value) for value in external), path.name
        assert all(PINNED_ACTION.fullmatch(value) for value in external), path.name
        assert parser_misses(text) == [], path.name


def test_the_text_scan_notices_a_uses_line_the_parser_does_not_read():
    # A ``uses:`` line inside a ``run: |`` block is script text to YAML, not an action; the cross-check
    # reports it so that a workflow the parser misreads cannot pass unnoticed.
    text = "jobs:\n  j:\n    runs-on: x\n    steps:\n      - run: |\n          echo\n          uses: octo/tool@v1\n"
    assert uses_values(text) == []
    assert parser_misses(text) == ["octo/tool@v1"]


def test_the_text_scan_agrees_with_the_parser_on_a_normal_workflow():
    text = _workflow_with_step("octo/tool@" + "a" * 40 + " # v1.2.3") + "      - uses: ./local\n      - uses: \"octo/q@v2\"\n"
    assert parser_misses(text) == []


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


# --- the real-model eval workflow: when it runs and which model it caches ------------------------
#
# evals.yml builds a release with the real embedding model and judges it. Two things in it must follow
# the rest of the repository, and a mistake in either shows only on the GitHub runner, so they are
# checked here:
#   * the ``paths`` filter lists everything that changes what the gate measures (the retrieval and
#     release code, the image and its locked packages, the migrations, the data and gold queries, the
#     committed baseline), and ``push`` and ``pull_request`` use the same list. GitHub reads the
#     patterns in order: a ``!`` pattern removes what the positive patterns before it matched, so it must
#     come after them;
#   * the model cache key ends with the model revision pinned in engine/src/gsn/embed.py, so a bumped pin
#     cannot keep restoring the old snapshot.

EVALS_WORKFLOW = WORKFLOWS_DIR / "evals.yml"
EMBED_PY = ROOT / "engine" / "src" / "gsn" / "embed.py"
EVALS_PATHS_REQUIRED = [
    "engine/src/gsn/**",
    "!engine/src/gsn/engine/**",
    "!engine/src/gsn/worker/**",
    "engine/Dockerfile",
    "engine/constraints.txt",
    "engine/constraints-ml.txt",
    "engine/pyproject.toml",
    "db/migrations/**",
    "compose.yaml",
    "data/**",
    "*_gold_queries.json",
    "concept_registry.json",
    "docs/reports/m1-parity/**",
    ".github/workflows/evals.yml",
]
CACHE_KEY_REFERENCE = "${{ env.MODEL_CACHE_KEY }}"


def workflow_triggers(workflow_text):
    """The ``on:`` mapping of a workflow (YAML 1.1 reads the key ``on`` as the boolean ``True``)."""
    workflow = yaml.safe_load(workflow_text) or {}
    return workflow.get("on", workflow.get(True)) or {}


def evals_path_problems(workflow_text):
    triggers = workflow_triggers(workflow_text)
    lists = {event: (triggers.get(event) or {}).get("paths") or [] for event in ("push", "pull_request")}
    problems = [f"{event} has no paths filter" for event, paths in lists.items() if not paths]
    if problems:
        return problems
    if lists["push"] != lists["pull_request"]:
        problems.append("push and pull_request paths differ")
    paths = lists["push"]
    problems += [f"missing path pattern {entry!r}" for entry in EVALS_PATHS_REQUIRED if entry not in paths]
    if "engine/src/gsn/**" in paths:
        first = paths.index("engine/src/gsn/**")
        problems += [
            f"{entry!r} comes before 'engine/src/gsn/**', so it excludes nothing"
            for entry in EVALS_PATHS_REQUIRED
            if entry.startswith("!") and entry in paths and paths.index(entry) < first
        ]
    return problems


def model_revision(embed_text):
    """``MODEL_REVISION`` of engine/src/gsn/embed.py, read as text (the module needs the ML packages)."""
    match = re.search(r'^MODEL_REVISION\s*=\s*"([0-9a-f]{40})"\s*$', embed_text, re.MULTILINE)
    assert match, "MODEL_REVISION = \"<40 hex>\" not found in embed.py"
    return match.group(1)


def cache_key_problems(workflow_text, revision):
    workflow = yaml.safe_load(workflow_text) or {}
    jobs = list((workflow.get("jobs") or {}).values())
    environments = [workflow.get("env") or {}, *[job.get("env") or {} for job in jobs]]
    keys = [env["MODEL_CACHE_KEY"] for env in environments if "MODEL_CACHE_KEY" in env]
    problems = []
    if len(keys) != 1:
        problems.append(f"MODEL_CACHE_KEY is set {len(keys)} times, expected once")
    elif not str(keys[0]).endswith(revision):
        problems.append(f"MODEL_CACHE_KEY {keys[0]!r} does not end with MODEL_REVISION {revision}")
    steps = [step for job in jobs for step in job.get("steps") or [] if str(step.get("uses", "")).startswith("actions/cache")]
    if not steps:
        problems.append("no actions/cache step")
    for step in steps:
        key = (step.get("with") or {}).get("key")
        if key != CACHE_KEY_REFERENCE:
            problems.append(f"{step['uses'].split('@')[0]} uses key {key!r}, not {CACHE_KEY_REFERENCE}")
    return problems


def _evals_text():
    return EVALS_WORKFLOW.read_text(encoding="utf-8")


def _revision():
    return model_revision(EMBED_PY.read_text(encoding="utf-8"))


def test_the_eval_workflow_runs_for_everything_that_changes_what_the_gate_measures():
    assert evals_path_problems(_evals_text()) == []


def test_the_path_check_fails_when_only_one_event_loses_a_pattern():
    head, separator, tail = _evals_text().rpartition('      - "data/**"\n')  # the last one: pull_request's
    assert separator
    assert evals_path_problems(head + tail) == ["push and pull_request paths differ"]


def test_the_path_check_fails_when_both_events_lose_a_pattern():
    text = _evals_text()
    assert text.count('      - "db/migrations/**"\n') == 2
    assert evals_path_problems(text.replace('      - "db/migrations/**"\n', "")) == ["missing path pattern 'db/migrations/**'"]


def test_the_path_check_fails_when_an_exclusion_comes_before_the_pattern_it_carves_from():
    text = _evals_text()
    positive_then_negative = (
        '      - "engine/src/gsn/**"\n      - "!engine/src/gsn/engine/**"\n      - "!engine/src/gsn/worker/**"\n'
    )
    negative_then_positive = (
        '      - "!engine/src/gsn/engine/**"\n      - "!engine/src/gsn/worker/**"\n      - "engine/src/gsn/**"\n'
    )
    assert text.count(positive_then_negative) == 2
    assert evals_path_problems(text.replace(positive_then_negative, negative_then_positive)) == [
        "'!engine/src/gsn/engine/**' comes before 'engine/src/gsn/**', so it excludes nothing",
        "'!engine/src/gsn/worker/**' comes before 'engine/src/gsn/**', so it excludes nothing",
    ]


def test_the_path_check_fails_without_a_filter():
    text = "on:\n  push:\n  pull_request:\njobs: {}\n"
    assert evals_path_problems(text) == ["push has no paths filter", "pull_request has no paths filter"]


def test_model_revision_is_read_from_the_embedder_source_as_text():
    assert re.fullmatch(r"[0-9a-f]{40}", _revision())
    assert model_revision('MODEL_REPO = "x/y"\nMODEL_REVISION = "' + "ab" * 20 + '"\n') == "ab" * 20


def test_the_model_cache_key_ends_with_the_pinned_model_revision():
    assert cache_key_problems(_evals_text(), _revision()) == []


def test_the_cache_key_check_fails_after_a_pin_bump_without_a_key_bump():
    bumped = "0" * 40
    assert bumped != _revision()
    problems = cache_key_problems(_evals_text(), bumped)
    assert len(problems) == 1 and "does not end with MODEL_REVISION " + bumped in problems[0]


def test_the_cache_key_check_fails_when_a_cache_step_has_a_key_of_its_own():
    text = _evals_text()
    assert text.count("key: " + CACHE_KEY_REFERENCE) == 2
    problems = cache_key_problems(text.replace("key: " + CACHE_KEY_REFERENCE, "key: bge-m3-literal", 1), _revision())
    assert problems == [f"actions/cache/restore uses key 'bge-m3-literal', not {CACHE_KEY_REFERENCE}"]
