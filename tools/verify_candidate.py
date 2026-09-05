"""
tools/verify_candidate.py — exported-commit verification (P00, control guide).

Algorithm contract:
  1. Resolve the repository root from this script; resolve HEAD; record git
     status.
  2. Create a fresh TemporaryDirectory OUTSIDE the repository.
  3. git archive --format=tar --output=<scratch>/candidate.tar HEAD
  4. Inspect archive member paths; reject escapes, links and nonregular
     members other than directories; extract under <scratch>/candidate.
     (A git archive contains only TRACKED files, so untracked working files
     are excluded by construction.)
  5. Invoke the candidate's tools/run_offline_checks.py with sys.executable
     and the same --legacy/--pattern arguments.
  6. Return the candidate run's exit code; print HEAD, interpreter,
     arguments and the scratch result path.
  7. Never copy untracked files, never install dependencies or download
     anything to make an export pass.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def git(*args: str, cwd: Path | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd or REPO_ROOT),
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _member_errors(member: tarfile.TarInfo, extract_root: Path) -> list[str]:
    """C3: per-member safety checks with explicit Windows path semantics.

    Rejects, before any extraction: drive-absolute and drive-relative names,
    UNC paths, rooted names, backslash forms (normalized to slashes first),
    alternate-data-stream / colon-bearing names, parent traversal that
    resolves outside the extraction root, symlinks/hardlinks and nonregular
    members. Containment is decided on RESOLVED destinations, never by
    string-prefix comparison.
    """
    errors: list[str] = []
    raw = member.name
    name = raw.replace("\\", "/")          # normalize Windows separators first

    if ":" in name:
        # a colon can select a drive ("C:/x", "C:x") or an NTFS alternate
        # data stream ("file.txt:stream") on Windows — always rejected
        errors.append(f"colon-bearing name (drive/ADS risk): {raw!r}")
        return errors

    if name.startswith("//") or "\\" in raw:
        errors.append(f"UNC/rooted path: {raw!r}")
        return errors
    if name.startswith("/"):
        errors.append(f"rooted path: {raw!r}")
        return errors

    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts:
        errors.append(f"empty member name: {raw!r}")
        return errors

    if member.issym() or member.islnk():
        errors.append(f"link member: {raw!r}")
    if not (member.isfile() or member.isdir()):
        errors.append(f"nonregular member ({member.type!r}): {raw!r}")

    # resolved-destination containment (authoritative for '..' and any other
    # normalization surprise) — computed from the normalized join
    dest = extract_root
    for part in parts:
        dest = dest / part
    root_resolved = extract_root.resolve()
    try:
        dest_resolved = dest.resolve()
    except OSError as exc:
        errors.append(f"cannot resolve destination for {raw!r}: {exc}")
        return errors
    if os.path.commonpath([str(dest_resolved), str(root_resolved)]) != str(root_resolved):
        errors.append(f"resolves outside the extraction root: {raw!r} -> {dest_resolved}")
    return errors


def _plan_extraction(tar_path: Path, extract_root: Path) -> list[str]:
    """Validate ALL members before any extraction; raise SystemExit listing
    every defect if any member is unsafe. Returns the member names on success
    (the caller then performs the single extraction)."""
    errors: list[str] = []
    names: list[str] = []
    with tarfile.open(tar_path) as tf:
        for m in tf.getmembers():
            names.append(m.name)
            errors.extend(_member_errors(m, extract_root))
    if errors:
        raise SystemExit("candidate archive rejected:\n  " + "\n  ".join(errors))
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the exported HEAD candidate offline")
    ap.add_argument("--legacy", action="store_true")
    ap.add_argument("--pattern", default="test_*.py")
    args = ap.parse_args()

    head = git("rev-parse", "HEAD")
    status = git("status", "--short")
    print(f"HEAD        : {head}")
    print(f"interpreter : {sys.executable}")
    print(f"arguments   : --legacy={args.legacy} --pattern={args.pattern!r}")
    if status:
        print("git status  :")
        for line in status.splitlines():
            print(f"  {line}")

    scratch = Path(tempfile.mkdtemp(prefix="candidate_verify_"))
    tar_path = scratch / "candidate.tar"
    extract_to = scratch / "candidate"
    extract_to.mkdir(parents=True, exist_ok=True)
    print(f"scratch     : {scratch}")

    git("archive", "--format=tar", f"--output={tar_path}", "HEAD")
    names = _plan_extraction(tar_path, extract_to)
    with tarfile.open(tar_path) as tf:
        tf.extractall(path=extract_to)
    print(f"archive     : {len(names)} tracked files extracted (untracked excluded)")

    candidate_runner = extract_to / "tools" / "run_offline_checks.py"
    if not candidate_runner.exists():
        print("FATAL: candidate is missing tools/run_offline_checks.py", file=sys.stderr)
        return 2

    cmd = [sys.executable, "-B", str(candidate_runner)]
    if args.legacy:
        cmd.append("--legacy")
    cmd += ["--pattern", args.pattern]
    print("candidate command:", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(extract_to), timeout=3600)
    tail = proc.stdout.strip().splitlines()[-8:]
    print("-" * 72)
    for line in tail:
        print(line)
    print("-" * 72)
    print(f"candidate exit code: {proc.returncode}")
    if proc.returncode != 0:
        err_tail = proc.stderr.strip().splitlines()[-10:]
        for line in err_tail:
            print(f"  stderr: {line}")
    print(f"scratch preserved for audit: {scratch}")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
