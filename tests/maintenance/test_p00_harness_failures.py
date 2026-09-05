"""
tests/maintenance/test_p00_harness_failures.py — P00-C1 regressions.

Covers the three Codex findings against the P00 harness tools:
  C1  empty/nonmatching discovery patterns must fail closed (NO_TESTS_DISCOVERED)
  C2  direct DNS resolver entry points must be guarded before the real resolver
  C3  Windows archive path escapes must be rejected before any extraction

All negative subprocesses run with their OWN guard logs and nonmatching
patterns so they cannot recurse into the enclosing suite or pollute the
outer run's attempt count.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))
RUNNER = REPO_ROOT / "tools" / "run_offline_checks.py"
VERIFIER = REPO_ROOT / "tools" / "verify_candidate.py"

NONMATCHING = "test_p00_typo_no_match_*.py"


def _isolated_env(log_name: str) -> dict:
    """Fresh environment for a deliberate negative subprocess: its own guard
    log, never the enclosing run's, with bytecode/UTF-8 fixed."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["OFFLINE_GUARD_LOG"] = str(Path(tempfile.mkdtemp(prefix="p00c1_")) / log_name)
    return env


class TestC1EmptyDiscoveryFailsClosed(unittest.TestCase):
    """C1: a pattern matching zero tests, or a blank pattern, must produce a
    nonzero exit with a stable diagnostic — legacy passes cannot mask it."""

    def test_nonmatching_pattern_returns_nonzero_with_diagnostic(self):
        result = subprocess.run(
            [sys.executable, "-B", str(RUNNER), "--pattern", NONMATCHING],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            env=_isolated_env("c1_nonmatch.jsonl"), timeout=600)
        self.assertNotEqual(result.returncode, 0,
                            f"nonmatching pattern exited 0:\n{result.stdout}")
        self.assertIn("NO_TESTS_DISCOVERED", result.stdout + result.stderr)

    def test_nonmatching_pattern_with_legacy_cannot_mask(self):
        result = subprocess.run(
            [sys.executable, "-B", str(RUNNER), "--legacy", "--pattern", NONMATCHING],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            env=_isolated_env("c1_nonmatch_legacy.jsonl"), timeout=1800)
        self.assertNotEqual(result.returncode, 0,
                            "passing legacy suites masked a nonmatching pattern")
        combined = result.stdout + result.stderr
        self.assertIn("NO_TESTS_DISCOVERED", combined)
        # the legacy suites really ran and passed inside that child
        self.assertIn('"self_reported_checks": [60]', combined)
        self.assertIn('"self_reported_checks": [31]', combined)
        self.assertIn('"self_reported_checks": [63]', combined)

    def test_blank_pattern_rejected_at_validation(self):
        for blank in ("", "   "):
            result = subprocess.run(
                [sys.executable, "-B", str(RUNNER), "--pattern", blank],
                capture_output=True, text=True, cwd=str(REPO_ROOT),
                env=_isolated_env("c1_blank.jsonl"), timeout=300)
            self.assertNotEqual(result.returncode, 0,
                                f"blank pattern {blank!r} exited 0")
            self.assertIn("INVALID_PATTERN", result.stdout + result.stderr)

    def test_normal_pattern_still_runs_all_p00_tests(self):
        # Recursion guard: the child runs the full P00 pattern (which includes
        # this file); inside that child this single test skips itself so the
        # subprocess tree terminates. The skip is visible in the child's own
        # unittest counts — nothing is hidden from the reported totals.
        if os.environ.get("P00_NORMAL_PATTERN_CHILD") == "1":
            self.skipTest("child of the normal-pattern run (recursion guard)")
        env = _isolated_env("c1_normal.jsonl")
        env["P00_NORMAL_PATTERN_CHILD"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", str(RUNNER), "--pattern", "test_p00_*.py"],
            capture_output=True, text=True, cwd=str(REPO_ROOT),
            env=env, timeout=1800)
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])
        self.assertNotIn("NO_TESTS_DISCOVERED", result.stdout)
        m = [line for line in result.stdout.splitlines()
             if "tests/maintenance:test_p00_*.py" in line]
        self.assertTrue(m)
        entry = json.loads(m[0])
        # baseline(9) + harness_failures(13, minus this skipped test)
        self.assertGreaterEqual(entry["ran"], 9 + 12)
        self.assertLessEqual(entry["skips"], 1)


