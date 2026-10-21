"""Generate ``.env`` from ``.env.example`` with random secrets (standard library only).

Every value that starts with ``change-me`` is replaced by a fresh random token; comments,
blank lines and all other values are copied unchanged. The output always uses LF line
endings and is created with owner-only permissions where the OS supports them. An existing
``.env`` is never overwritten, and nothing is written if a ``change-me`` placeholder would
survive. Secret values are never printed.

Details:
* The example is read as ``utf-8-sig``, so a byte-order mark added by a Windows editor is dropped.
* A placeholder may be quoted (``A="change-me-a"`` or ``A='change-me-a'``). The replacement is
  written UNQUOTED, which is safe because the default tokens are URL-safe
  (``[A-Za-z0-9_-]``) and compose reads the bare value as-is.
* A ``change-me`` that is not at the start of a value (for example inside a URL) is not replaced;
  because that would leave a known placeholder in the secrets file, ``make_env`` raises
  ``ValueError`` instead of writing.

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


def _is_placeholder(value: str) -> bool:
    """True for ``change-me*``, optionally wrapped in one pair of matching quotes."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value.startswith(PLACEHOLDER_PREFIX)


def make_env(
    example: Path,
    target: Path,
    token: Callable[[], str] = lambda: secrets.token_urlsafe(24),
) -> None:
    """Write ``target`` from ``example``, replacing each ``change-me*`` value with ``token()``.

    Raises ``FileExistsError`` (leaving ``target`` untouched) if ``target`` already exists, and
    ``ValueError`` (creating no file) if a non-comment line would still contain ``change-me``.
    ``secrets.token_urlsafe(24)`` yields 32 characters from ``[A-Za-z0-9_-]``, so the values are
    safe in a URL, a shell word and a compose ``.env`` file without quoting.
    """
    example = Path(example)
    target = Path(target)
    if target.exists():
        raise FileExistsError(f"{target} already exists; refusing to overwrite it")

    out_lines: list[str] = []
    for line in example.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            if _is_placeholder(value):
                line = f"{key}={token()}"
        out_lines.append(line)

    leftovers = [
        number
        for number, line in enumerate(out_lines, start=1)
        if line.strip() and not line.lstrip().startswith("#") and PLACEHOLDER_PREFIX in line
    ]
    if leftovers:
        raise ValueError(
            f"{example} line(s) {leftovers} would still contain a {PLACEHOLDER_PREFIX!r} placeholder "
            "after replacement; refusing to write the env file"
        )
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
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        # messages name paths and line numbers only, never secret values
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.target} (secrets generated, not shown)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
