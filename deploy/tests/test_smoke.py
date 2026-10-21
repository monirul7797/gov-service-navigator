"""deploy/smoke.py against a throw-away local HTTP server (no Docker needed)."""
import http.server
import threading

import pytest

import smoke  # deploy/ is on sys.path via conftest.py


@pytest.fixture
def server():
    """Serve /ready: 503 for the first ``fail_first`` requests, then 200 with a JSON body."""
    state = {"calls": 0, "fail_first": 2, "truncate_first": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            state["calls"] += 1
            if state["calls"] <= state["truncate_first"]:
                # promise 100 bytes, send 5 and hang up: the client sees http.client.IncompleteRead
                self.send_response(200)
                self.send_header("Content-Length", "100")
                self.end_headers()
                self.wfile.write(b"short")
                self.close_connection = True
                return
            ok = self.path == "/ready" and state["calls"] > state["fail_first"]
            body = b'{"status":"ok"}' if ok else b'{"status":"degraded"}'
            self.send_response(200 if ok else 503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    state["url"] = f"http://127.0.0.1:{httpd.server_address[1]}/ready"
    yield state
    httpd.shutdown()
    httpd.server_close()


def test_polls_until_200_then_prints_the_body_and_exits_0(server, capsys):
    assert smoke.main(["--url", server["url"], "--timeout", "30", "--interval", "0.05"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == '{"status":"ok"}'
    assert server["calls"] == 3
    assert "HTTP 503" in captured.err


def test_exits_1_when_the_deadline_passes(server, capsys):
    server["fail_first"] = 10_000
    assert smoke.main(["--url", server["url"], "--timeout", "0.3", "--interval", "0.05"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "did not answer HTTP 200" in captured.err


def test_connection_refused_counts_as_not_ready_and_times_out(capsys):
    # port 9 (discard) is closed on a normal machine; nothing listens there
    assert smoke.main(["--url", "http://127.0.0.1:9/readyz", "--timeout", "0.3", "--interval", "0.05"]) == 1
    assert "no answer" in capsys.readouterr().err


def test_wait_for_ok_uses_the_injected_clock():
    ticks = iter([0.0, 5.0, 11.0, 17.0])
    calls = []
    result = smoke.wait_for_ok(
        "http://127.0.0.1:9/x",
        timeout=10,
        interval=1,
        clock=lambda: next(ticks),
        sleep=calls.append,
        log=lambda _m: None,
    )
    assert result is None
    assert calls == [1]


def test_a_truncated_response_is_retried_not_raised(server, capsys):
    # http.client.IncompleteRead is an HTTPException, not an OSError; a stack that is still starting
    # can drop a connection mid-answer and smoke.py must keep polling.
    server["truncate_first"] = 2
    server["fail_first"] = 2
    assert smoke.main(["--url", server["url"], "--timeout", "30", "--interval", "0.05"]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == '{"status":"ok"}'
    assert "no answer" in captured.err


def test_fetch_reports_a_malformed_status_line_as_no_answer():
    # BadStatusLine is also an http.client.HTTPException
    import socket
    import threading

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def serve_garbage():
        conn, _ = listener.accept()
        conn.recv(4096)
        conn.sendall(b"this is not http\r\n\r\n")
        conn.close()

    thread = threading.Thread(target=serve_garbage, daemon=True)
    thread.start()
    try:
        status, detail = smoke.fetch(f"http://127.0.0.1:{listener.getsockname()[1]}/x", 5)
    finally:
        listener.close()
        thread.join(timeout=5)
    assert status is None and detail