class TestC2DirectResolverGuard(unittest.TestCase):
    """C2: gethostbyname / gethostbyname_ex / gethostbyaddr are guarded before
    the underlying resolver; a caught violation still fails the parent run."""

    def _resolver_probe_script(self, resolver: str) -> str:
        return (
            "import socket, sys\n"
            "calls = []\n"
            "def sentinel(name):\n"
            "    calls.append(name)\n"
            "    return '203.0.113.1'\n"
            f"socket.{resolver} = lambda *a, **k: sentinel('{resolver}')\n"
            "sys.path.insert(0, r'%s')\n"
            "sys.path.insert(0, r'%s')\n"
            "from run_offline_checks import install_guard, OfflineGuardViolation\n"
            "install_guard()\n"
            "try:\n"
            f"    socket.{resolver}('gov.example')\n"
            "except OfflineGuardViolation:\n"
            "    print('GUARD_VIOLATION_RAISED')\n"
            "    print('SENTINEL_CALLS=' + str(len(calls)))\n"
            "else:\n"
            "    print('GUARD_DID_NOT_FIRE')\n"
        ) % (str(REPO_ROOT / "tools"), str(REPO_ROOT))

    def _run_probe(self, resolver: str):
        script = self._resolver_probe_script(resolver)
        return subprocess.run([sys.executable, "-B", "-c", script],
                              capture_output=True, text=True,
                              cwd=str(REPO_ROOT),
                              env=_isolated_env(f"c2_{resolver}.jsonl"),
                              timeout=300)

    def test_gethostbyname_guarded_before_underlying_resolver(self):
        result = self._run_probe("gethostbyname")
        self.assertIn("GUARD_VIOLATION_RAISED", result.stdout, result.stdout + result.stderr)
        self.assertIn("SENTINEL_CALLS=0", result.stdout)
        self.assertIn("NETWORK_ATTEMPT", result.stderr)

    def test_gethostbyname_ex_guarded(self):
        result = self._run_probe("gethostbyname_ex")
        self.assertIn("GUARD_VIOLATION_RAISED", result.stdout, result.stdout + result.stderr)
        self.assertIn("SENTINEL_CALLS=0", result.stdout)
        self.assertIn("NETWORK_ATTEMPT", result.stderr)

    def test_gethostbyaddr_guarded(self):
        result = self._run_probe("gethostbyaddr")
        self.assertIn("GUARD_VIOLATION_RAISED", result.stdout, result.stdout + result.stderr)
        self.assertIn("SENTINEL_CALLS=0", result.stdout)
        self.assertIn("NETWORK_ATTEMPT", result.stderr)

    # P00-C2 fixture: a COMPLETE temporary repository — all four legacy-named
    # scripts, a genuine passing unittest under tests/maintenance selected by
    # a fixture-only pattern, and the copied production runner. With this
    # fixture the parent's exit status can only come from network-attempt
    # enforcement, never from missing scripts or empty discovery.
    _AGG_PATTERN = "test_aggregation_fixture.py"

    def _build_aggregation_repo(self, base: Path, *, runner_source: str,
                                attempt_env_name: str = "P00C2_ATTEMPT") -> Path:
        repo = base / "repo"
        (repo / "tools").mkdir(parents=True)
        (repo / "tests" / "maintenance").mkdir(parents=True)
        (repo / "tests" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "tests" / "maintenance" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "tools" / "run_offline_checks.py").write_text(runner_source, encoding="utf-8")
        (repo / "tests" / "maintenance" / self._AGG_PATTERN).write_text(
            "import unittest\n"
            "class TestAggregationFixture(unittest.TestCase):\n"
            "    def test_passes(self):\n"
            "        self.assertTrue(True)\n", encoding="utf-8")
        # three simple successful legacy fixtures; test_freshness.py is the
        # one that conditionally attempts a guarded connection and catches it
        simple = (
            "import sys\n"
            "print('fixture legacy suite: pass')\n"
            "sys.exit(0)\n"
        )
        bodies = {
            "test_freshness.py": (
                "import os, socket, sys\n"
                f"if os.environ.get({attempt_env_name!r}) == '1':\n"
                "    try:\n"
                "        socket.create_connection(('gov.example', 443), timeout=3)\n"
                "    except Exception:\n"
                "        pass  # the guard violation is deliberately caught\n"
                "    print('fixture: attempted and caught')\n"
                "print('fixture legacy suite: pass')\n"
                "sys.exit(0)\n"),
            "test_freshness_v21.py": simple,
            "test_acquisition.py": simple,
            "test_multiturn.py": simple,
        }
        for name, body in bodies.items():
            (repo / name).write_text(body, encoding="utf-8")
        return repo

    def _run_aggregation(self, repo: Path, *, attempt: bool, log_name: str):
        env = _isolated_env(log_name)
        if attempt:
            env["P00C2_ATTEMPT"] = "1"
        return subprocess.run(
            [sys.executable, "-B", str(repo / "tools" / "run_offline_checks.py"),
             "--legacy", "--pattern", self._AGG_PATTERN],
            capture_output=True, text=True, cwd=str(repo), env=env, timeout=900)

    @staticmethod
    def _rows(output: str) -> dict:
        rows = {}
        for line in output.splitlines():
            line = line.strip()
            if line.startswith('{"suite":'):
                row = json.loads(line)
                rows[row["suite"]] = row
        return rows

    def test_positive_control_all_children_pass(self):
        """Positive control over the complete fixture: everything green, zero
        attempts, no missing-script or NO_TESTS_DISCOVERED diagnostics."""
        repo = self._build_aggregation_repo(
            Path(tempfile.mkdtemp(prefix="p00c2_pos_")),
            runner_source=(REPO_ROOT / "tools" / "run_offline_checks.py").read_text(encoding="utf-8"))
        result = self._run_aggregation(repo, attempt=False, log_name="c2_pos.jsonl")
        self.assertEqual(result.returncode, 0, result.stdout[-2000:])
        rows = self._rows(result.stdout)
        legacy = [n for n in ("test_freshness.py", "test_freshness_v21.py",
                              "test_acquisition.py", "test_multiturn.py") if n in rows]
        self.assertEqual(len(legacy), 4, f"missing legacy rows: {sorted(rows)}")
        for name in legacy:
            self.assertEqual(rows[name]["exit"], 0, name)
            self.assertEqual(rows[name]["network_attempts"], 0, name)
            self.assertTrue(rows[name]["ok"], name)
        disc = rows.get(f"tests/maintenance:{self._AGG_PATTERN}")
        self.assertIsNotNone(disc)
        self.assertEqual((disc["exit"], disc["ran"], disc["failures"],
                          disc["errors"], disc["skips"]), (0, 1, 0, 0, 0))
        self.assertIn("attempted_network=0", result.stdout)
        self.assertNotIn("missing script", result.stdout)
        self.assertNotIn("NO_TESTS_DISCOVERED", result.stdout)

    def test_caught_violation_still_fails_parent_aggregation(self):
        """Real parent aggregation path, complete fixture: the offending child
        catches the guard violation and exits 0, every other child and the
        discovered test succeed — and the parent STILL fails, with exactly one
        recorded attempt attributed to the offending child."""
        repo = self._build_aggregation_repo(
            Path(tempfile.mkdtemp(prefix="p00c2_vio_")),
            runner_source=(REPO_ROOT / "tools" / "run_offline_checks.py").read_text(encoding="utf-8"))
        result = self._run_aggregation(repo, attempt=True, log_name="c2_vio.jsonl")
        self.assertNotEqual(result.returncode, 0,
                            "parent accepted a child that caught a guard violation")
        rows = self._rows(result.stdout)
        offender = rows["test_freshness.py"]
        # causal condition: the offending child SUCCEEDED (it caught and
        # exited 0) yet is the sole source of the recorded attempt
        self.assertEqual(offender["exit"], 0)
        self.assertEqual(offender["network_attempts"], 1)
        self.assertFalse(offender["ok"])
        for name in ("test_freshness_v21.py", "test_acquisition.py", "test_multiturn.py"):
            self.assertEqual(rows[name]["exit"], 0, name)
            self.assertEqual(rows[name]["network_attempts"], 0, name)
            self.assertTrue(rows[name]["ok"], name)
        disc = rows[f"tests/maintenance:{self._AGG_PATTERN}"]
        self.assertEqual((disc["exit"], disc["ran"], disc["failures"],
                          disc["errors"], disc["skips"]), (0, 1, 0, 0, 0))
        self.assertEqual(disc["network_attempts"], 0)
        self.assertIn("attempted_network=1", result.stdout)

    def test_mutation_detection_enforcement_removal(self):
        """Demonstrate the regression detects enforcement removal: with the
        two attempt-decisions mutated out of a TEMP COPY of the runner, the
        same caught-violation fixture produces the false success (exit 0 with
        attempted_network=1) that the real regression asserts against. The
        real tools/run_offline_checks.py is never edited."""
        real_source = (REPO_ROOT / "tools" / "run_offline_checks.py").read_text(encoding="utf-8")
        sub1_old = "ok = (code == 0) and not attempts"
        sub2_old = "return 0 if exit_ok and not total_attempts else 1"
        # fail loudly if source drift makes the mutation invalid
        self.assertEqual(real_source.count(sub1_old), 1, "legacy decision anchor moved")
        self.assertEqual(real_source.count(sub2_old), 1, "final decision anchor moved")
        mutated = real_source.replace(sub1_old, "ok = (code == 0)") \
                             .replace(sub2_old, "return 0 if exit_ok else 1")
        self.assertIn("install_guard(", mutated)   # guard installation left intact
        self.assertIn("_record(", mutated)          # attempt recording left intact

        repo = self._build_aggregation_repo(
            Path(tempfile.mkdtemp(prefix="p00c2_mut_")), runner_source=mutated)
        result = self._run_aggregation(repo, attempt=True, log_name="c2_mut.jsonl")
        # with enforcement removed: the false success appears — this is exactly
        # the behavior the committed caught-violation regression asserts against
        self.assertEqual(result.returncode, 0,
                         "mutated runner unexpectedly failed; the demonstration "
                         "no longer isolates enforcement removal")
        self.assertIn("attempted_network=1", result.stdout)

        # the real tool is untouched by the demonstration
        self.assertEqual(
            (REPO_ROOT / "tools" / "run_offline_checks.py").read_text(encoding="utf-8"),
            real_source)


