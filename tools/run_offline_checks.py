"""
tools/run_offline_checks.py — offline deterministic check harness (P00).

Fixed test interface from the control guide:

    python -B tools/run_offline_checks.py --legacy --pattern 'test_p00_*.py'   # P00
    python -B tools/run_offline_checks.py --legacy --pattern 'test_p*.py'      # P01+

Behavior:
  * installs a strict network guard (in each child process) that blocks and
    RECORDS socket connections, DNS lookups and high-level HTTP entry points;
    the recorded attempts fail the whole run even when production catches the
    raised exception;
  * the guard's exception derives from RuntimeError, NOT OSError, so generic
    `except OSError` recovery in production cannot swallow it silently;
  * --legacy runs test_freshness.py, test_freshness_v21.py, test_acquisition.py
    and test_multiturn.py as isolated child processes (runpy, run_name
    '__main__'), preserving each script's SystemExit result;
  * --pattern selects unittest discovery under tests/maintenance;
  * prints executed counts, failures, skips and the attempted-network count;
    exits nonzero on any failure or any attempted network;
  * --probe-network is an internal test mode that deliberately attempts
    socket.create_connection(('gov.example', 443)) — it must exit nonzero
    BEFORE network access with a NETWORK_ATTEMPT diagnostic.

One freshness/maintenance process at a time; no lock, per the V2 design note.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = Path(__file__).resolve().parent

GUARD_ENV = "OFFLINE_GUARD_LOG"

LEGACY_SCRIPTS = ["test_freshness.py", "test_freshness_v21.py",
                  "test_acquisition.py", "test_multiturn.py"]

GUARD_BOOTSTRAP = r"""
import sys, os
script = sys.argv[1]
sys.argv = [script]
repo = os.path.dirname(os.path.abspath(script))
tools = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else r'{tools}'
sys.path.insert(0, r'{tools}')
sys.path.insert(0, repo)
sys.path.insert(0, r'{repo_root}')
from run_offline_checks import install_guard
install_guard(os.environ['{env}'])
import runpy
runpy.run_path(script, run_name='__main__')
"""


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------

class OfflineGuardViolation(RuntimeError):
    """Raised on any attempted network operation.

    Deliberately NOT an OSError (and not a urllib.error.URLError, which is an
    OSError subclass): production code that recovers from OS-level network
    failures must not silently swallow a guard violation.
    """


_ATTEMPTS: list = []


def _record(kind: str, detail: str):
    _ATTEMPTS.append({"kind": kind, "detail": detail})
    line = json.dumps({"kind": kind, "detail": detail})
    log_path = os.environ.get(GUARD_ENV)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    # the attempted-network diagnostic must survive even an unhandled raise
    print(f"NETWORK_ATTEMPT: {kind} {detail}", file=sys.stderr, flush=True)


def install_guard(log_path: str | None = None) -> None:
    """Block all outbound network primitives. Idempotent."""
    import socket

    if log_path:
        os.environ[GUARD_ENV] = str(log_path)

    if getattr(socket, "_offline_guard_installed", False):
        return

    real_create_connection = socket.create_connection
    real_getaddrinfo = socket.getaddrinfo
    real_socket_init = socket.socket.__init__
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def guarded_create_connection(address, *args, **kwargs):
        _record("socket.create_connection", repr(address))
        raise OfflineGuardViolation(f"network attempt blocked: socket.create_connection({address!r})")

    def guarded_getaddrinfo(host, *args, **kwargs):
        _record("socket.getaddrinfo", repr(host))
        raise OfflineGuardViolation(f"network attempt blocked: socket.getaddrinfo({host!r})")

    def guarded_connect(self, address):
        _record("socket.connect", repr(address))
        raise OfflineGuardViolation(f"network attempt blocked: socket.connect({address!r})")

    def guarded_connect_ex(self, address):
        _record("socket.connect_ex", repr(address))
        raise OfflineGuardViolation(f"network attempt blocked: socket.connect_ex({address!r})")

    socket.create_connection = guarded_create_connection
    socket.getaddrinfo = guarded_getaddrinfo

    # C2: direct resolver entry points — guarded so the underlying resolver
    # (real or sentinel) is never reached
    real_gethostbyname = socket.gethostbyname
    real_gethostbyname_ex = socket.gethostbyname_ex
    real_gethostbyaddr = socket.gethostbyaddr

    def guarded_gethostbyname(host):
        _record("socket.gethostbyname", repr(host))
        raise OfflineGuardViolation(f"network attempt blocked: socket.gethostbyname({host!r})")

    def guarded_gethostbyname_ex(name):
        _record("socket.gethostbyname_ex", repr(name))
        raise OfflineGuardViolation(f"network attempt blocked: socket.gethostbyname_ex({name!r})")

    def guarded_gethostbyaddr(addr):
        _record("socket.gethostbyaddr", repr(addr))
        raise OfflineGuardViolation(f"network attempt blocked: socket.gethostbyaddr({addr!r})")

    socket.gethostbyname = guarded_gethostbyname
    socket.gethostbyname_ex = guarded_gethostbyname_ex
    socket.gethostbyaddr = guarded_gethostbyaddr

    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    socket._offline_guard_installed = True

    # high-level HTTP entry points (requests may not be needed by every suite
    # but must also be blocked when present)
    try:
        import urllib.request
        real_urlopen = urllib.request.urlopen

        def guarded_urlopen(fullurl, *args, **kwargs):
            _record("urllib.request.urlopen", repr(fullurl))
            raise OfflineGuardViolation(f"network attempt blocked: urlopen({fullurl!r})")

        urllib.request.urlopen = guarded_urlopen
    except Exception:
        pass

    try:
        import requests.sessions
        real_session_request = requests.sessions.Session.request

        def guarded_session_request(self, method, url, *args, **kwargs):
            _record("requests.Session.request", f"{method} {url}")
            raise OfflineGuardViolation(
                f"network attempt blocked: requests {method} {url!r}")

        requests.sessions.Session.request = guarded_session_request
    except Exception:
        pass


# ---------------------------------------------------------------------------
# internal modes
# ---------------------------------------------------------------------------

def probe_network() -> int:
    """Deliberate violation: must fail closed BEFORE any network contact.

    The probe isolates its own guard log: a probe attempt is demonstrated via
    the NETWORK_ATTEMPT stderr diagnostic and the nonzero exit, and must not
    pollute a parent harness attempt count."""
    os.environ.pop(GUARD_ENV, None)
    install_guard()
    import socket
    try:
        socket.create_connection(("gov.example", 443), timeout=3)
    except OfflineGuardViolation as exc:
        print(f"probe correctly failed closed: {exc}")
        return 1   # nonzero: this mode demonstrates the guard failing closed
    print("probe unexpectedly completed a connection — the guard did not fire",
          file=sys.stderr)
    return 2


def validate_pattern(pattern: str | None) -> str | None:
    """C1: blank/whitespace patterns are rejected before any run starts.
    Returns an error message, or None when the pattern is usable."""
    if pattern is None or not pattern.strip():
        return (f"INVALID_PATTERN: --pattern {pattern!r} is empty or whitespace; "
                "a packet test pattern must name real tests")
    return None


def run_discovery_child(pattern: str) -> int:
    """Internal mode: guard installed, then unittest discovery.
    C1: a valid pattern matching zero tests fails closed with the stable
    NO_TESTS_DISCOVERED diagnostic (exit 3)."""
    install_guard(os.environ.get(GUARD_ENV))
    import unittest
    loader = unittest.defaultTestLoader
    suite = loader.discover(start_dir=str(REPO_ROOT / "tests" / "maintenance"),
                            pattern=pattern, top_level_dir=str(REPO_ROOT))
    discovered = suite.countTestCases()
    if discovered == 0:
        print(f"NO_TESTS_DISCOVERED: pattern {pattern!r} matched 0 tests under "
              f"tests/maintenance", file=sys.stderr, flush=True)
        return 3
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


# ---------------------------------------------------------------------------
# parent orchestration
# ---------------------------------------------------------------------------

def _spawn_guarded(cmd: list, log_path: Path) -> tuple[int, str]:
    env = os.environ.copy()
    env[GUARD_ENV] = str(log_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          cwd=str(REPO_ROOT), env=env, timeout=1800)
    return proc.returncode, proc.stdout + proc.stderr


def _parse_unittest_summary(output: str) -> tuple[int, int, int, int]:
    """Extract (ran, failures, errors, skips) from a unittest summary line."""
    import re
    ran = fails = errs = skips = 0
    m = re.search(r"Ran (\d+) tests?", output)
    if m:
        ran = int(m.group(1))
    fails = sum(int(x) for x in re.findall(r"failures?[=:]\s*(\d+)", output))
    errs = sum(int(x) for x in re.findall(r"errors?[=:]\s*(\d+)", output))
    skips = sum(int(x) for x in re.findall(r"skipped?s?[=:]\s*(\d+)", output))
    return ran, fails, errs, skips


def _guard_log_attempts(log_path: Path) -> list:
    if not log_path.exists():
        return []
    attempts = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                attempts.append(json.loads(line))
    return attempts


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline deterministic check harness")
    ap.add_argument("--legacy", action="store_true",
                    help="also run the four legacy suites as guarded children")
    ap.add_argument("--pattern", default="test_*.py",
                    help="unittest discovery pattern under tests/maintenance")
    ap.add_argument("--probe-network", action="store_true",
                    help="internal test mode: deliberately attempt a connection; "
                         "must exit nonzero before network access")
    args = ap.parse_args()

    pattern_error = validate_pattern(args.pattern)
    if pattern_error:
        print(pattern_error, file=sys.stderr, flush=True)
        return 2

    if args.probe_network:
        return probe_network()

    import tempfile
    scratch = Path(tempfile.mkdtemp(prefix="offline_checks_"))
    total_attempts: list = []
    results = []
    exit_ok = True

    if args.legacy:
        bootstrap = GUARD_BOOTSTRAP.replace("{tools}", str(TOOLS_DIR)) \
                                   .replace("{repo_root}", str(REPO_ROOT)) \
                                   .replace("{env}", GUARD_ENV)
        for script in LEGACY_SCRIPTS:
            script_path = REPO_ROOT / script
            if not script_path.exists():
                results.append({"suite": script, "exit": None,
                                "note": "missing script"})
                exit_ok = False
                continue
            log_path = scratch / f"guard_{script}.jsonl"
            cmd = [sys.executable, "-B", "-c", bootstrap, str(script_path)]
            code, output = _spawn_guarded(cmd, log_path)
            ran, fails, errs, skips = _parse_unittest_summary(output)
            import re as _re
            self_reported = [int(x) for x in _re.findall(r"\((\d+) checks?\)", output)]
            attempts = _guard_log_attempts(log_path)
            total_attempts.extend(attempts)
            ok = (code == 0) and not attempts
            exit_ok = exit_ok and ok
            results.append({
                "suite": script, "exit": code, "ran": ran, "failures": fails,
                "errors": errs, "skips": skips, "network_attempts": len(attempts),
                "self_reported_checks": self_reported, "ok": ok,
                "tail": output.strip().splitlines()[-3:] if not ok else [],
            })

    if args.pattern:
        log_path = scratch / "guard_discovery.jsonl"
        cmd = [sys.executable, "-B", str(Path(__file__).resolve()),
               "--discover-child", "--pattern", args.pattern]
        code, output = _spawn_guarded(cmd, log_path)
        ran, fails, errs, skips = _parse_unittest_summary(output)
        attempts = _guard_log_attempts(log_path)
        total_attempts.extend(attempts)
        # C1: a discovery child must have discovered AND run tests — an empty
        # run (exit 3 / NO_TESTS_DISCOVERED) or a zero-count success is failure
        ok = (code == 0) and ran > 0 and not attempts
        exit_ok = exit_ok and ok
        results.append({
            "suite": f"tests/maintenance:{args.pattern}", "exit": code, "ran": ran,
            "failures": fails, "errors": errs, "skips": skips,
            "network_attempts": len(attempts), "ok": ok,
            "tail": output.strip().splitlines()[-6:] if not ok else [],
        })

    print("=" * 72)
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    ran_total = sum(r.get("ran", 0) or 0 for r in results)
    print(f"TOTALS: ran={ran_total} "
          f"failures={sum(r.get('failures', 0) or 0 for r in results)} "
          f"errors={sum(r.get('errors', 0) or 0 for r in results)} "
          f"skips={sum(r.get('skips', 0) or 0 for r in results)} "
          f"attempted_network={len(total_attempts)}")
    if total_attempts:
        print("ATTEMPTED NETWORK DETECTED — the offline run fails:", file=sys.stderr)
        for a in total_attempts:
            print(f"  NETWORK_ATTEMPT: {a['kind']} {a['detail']}", file=sys.stderr)
    return 0 if exit_ok and not total_attempts else 1


if __name__ == "__main__":
    if "--discover-child" in sys.argv:
        idx = sys.argv.index("--pattern")
        sys.exit(run_discovery_child(sys.argv[idx + 1]))
    sys.exit(main())
