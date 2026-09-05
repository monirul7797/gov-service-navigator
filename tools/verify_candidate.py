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


def _validate_members(tar_path: Path, extract_to: Path) -> list[str]:
    """Reject path escapes, links and nonregular/non-directory members."""
    bad = []
    members = []
    with tarfile.open(tar_path) as tf:
        for m in tf.getmembers():
            name = m.name.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                bad.append(f"path escape: {name}")
            if m.issym() or m.islnk():
                bad.append(f"link member: {name}")
            if not (m.isfile() or m.isdir()):
                bad.append(f"nonregular member: {name}")
            members.append(m)
        if bad:
            raise SystemExit("candidate archive rejected:\n  " + "\n  ".join(bad))
        tf.extractall(path=extract_to)
    return [m.name for m in members]


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
    names = _validate_members(tar_path, extract_to)
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
