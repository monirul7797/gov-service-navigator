"""The running stack seen over HTTP, from inside the compose network (standard library only)."""
import json
import urllib.error
import urllib.request


def fetch(url):
    """``(status, headers, body)``; a 4xx/5xx answer is returned, not raised."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, response.headers, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read().decode("utf-8", errors="replace")


def test_public_healthz_through_caddy():
    status, _, body = fetch("http://caddy/healthz")
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert payload["service"] == "web"


def test_readyz_ok():
    status, _, body = fetch("http://web:8080/readyz")
    assert status == 200
    payload = json.loads(body)
    assert payload["status"] == "ok"
    assert payload["checks"] == {"database": "ok", "engine": "ok"}


def test_engine_healthz_on_internal_network():
    status, _, body = fetch("http://engine:8000/healthz")
    assert status == 200
    assert json.loads(body)["service"] == "engine"


def test_no_admin_routes_on_public_side():
    status, _, _ = fetch("http://caddy/admin-api/healthz")
    assert status == 404


def test_security_headers_through_caddy():
    status, headers, _ = fetch("http://caddy/healthz")
    assert status == 200
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert "Server" not in headers
