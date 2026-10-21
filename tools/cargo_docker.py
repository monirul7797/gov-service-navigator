"""Run cargo inside the pinned Rust Docker image.

The Windows dev machine has no MSVC Build Tools, so Rust is compiled and tested only inside Docker.
Usage (from anywhere): ``python tools/cargo_docker.py <cargo args...>``, for example
``python tools/cargo_docker.py test`` or ``python tools/cargo_docker.py clippy --all-targets -- -D warnings``.

Only three folders of the repository are mounted, never the repository root. Crate build scripts and
proc-macros run as root with network access during ``cargo`` commands, so the container must not
see ``setenv*.bat`` (LLM keys), ``.env`` (database passwords), ``data/`` or ``.git/`` (hooks):

* ``web/`` at /work/web, read-write (sources, Cargo.lock; cargo may edit Cargo.toml/Cargo.lock);
* ``db/`` at /work/db, read-only (``sqlx::migrate!("../db/migrations")``);
* ``contracts/`` at /work/contracts, read-only (the tests read the shared JSON examples).

Binds use ``--mount type=bind`` rather than ``-v`` because ``--mount`` fails when a source is
missing, where ``-v`` silently creates an empty directory. Docker reads a ``--mount`` value as one
CSV record, so a path containing a comma or a double quote is quoted (``_csv_field``); spaces need
nothing and the whole value stays one argv element. Three named volumes keep the cargo registry,
the rustup components and the build output (``web/target``) between runs; they live in Docker's
data disk, not in the repository.

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


def _csv_field(text: str) -> str:
    """One CSV field as Docker's ``--mount`` parser expects it (quoted only when needed)."""
    if any(c in text for c in (",", '"', "\r", "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text


def _bind(source: Path, target: str, readonly: bool) -> str:
    """A ``--mount type=bind`` value. A missing source makes ``docker run`` fail (unlike ``-v``)."""
    value = f"type=bind,{_csv_field(f'src={source}')},dst={target}"
    return value + ",readonly" if readonly else value


def build_command(repo_root: Path, cargo_args: Sequence[str], image: str) -> list[str]:
    """The ``docker run`` argv; each mount is one argv element even when the path has spaces."""
    return [
        "docker", "run", "--rm",
        "--mount", _bind(repo_root / "web", "/work/web", readonly=False),
        "--mount", _bind(repo_root / "db", "/work/db", readonly=True),
        "--mount", _bind(repo_root / "contracts", "/work/contracts", readonly=True),
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
