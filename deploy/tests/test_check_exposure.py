"""Tests for deploy/check_exposure.py (which TCP ports are reachable on a host)."""
import socket

import pytest

from check_exposure import evaluate, main, probe


# A closed loopback port takes ~2 s to refuse on Windows; a short timeout still reports "not open".
FAST = 0.3


def _free_port():
    """A port that was free a moment ago (bind to :0, read it, close)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s, s.getsockname()[1]


def test_probe_detects_listening_port():
    server, port = _listener()
    try:
        assert probe("127.0.0.1", port) is True
    finally:
        server.close()


def test_probe_detects_closed_port():
    assert probe("127.0.0.1", _free_port(), timeout=FAST) is False


def test_evaluate_passes_when_expectations_hold():
    server, open_port = _listener()
    try:
        assert evaluate("127.0.0.1", [open_port], [_free_port()], timeout=FAST) == []
    finally:
        server.close()


def test_evaluate_reports_both_kinds_of_mismatch():
    server, open_port = _listener()
    try:
        closed_port = _free_port()
        # one open port listed in expect_closed and one closed port listed in expect_open
        problems = evaluate("127.0.0.1", [closed_port], [open_port], timeout=FAST)
        assert len(problems) == 2
    finally:
        server.close()


def test_cli_exit_codes():
    server, open_port = _listener()
    try:
        closed_port = _free_port()
        base = ["--host", "127.0.0.1", "--timeout", str(FAST)]
        assert main(base + ["--expect-open", str(open_port), "--expect-closed", str(closed_port)]) == 0
        assert main(base + ["--expect-open", str(closed_port), "--expect-closed", str(open_port)]) == 1
    finally:
        server.close()


def test_unresolvable_host_is_a_failure_not_a_pass(monkeypatch):
    def boom(*args, **kwargs):
        raise socket.gaierror("name not known")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    problems = evaluate("no-such-host.invalid", [], [5432])
    assert len(problems) == 1
    assert "resolve" in problems[0]


def test_port_listed_as_open_and_closed_is_rejected():
    with pytest.raises(ValueError):
        evaluate("127.0.0.1", [80], [80])


def test_cli_accepts_comma_separated_lists():
    ports = [_free_port(), _free_port(), _free_port()]
    assert main(["--host", "127.0.0.1", "--timeout", str(FAST), "--expect-closed", ",".join(map(str, ports))]) == 0
