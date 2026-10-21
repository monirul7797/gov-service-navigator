"""Poll a URL until it answers HTTP 200, print the body and exit 0 (standard library only).

    python deploy/smoke.py --url http://localhost/readyz --timeout 300

Used after ``docker compose up -d``: the stack needs a while (database initialisation, health
checks), so the script retries every ``--interval`` seconds until the deadline. Exit code: 0 = got
a 200 (body on stdout), 1 = timed out (last problem on stderr). Progress goes to stderr only.
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from typing import Callable


def fetch(url: str, timeout: float) -> tuple[int | None, str]:
    """``(status, body)``; status is None when no HTTP answer arrived (connection refused, timeout)."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # a non-2xx answer is still an answer
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except OSError as exc:  # URLError, ConnectionError, socket.timeout
        return None, str(getattr(exc, "reason", exc))


def wait_for_ok(
    url: str,
    timeout: float,
    interval: float = 2.0,
    request_timeout: float = 5.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = lambda message: print(message, file=sys.stderr),
) -> str | None:
    """The body of the first HTTP 200 answer, or None if ``timeout`` seconds pass without one."""
    deadline = clock() + timeout
    while True:
        status, body = fetch(url, request_timeout)
        if status == 200:
            return body
        log(f"waiting for {url}: " + (f"HTTP {status}" if status is not None else f"no answer ({body})"))
        if clock() + interval > deadline:
            return None
        sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wait until a URL answers HTTP 200.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--timeout", type=float, default=300.0, metavar="SECONDS")
    parser.add_argument("--interval", type=float, default=2.0, metavar="SECONDS")
    args = parser.parse_args(argv)
    body = wait_for_ok(args.url, args.timeout, args.interval)
    if body is None:
        print(f"FAIL: {args.url} did not answer HTTP 200 within {args.timeout:g} s", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
