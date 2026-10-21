"""``constraints.txt`` pins the whole dependency tree so image builds are reproducible.

Regenerate it as described in docs/DEVELOPMENT.md ("Python constraints"). These checks catch drift
between ``pyproject.toml``, the file and the packages actually installed.
"""
import importlib.metadata as md
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ENGINE = Path(__file__).resolve().parents[1]
# Provided by the base interpreter image (pinned by its tag), not by the constraints: pinning pip
# itself would make pip reinstall itself whenever the base image's pip differs.
INTERPRETER_PROVIDED = {"pip", "setuptools", "wheel"}
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.!+_-]+)$")


def _constraints():
    pins = {}
    for number, raw in enumerate((ENGINE / "constraints.txt").read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.match(line)
        assert match, f"constraints.txt line {number} is not an exact 'name==version' pin: {raw!r}"
        name = canonicalize_name(match.group(1))
        assert name not in pins, f"{name} is pinned twice"
        pins[name] = match.group(2)
    return pins


def test_every_line_is_an_exact_pin_and_gsn_itself_is_not_listed():
    pins = _constraints()
    assert pins
    assert "gsn" not in pins


def test_direct_dependencies_in_pyproject_match_the_constraints():
    project = tomllib.loads((ENGINE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    direct = project["dependencies"] + project["optional-dependencies"]["dev"]
    pins = _constraints()
    for text in direct:
        req = Requirement(text)
        assert str(req.specifier).startswith("=="), f"{text} is not pinned with =="
        assert pins[canonicalize_name(req.name)] == str(req.specifier)[2:], text


def test_the_installed_dependency_tree_is_fully_pinned_to_the_installed_versions():
    pins = _constraints()
    installed = {canonicalize_name(d.metadata["Name"]): d for d in md.distributions()}
    seen, todo = set(), [("gsn", ("dev",))]
    while todo:
        name, extras = todo.pop()
        key = canonicalize_name(name)
        dist = installed[key]
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker and not any(req.marker.evaluate({"extra": e}) for e in (*extras, "")):
                continue
            child = canonicalize_name(req.name)
            if child not in seen:
                seen.add(child)
                todo.append((req.name, tuple(req.extras)))
    seen -= INTERPRETER_PROVIDED
    missing = sorted(name for name in seen if name not in pins)
    assert not missing, f"not in constraints.txt: {missing}"
    stale = sorted(name for name in seen if pins[name] != installed[name].version)
    assert not stale, f"constraints.txt differs from the installed version for: {stale}"
