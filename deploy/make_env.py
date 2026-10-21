"""Generate ``.env`` from ``.env.example`` with random secrets (standard library only).

Every value that starts with ``change-me`` is replaced by a fresh random token; comments,
blank lines and all other values are copied unchanged. The output always uses LF line
endings and is created with owner-only permissions where the OS supports them. An existing
``.env`` is never overwritten. Secret values are never printed.

Usage (from anywhere)::

    python deploy/make_env.py
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path
from typing import Callable

PLACEHOLDER_PREFIX = "change-me"
REPO_ROOT = Path(__file__).resolve().parents[1]


def make_env(
    example: Path,
    target: Path,
    token: Callable[[], str] = lambda: secrets.token_urlsafe(24),
) -> None:
    """Write ``target`` from ``example``, replacing each ``change-me*`` value with ``token()``.

    Raises ``FileExistsError`` (leaving ``target`` untouched) if ``target`` already exists.
    ``secrets.token_urlsafe(24)`` yields 32 characters from ``[A-Za-z0-9_-]``, so the values are
    safe in a URL, a shell word and a compose ``.env`` file without quoting.
    """
    example = Path(example)
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"{target} already exists; refusing to overwrite it")

    out_lines: list[str] = []
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            if value.strip().startswith(PLACEHOLDER_PREFIX):
                line = f"{key}={token()}"
        out_lines.append(line)
    content = "\n".join(out_lines) + "\n"

    # O_EXCL makes "create only if absent" atomic; 0o600 keeps the secrets owner-only on POSIX
    # (Windows ignores the mode bits).
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create .env from .env.example with random secrets.")
    parser.add_argument("--example", type=Path, default=REPO_ROOT / ".env.example")
    parser.add_argument("--target", type=Path, default=REPO_ROOT / ".env")
    args = parser.parse_args(argv)
    try:
        make_env(args.example, args.target)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.target} (secrets generated, not shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
