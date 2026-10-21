"""The engine's /healthz must match the shared contract example."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gsn.engine.app import create_app

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts/healthz/response.example.json").read_text(encoding="utf-8")
)


def test_healthz_matches_contract():
    body = TestClient(create_app("abc123")).get("/healthz")
    assert body.status_code == 200
    data = body.json()
    assert set(data) == set(CONTRACT)
    assert data == {"status": "ok", "service": "engine", "version": "abc123"}


def test_contract_example_is_the_documented_value():
    assert CONTRACT == {"status": "ok", "service": "engine", "version": "dev"}


def test_module_level_app_defaults_to_dev_version(monkeypatch):
    monkeypatch.delenv("GSN_VERSION", raising=False)
    import importlib

    import gsn.engine.app as mod

    mod = importlib.reload(mod)
    assert TestClient(mod.app).get("/healthz").json()["version"] == "dev"


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_and_schema_endpoints_are_disabled(path):
    assert TestClient(create_app("abc123")).get(path).status_code == 404
