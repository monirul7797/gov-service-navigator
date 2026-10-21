"""Static checks on ``compose.yaml`` and the Dockerfiles (no Docker needed).

They guard the review-focus items for the container stack: nothing but Caddy and the admin
listener is published, the long-running service set is fixed, every image pulled from a registry
and every Dockerfile ``FROM`` is pinned to an exact version, secrets are never written into the
compose file, and each service only receives its own database password.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
DOCKERFILES = [ROOT / "web" / "Dockerfile", ROOT / "engine" / "Dockerfile"]

ALLOWED_PORTS = {"caddy": ["80:80"], "web": ["${GSN_ADMIN_PUBLISH:-127.0.0.1:8081}:8081"]}
EXPECTED_SERVICES = {"caddy", "web", "engine", "worker", "postgres"}

# Which database password variable each service may receive (spec section 6.1). The engine has no
# database access in M0, so it gets none. Postgres receives all of them to create the roles.
DB_PASSWORD_VARS = {
    "GSN_DB_MIGRATE_PASSWORD",
    "GSN_DB_WEB_PASSWORD",
    "GSN_DB_ENGINE_PASSWORD",
    "GSN_DB_WORKER_PASSWORD",
}
ALLOWED_DB_PASSWORDS = {
    "caddy": set(),
    "web": {"GSN_DB_WEB_PASSWORD"},
    "engine": set(),
    "worker": {"GSN_DB_WORKER_PASSWORD"},
    "postgres": DB_PASSWORD_VARS,
}

EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")


def _compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _services():
    return _compose()["services"]


def _env(service):
    env = service.get("environment", {})
    if isinstance(env, list):
        env = dict(item.split("=", 1) for item in env)
    return {key: str(value) for key, value in env.items()}


def split_image(reference):
    """``(name, tag, digest)`` of an image reference; tag/digest are None when absent."""
    digest = None
    if "@" in reference:
        reference, digest = reference.split("@", 1)
    last = reference.rsplit("/", 1)[-1]
    if ":" in last:
        reference, tag = reference.rsplit(":", 1)
    else:
        tag = None
    return reference, tag, digest


def is_pinned(reference):
    """True for an exact version tag (X.Y.Z somewhere in it) or a digest; never ``latest``."""
    _, tag, digest = split_image(reference)
    if digest and digest.startswith("sha256:") and len(digest) == len("sha256:") + 64:
        return tag != "latest"
    return tag is not None and tag != "latest" and EXACT_VERSION.search(tag) is not None


def dockerfile_from_lines(path):
    """Each ``FROM`` as ``(image, alias_or_None)``; stage aliases are returned for later skipping."""
    froms = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?\s*$", line, re.IGNORECASE)
        if match:
            froms.append((match.group(1), match.group(2)))
    return froms


# --- published ports -------------------------------------------------------------------------


def test_only_caddy_and_admin_listener_are_published():
    services = _services()
    published = {name: svc.get("ports", []) for name, svc in services.items() if svc.get("ports")}
    assert published == ALLOWED_PORTS


def test_no_service_uses_host_networking_or_expose_to_bypass_the_port_rule():
    for name, svc in _services().items():
        assert svc.get("network_mode") != "host", name
        assert not svc.get("devices"), name
        assert not svc.get("privileged"), name


def test_web_admin_listener_is_reachable_through_the_published_port():
    # The default (127.0.0.1) would be unreachable through a published port.
    env = _env(_services()["web"])
    assert env["GSN_ADMIN_BIND"] == "0.0.0.0:8081"
    assert env["GSN_PUBLIC_BIND"] == "0.0.0.0:8080"


# --- service set -----------------------------------------------------------------------------


def test_long_running_services_are_exactly_five():
    assert set(_services()) == EXPECTED_SERVICES


def test_web_and_engine_and_postgres_have_healthchecks_with_the_documented_settings():
    services = _services()
    assert services["web"]["healthcheck"]["test"] == ["CMD", "/app/gsn-web", "healthcheck"]
    assert services["engine"]["healthcheck"]["start_period"] == "120s"
    test = services["postgres"]["healthcheck"]["test"]
    assert "pg_isready" in " ".join(test)
    # TCP, not the unix socket: the temporary init-phase server listens on the socket only, so a
    # socket-based check would report "healthy" before the init scripts have finished.
    assert "127.0.0.1" in " ".join(test)


def test_dependencies_wait_for_healthy_services():
    services = _services()
    web_deps = services["web"]["depends_on"]
    assert web_deps["postgres"]["condition"] == "service_healthy"
    assert web_deps["engine"]["condition"] == "service_healthy"
    assert services["worker"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["caddy"]["depends_on"]["web"]["condition"] == "service_healthy"


def test_worker_restarts_because_it_exits_on_errors_after_ready():
    assert _services()["worker"]["restart"] == "unless-stopped"


def test_worker_reuses_the_engine_image():
    services = _services()
    assert services["worker"]["image"] == services["engine"]["image"] == "gsn-engine:dev"
    assert services["worker"]["command"] == ["gsn", "worker"]
    assert "build" not in services["worker"]


# --- pinned images ---------------------------------------------------------------------------


def test_every_image_is_pinned():
    # Locally built images (a service with ``build:``) and the services that reuse them are exempt;
    # everything pulled from a registry needs an exact version tag or a digest.
    services = _services()
    built = {svc["image"] for svc in services.values() if "build" in svc}
    assert built == {"gsn-web:dev", "gsn-engine:dev"}
    pulled = {name: svc["image"] for name, svc in services.items() if svc["image"] not in built}
    assert set(pulled) == {"postgres", "caddy"}
    for name, reference in pulled.items():
        assert is_pinned(reference), f"{name}: {reference} is not pinned to an exact version"


@pytest.mark.parametrize("reference", ["caddy:2", "caddy:2.11", "caddy:latest", "caddy", "postgres:17"])
def test_is_pinned_rejects_floating_references(reference):
    assert not is_pinned(reference)


@pytest.mark.parametrize(
    "reference",
    [
        "caddy:2.11.7",
        "pgvector/pgvector:0.8.7-pg17-bookworm",
        "gcr.io/distroless/cc-debian12:nonroot@sha256:" + "a" * 64,
        "registry:5000/team/app:1.2.3",
    ],
)
def test_is_pinned_accepts_exact_versions_and_digests(reference):
    assert is_pinned(reference)


@pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.parent.name)
def test_every_dockerfile_from_is_pinned(path):
    froms = dockerfile_from_lines(path)
    assert froms, f"no FROM found in {path}"
    stages = set()
    for image, alias in froms:
        if image not in stages:  # FROM <earlier stage> is not a registry pull
            assert is_pinned(image), f"{path.parent.name}/Dockerfile: {image} is not pinned"
        if alias:
            stages.add(alias)


def test_web_dockerfile_rust_image_matches_the_toolchain_file():
    channel = re.search(
        r'channel\s*=\s*"([^"]+)"', (ROOT / "web" / "rust-toolchain.toml").read_text(encoding="utf-8")
    ).group(1)
    text = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert f"FROM rust:{channel}-bookworm" in text
    # RUSTUP_TOOLCHAIN makes cargo use the image's own toolchain instead of downloading components.
    assert f"RUSTUP_TOOLCHAIN={channel}" in text


# --- secrets and least privilege -------------------------------------------------------------


def test_each_service_receives_only_its_own_database_password():
    for name, svc in _services().items():
        text = yaml.safe_dump(svc)
        used = {var for var in DB_PASSWORD_VARS if var in text}
        assert used == ALLOWED_DB_PASSWORDS[name], name


def test_every_secret_value_comes_from_the_environment_file():
    # No literal password in compose.yaml: each *PASSWORD value must be a ${VAR...} reference.
    for name, svc in _services().items():
        for key, value in _env(svc).items():
            if "PASSWORD" in key:
                assert re.fullmatch(r"\$\{[A-Z_]+(:?[?-][^}]*)?\}", value), f"{name}.{key}"


def test_services_use_their_own_database_roles():
    services = _services()
    assert _env(services["web"])["GSN_DB_USER"] == "gsn_web"
    assert _env(services["worker"])["GSN_DB_USER"] == "gsn_worker"
    assert "GSN_DB_USER" not in _env(services["engine"])


# --- containers run as non-root --------------------------------------------------------------


def test_web_runtime_image_is_distroless_nonroot():
    text = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM gcr\.io/distroless/cc-debian12:nonroot@sha256:[0-9a-f]{64}\b", text, re.M)
    assert re.search(r"^USER nonroot", text, re.M)
    assert 'ENTRYPOINT ["/app/gsn-web"]' in text
    assert 'CMD ["serve"]' in text


@pytest.mark.parametrize("stage", ["runtime", "test"])
def test_engine_stages_run_as_a_dedicated_non_root_user(stage):
    text = (ROOT / "engine" / "Dockerfile").read_text(encoding="utf-8")
    block = re.search(rf"^FROM \S+ AS {stage}\b(.*?)(?=^FROM |\Z)", text, re.M | re.S).group(1)
    user = re.search(r"^USER (\S+)", block, re.M)
    assert user is not None and user.group(1) not in {"root", "0"}


def test_engine_runtime_stage_contains_no_tests():
    text = (ROOT / "engine" / "Dockerfile").read_text(encoding="utf-8")
    runtime = re.search(r"^FROM \S+ AS runtime\b(.*?)(?=^FROM |\Z)", text, re.M | re.S).group(1)
    assert "tests" not in runtime
    test = re.search(r"^FROM \S+ AS test\b(.*?)(?=^FROM |\Z)", text, re.M | re.S).group(1)
    assert ".[dev]" in test or "[dev]" in test


def test_engine_build_context_ignores_caches_and_build_output():
    lines = {
        line.strip()
        for line in (ROOT / "engine" / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    # "tests": the runtime and test images never contain test files (integration tests are mounted).
    assert {".pytest_cache", "*.egg-info", "__pycache__", "tests"} <= lines


def test_roles_script_stops_on_errors():
    text = (ROOT / "db" / "init" / "10-roles.sh").read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash\n") or text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
