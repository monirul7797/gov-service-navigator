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


# --- the ML tree (engine/constraints-ml.txt, the `ml` extra, installed only in the release image) ---------

BASELINE = ENGINE.parent / "docs" / "reports" / "m1-parity" / "chroma_baseline.json"


def _read_pins(path, label):
    """The ``name -> version`` pins of one constraints file, as written (the order is checked separately)."""
    pins, order = {}, []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = PIN.match(line)
        assert match, f"{label} line {number} is not an exact 'name==version' pin: {raw!r}"
        name = canonicalize_name(match.group(1))
        assert name not in pins, f"{name} is pinned twice in {label}"
        pins[name] = match.group(2)
        order.append(line)
    return pins, order


def _ml_constraints():
    return _read_pins(ENGINE / "constraints-ml.txt", "constraints-ml.txt")


def _ml_extra():
    project = tomllib.loads((ENGINE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return project["optional-dependencies"]["ml"]


def test_ml_constraints_are_exact_pins_written_in_ascii_and_sorted_without_regard_to_case():
    raw = (ENGINE / "constraints-ml.txt").read_bytes()
    raw.decode("ascii")  # a UTF-16 file (PowerShell's `>`) or a BOM would fail here
    pins, order = _ml_constraints()
    assert pins
    assert "gsn" not in pins
    assert order == sorted(order, key=str.lower)
    assert (ENGINE / "constraints-ml.txt").read_text(encoding="ascii").startswith("#"), "keep the comment header"


def test_the_ml_extra_pins_match_constraints_ml():
    pins, _ = _ml_constraints()
    extra = _ml_extra()
    assert {canonicalize_name(Requirement(text).name) for text in extra} >= {"torch", "sentence-transformers"}
    for text in extra:
        req = Requirement(text)
        specifier = str(req.specifier)
        assert specifier.startswith("=="), f"{text} is not pinned with =="
        assert pins[canonicalize_name(req.name)] == specifier[2:], text


def test_no_package_is_pinned_in_both_constraints_files_with_different_versions():
    """The ML file lists what the extra needs beyond the *runtime* image, which can include packages the
    dev tree also needs (``packaging``, ``filelock``, ...); those must carry the same version in both."""
    core, _ = _read_pins(ENGINE / "constraints.txt", "constraints.txt")
    ml, _ = _ml_constraints()
    differing = sorted(f"{name}: {core[name]} vs {ml[name]}" for name in core.keys() & ml.keys() if core[name] != ml[name])
    assert not differing, differing


def test_the_ml_pins_are_the_versions_that_made_the_chromadb_baseline():
    """Vectors from the release image are compared with the baseline's, so the libraries must be the same.

    The baseline records its environment; torch carries the ``+cpu`` label here (the PyTorch CPU index).
    A deliberate change belongs to the M2 engine switch and re-runs parity.
    """
    import json

    environment = json.loads(BASELINE.read_text(encoding="utf-8"))["environment"]
    pins, _ = _ml_constraints()
    for name in ("torch", "sentence-transformers", "transformers", "tokenizers", "numpy"):
        assert pins[name].split("+")[0] == environment[name], name
    assert pins["torch"] == environment["torch"] + "+cpu"


def test_the_release_image_installs_the_ml_pins_without_resolving_anything():
    dockerfile = (ENGINE / "Dockerfile").read_text(encoding="utf-8")
    block = re.search(r"^FROM runtime AS release\b(.*?)(?=^FROM |\Z)", dockerfile, re.M | re.S).group(1)
    assert "--no-deps" in block and "/build/constraints-ml.txt" in block
    assert "https://download.pytorch.org/whl/cpu" in block
    assert "pip check" in block


def test_the_ml_file_does_not_repeat_the_engines_runtime_dependencies():
    project = tomllib.loads((ENGINE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    runtime = {canonicalize_name(Requirement(text).name) for text in project["dependencies"]}
    pins, _ = _ml_constraints()
    assert not sorted(runtime & pins.keys())
