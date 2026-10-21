"""Tests for tools/cargo_docker.py (runs cargo inside the pinned Rust Docker image).

There is no MSVC toolchain on the Windows dev machine, so every cargo command goes through this
wrapper. The tests never start Docker: the process runner is replaced by a fake.
"""
import os
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import cargo_docker  # noqa: E402
from cargo_docker import FALLBACK_DOCKER, build_command, image_from_toolchain, main  # noqa: E402

REPO_ROOT = TOOLS_DIR.parent


def _toolchain(tmp_path, channel):
    f = tmp_path / "rust-toolchain.toml"
    f.write_text(
        f'[toolchain]\nchannel = "{channel}"\ncomponents = ["rustfmt", "clippy"]\n',
        encoding="utf-8",
    )
    return f


def test_image_name_comes_from_toolchain_channel(tmp_path):
    assert image_from_toolchain(_toolchain(tmp_path, "1.99.1")) == "rust:1.99.1-bookworm"
    assert image_from_toolchain(_toolchain(tmp_path, "1.99.2")) == "rust:1.99.2-bookworm"


def test_committed_toolchain_pins_an_exact_version():
    image = image_from_toolchain(REPO_ROOT / "web" / "rust-toolchain.toml")
    assert image.startswith("rust:1.99.") and image.endswith("-bookworm")


def test_build_command_for_test_has_exact_argv():
    root = Path("/some/repo")
    assert build_command(root, ["test"], "rust:1.99.1-bookworm") == [
        "docker", "run", "--rm",
        "-v", f"{root}:/work",
        "-v", "gsn-cargo-registry:/usr/local/cargo/registry",
        "-v", "gsn-rustup:/usr/local/rustup",
        "-v", "gsn-cargo-target:/work/web/target",
        "-w", "/work/web",
        "rust:1.99.1-bookworm",
        "cargo", "test",
    ]


def test_cargo_args_are_passed_through_verbatim_and_last():
    cmd = build_command(Path("/r"), ["clippy", "--all-targets", "--", "-D", "warnings"], "img:1")
    assert cmd[-6:] == ["cargo", "clippy", "--all-targets", "--", "-D", "warnings"]
    assert cmd[cmd.index("img:1") + 1] == "cargo"


def test_repo_path_with_spaces_stays_one_argv_element():
    root = Path("C:/Users/Some One/Structured Data/NID/repo")
    cmd = build_command(root, ["test"], "rust:1.99.1-bookworm")
    mount = f"{root}:/work"
    assert " " in mount
    assert cmd.count(mount) == 1
    assert cmd[cmd.index(mount) - 1] == "-v"


def test_exit_code_is_passed_through_from_the_runner():
    calls = []

    def fake_runner(argv, env=None):
        calls.append(argv)
        return 3

    assert main(["test"], runner=fake_runner, which=lambda name: "/usr/bin/docker") == 3
    assert calls[0][0] == "/usr/bin/docker"
    assert calls[0][-2:] == ["cargo", "test"]
    assert main(["test"], runner=lambda argv, env=None: 0, which=lambda name: "/usr/bin/docker") == 0


def test_falls_back_to_docker_desktop_install_when_not_on_path(monkeypatch):
    monkeypatch.setattr(cargo_docker.os.path, "isfile", lambda p: p == FALLBACK_DOCKER)
    seen = {}

    def fake_runner(argv, env=None):
        seen["argv"] = argv
        seen["env"] = env
        return 0

    assert main(["fmt", "--check"], runner=fake_runner, which=lambda name: None) == 0
    assert seen["argv"][0] == FALLBACK_DOCKER
    # docker-credential-desktop lives next to docker.exe, so its folder must be on PATH too.
    assert str(Path(FALLBACK_DOCKER).parent) in seen["env"]["PATH"].split(os.pathsep)


def test_clear_message_and_nonzero_exit_when_docker_is_missing(monkeypatch, capsys):
    monkeypatch.setattr(cargo_docker.os.path, "isfile", lambda p: False)

    def never_called(argv, env=None):
        raise AssertionError("runner must not be called without docker")

    code = main(["test"], runner=never_called, which=lambda name: None)
    assert code != 0
    err = capsys.readouterr().err
    assert "docker" in err.lower()
    assert FALLBACK_DOCKER in err