class TestC3WindowsArchiveContainment(unittest.TestCase):
    """C3: synthetic TarInfo members with Windows path escapes are rejected
    before any extraction; a valid nested archive really extracts."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p00c1_c3_"))
        self.extract_root = self.tmp / "candidate"
        self.extract_root.mkdir()
        import verify_candidate
        self.mod = verify_candidate

    def _tar(self, members: list[tuple[str, bytes]]) -> Path:
        tar_path = self.tmp / f"archive_{len(members)}.tar"
        with tarfile.open(tar_path, "w") as tf:
            for name, payload in members:
                info = tarfile.TarInfo(name=name)
                info.size = len(payload)
                import io
                tf.addfile(info, io.BytesIO(payload))
        return tar_path

    def _unsafe_names(self) -> list[str]:
        return [
            "C:/outside/probe.txt",          # drive-absolute
            "C:outside.txt",                 # drive-relative
            "\\\\server\\share\\probe.txt",  # UNC
            "\\rooted.txt",                  # backslash-rooted
            "a/../../escape.txt",            # parent traversal
            "sub/../../../escape2.txt",      # deeper traversal
            "file.txt:stream",               # alternate data stream
            "C:stream.txt",                  # drive + ADS shape
        ]

    def _assert_no_extraction(self, tar_path: Path):
        with patch.object(tarfile.TarFile, "extractall") as spy:
            with self.assertRaises(SystemExit):
                self.mod._plan_extraction(tar_path, self.extract_root)
            spy.assert_not_called()

    def test_unsafe_members_rejected_without_extraction(self):
        for name in self._unsafe_names():
            tar_path = self._tar([(name, b"payload")])
            with self.subTest(name=name):
                self._assert_no_extraction(tar_path)

    def test_links_and_nonregular_rejected(self):
        tar_path = self.tmp / "links.tar"
        with tarfile.open(tar_path, "w") as tf:
            sym = tarfile.TarInfo(name="link.txt")
            sym.type = tarfile.SYMTYPE
            sym.linkname = "/etc/passwd"
            tf.addfile(sym)
            hard = tarfile.TarInfo(name="hard.txt")
            hard.type = tarfile.LNKTYPE
            hard.linkname = "elsewhere"
            tf.addfile(hard)
            fifo = tarfile.TarInfo(name="pipe")
            fifo.type = tarfile.FIFOTYPE
            tf.addfile(fifo)
        self._assert_no_extraction(tar_path)

    def test_mixed_archive_extracts_nothing(self):
        tar_path = self._tar([("safe/nested/file.txt", b"fine"),
                              ("C:/outside/probe.txt", b"bad")])
        self._assert_no_extraction(tar_path)

    def test_valid_nested_archive_extracts(self):
        tar_path = self._tar([("safe/nested/file.txt", b"hello nested")])
        extraction_calls = []
        real_extractall = tarfile.TarFile.extractall

        def spy(self, path=None, **kwargs):
            extraction_calls.append(Path(str(path)))
            return real_extractall(self, path=path, **kwargs)

        with patch.object(tarfile.TarFile, "extractall", spy):
            names = self.mod._plan_extraction(tar_path, self.extract_root)
            # _plan_extraction itself must not extract
            self.assertEqual(extraction_calls, [])
            self.assertEqual(names, ["safe/nested/file.txt"])
            # the caller performs the single extraction
            with tarfile.open(tar_path) as tf:
                tf.extractall(path=self.extract_root)
        self.assertEqual(extraction_calls, [self.extract_root])
        extracted = self.extract_root / "safe" / "nested" / "file.txt"
        self.assertEqual(extracted.read_bytes(), b"hello nested")

    def test_containment_uses_resolved_paths(self):
        # a name that normalizes inside the root must resolve inside it too
        tar_path = self._tar([("safe/../safe/file.txt", b"ok")])
        names = self.mod._plan_extraction(tar_path, self.extract_root)
        self.assertEqual(names, ["safe/../safe/file.txt"])
        resolved = (self.extract_root / "safe/../safe/file.txt").resolve()
        root_resolved = self.extract_root.resolve()
        self.assertTrue(str(resolved).startswith(str(root_resolved) + os.sep))


if __name__ == "__main__":
    unittest.main()
