"""Check which TCP ports of a host accept connections (standard library only).

Use it to prove that only the intended ports are reachable, e.g. that Caddy answers on 80
while PostgreSQL (5432) and the internal service ports (8000, 8080) do not. For a meaningful
result run it from a different machine than the one hosting the stack; a probe from the host
itself says nothing about what the internet can reach.

    python deploy/check_exposure.py --host H --expect-open 80 --expect-closed 5432,8000,8080

Exit code: 0 = every expectation holds, 1 = at least one mismatch (details on stdout),
2 = invalid arguments.
"""
from __future__ import annotations

import argparse
import socket
import sys


def probe(host: str, port: int, timeout: float = 2.0) -> bool:
    """True if a TCP connection to ``host:port`` succeeds within ``timeout`` seconds.

    Refused, filtered (timeout) and unreachable all count as "not open".
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def evaluate(
    host: str, expect_open: list[int], expect_closed: list[int], timeout: float = 2.0
) -> list[str]:
    """Return human-readable problems; an empty list means every expectation holds."""
    both = sorted(set(expect_open) & set(expect_closed))
    if both:
        raise ValueError(f"ports listed as both open and closed: {both}")
    try:
        # An unresolvable host would make every "closed" expectation pass by accident.
        socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return [f"cannot resolve host {host!r}: {exc}"]

    problems: list[str] = []
    for port in expect_open:
        if not probe(host, port, timeout):
            problems.append(f"port {port} on {host} should be OPEN but is not reachable")
    for port in expect_closed:
        if probe(host, port, timeout):
            problems.append(f"port {port} on {host} should be CLOSED but accepts connections")
    return problems


def _ports(text: str) -> list[int]:
    ports: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        port = int(part)
        if not 1 <= port <= 65535:
            raise argparse.ArgumentTypeError(f"port out of range: {port}")
        ports.append(port)
    return ports


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check which TCP ports of a host are reachable.")
    parser.add_argument("--host", required=True)
    parser.add_argument("--expect-open", type=_ports, default=[], metavar="PORTS",
                        help="comma-separated ports that must accept connections")
    parser.add_argument("--expect-closed", type=_ports, default=[], metavar="PORTS",
                        help="comma-separated ports that must not accept connections")
    parser.add_argument("--timeout", type=float, default=2.0, metavar="SECONDS",
                        help="per-port connect timeout (default: 2)")
    args = parser.parse_args(argv)
    try:
        problems = evaluate(args.host, args.expect_open, args.expect_closed, args.timeout)
    except ValueError as exc:
        parser.error(str(exc))  # exits with status 2
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    print(f"OK: {args.host} matches expectations "
          f"(open: {args.expect_open or 'none'}, closed: {args.expect_closed or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
