"""Run cargo inside the pinned Rust Docker image.

The Windows dev machine has no MSVC Build Tools, so Rust is compiled and tested only inside Docker.
Usage (from anywhere): ``python tools/cargo_docker.py <cargo args...>``, for example
``python tools/cargo_docker.py test`` or ``python tools/cargo_docker.py clippy --all-targets -- -D warnings``.

The whole repository is mounted at /work because ``web/`` reads ``../db/migrations`` and
``../contracts``. Three named volumes keep the cargo registry, the rustup components and the build
output (``web/target``) between runs; they live in Docker's data disk, not in the repository.

Stdlib only; the exit code is cargo's exit code.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Callable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLCHAIN_FILE = REPO_ROOT / "web" / "rust-toolchain.toml"
FALLBACK_DOCKER = r"G:\Docker\DockerDesktop\resources\bin\docker.exe"


def image_from_toolchain(toolchain_file: Path) -> str:
    """The Docker image that matches ``channel`` in a rust-toolchain.toml."""
    with open(toolchain_file, "rb") as fh:
        channel = tomllib.load(fh)["toolchain"]["channel"]
    return f"rust:{channel}-bookworm"


def build_command(repo_root: Path, cargo_args: Sequence[str], image: str) -> list[str]:
    """The ``docker run`` argv; the repo path is one element even when it contains spaces."""
    return [
        "docker", "run", "--rm",
        "-v", f"{repo_root}:/work",
        "-v", "gsn-cargo-registry:/usr/local/cargo/registry",
        "-v", "gsn-rustup:/usr/local/rustup",
        "-v", "gsn-cargo-target:/work/web/target",
        "-w", "/work/web",
        image,
        "cargo", *cargo_args,
    ]


def _run(argv: Sequence[str], env: Optional[dict] = None) -> int:
    return subprocess.call(list(argv), env=env)


def main(
    argv: Optional[Sequence[str]] = None,
    runner: Callable[..., int] = _run,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> int:
    cargo_args = list(sys.argv[1:] if argv is None else argv)
    docker = which("docker")
    env = None
    if docker is None:
        if not os.path.isfile(FALLBACK_DOCKER):
            print(
                "error: 'docker' was not found on PATH and "
                f"{FALLBACK_DOCKER} does not exist. Start Docker Desktop and put its bin folder "
                "on PATH, then retry.",
                file=sys.stderr,
            )
            return 127
        docker = FALLBACK_DOCKER
        # docker-credential-desktop sits next to docker.exe and must be reachable for pulls.
        env = dict(os.environ)
        env["PATH"] = str(Path(FALLBACK_DOCKER).parent) + os.pathsep + env.get("PATH", "")
    cmd = build_command(REPO_ROOT, cargo_args, image_from_toolchain(TOOLCHAIN_FILE))
    cmd[0] = docker
    return runner(cmd, env=env)


if __name__ == "__main__":
    sys.exit(main())
