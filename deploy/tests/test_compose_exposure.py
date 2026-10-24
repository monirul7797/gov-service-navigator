"""Static checks on ``compose.yaml`` and the Dockerfiles (no Docker needed).

They guard the review-focus items for the container stack: nothing but Caddy and the admin
listener is published, the long-running service set is fixed (plus the one-shot ``migrate`` and
``release`` jobs), every image pulled from a registry and every Dockerfile ``FROM`` is pinned to an exact version,
secrets are never written into the compose file, and each service only receives its own database
password. ``compose.test.yaml`` (the ``itest`` job) gets its own checks at the end.
"""
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
COMPOSE_TEST = ROOT / "compose.test.yaml"
DOCKERFILES = [ROOT / "web" / "Dockerfile", ROOT / "engine" / "Dockerfile"]

ALLOWED_PORTS = {"caddy": ["80:80"], "web": ["${GSN_ADMIN_PUBLISH:-127.0.0.1:8081}:8081"]}
LONG_RUNNING_SERVICES = {"caddy", "web", "engine", "worker", "postgres"}
ONE_SHOT_SERVICES = {"migrate", "release"}  # run to completion and exit (restart "no")
EXPECTED_SERVICES = LONG_RUNNING_SERVICES | ONE_SHOT_SERVICES

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
    "migrate": {"GSN_DB_MIGRATE_PASSWORD"},
    "web": {"GSN_DB_WEB_PASSWORD"},
    "engine": set(),
    "worker": {"GSN_DB_WORKER_PASSWORD"},
    "release": {"GSN_DB_WORKER_PASSWORD"},
    "postgres": DB_PASSWORD_VARS,
}

EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")


def _compose():
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def _compose_test():
    return yaml.safe_load(COMPOSE_TEST.read_text(encoding="utf-8"))


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


def test_no_service_uses_host_networking_devices_or_privileged_mode():
    for name, svc in _services().items():
        assert svc.get("network_mode") != "host", name
        assert not svc.get("devices"), name
        assert not svc.get("privileged"), name


def test_no_service_loads_an_env_file():
    # env_file would hand every variable of a file (all five database passwords in .env) to a service
    # and bypass the per-service password rule below; each secret must be listed explicitly.
    for name, svc in _services().items():
        assert "env_file" not in svc, name


def test_web_admin_listener_is_reachable_through_the_published_port():
    # The default (127.0.0.1) would be unreachable through a published port.
    env = _env(_services()["web"])
    assert env["GSN_ADMIN_BIND"] == "0.0.0.0:8081"
    assert env["GSN_PUBLIC_BIND"] == "0.0.0.0:8080"


# --- service set -----------------------------------------------------------------------------


def test_long_running_services_are_exactly_five_plus_the_one_shot_jobs():
    services = _services()
    assert set(services) == EXPECTED_SERVICES
    long_running = {name for name, svc in services.items() if svc.get("restart") != "no"}
    assert long_running == LONG_RUNNING_SERVICES
    for name in LONG_RUNNING_SERVICES:
        assert services[name]["restart"] == "unless-stopped", name


def test_migrate_is_a_one_shot_job_on_the_web_image():
    migrate = _services()["migrate"]
    assert migrate["restart"] == "no"  # it must exit and stay stopped, never be restarted
    assert not migrate.get("ports")
    assert migrate["image"] == "gsn-web:dev"
    assert "build" not in migrate  # reuses the image the web service builds
    assert migrate["command"] == ["migrate"]
    env = _env(migrate)
    assert env["GSN_DB_USER"] == "gsn_migrate"
    assert env["GSN_DB_PASSWORD"].startswith("${GSN_DB_MIGRATE_PASSWORD")
    assert migrate["depends_on"] == {"postgres": {"condition": "service_healthy"}}


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
    # web must NOT wait for a healthy engine: a slow or failing model load (M2) would otherwise keep
    # web and caddy from starting at all, where the aim is a degraded site (/readyz 503, engine "fail").
    # It does wait for the database and for the migrations to have been applied.
    assert services["web"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"},
        "migrate": {"condition": "service_completed_successfully"},
        "engine": {"condition": "service_started"},
    }
    assert services["worker"]["depends_on"] == {
        "postgres": {"condition": "service_healthy"},
        "migrate": {"condition": "service_completed_successfully"},
    }
    assert services["caddy"]["depends_on"]["web"]["condition"] == "service_healthy"


def test_worker_restarts_because_it_exits_on_errors_after_ready():
    assert _services()["worker"]["restart"] == "unless-stopped"


def test_worker_reuses_the_engine_image():
    services = _services()
    assert services["worker"]["image"] == services["engine"]["image"] == "gsn-engine:dev"
    assert services["worker"]["command"] == ["gsn", "worker"]
    assert "build" not in services["worker"]


def test_release_is_a_one_shot_tool_that_is_never_started_by_a_plain_up():
    release = _services()["release"]
    assert release["restart"] == "no"
    assert release.get("profiles") == ["tools"]  # `docker compose up` skips it; `--profile tools run` starts it
    assert not release.get("ports")
    assert release["image"] == "gsn-release:dev"
    assert release["build"] == {"context": "./engine", "target": "release"}
    assert release["depends_on"] == {"migrate": {"condition": "service_completed_successfully"}}
    assert "command" not in release  # the image's entrypoint is `gsn`, so the arguments are the owner's


def test_release_mounts_only_the_models_volume_and_the_repository_read_only():
    release = _services()["release"]
    # The repository is mounted as the git directory only (no working tree, so no setenv*.bat or .env
    # file is visible, only what history holds, which the CLI scans before it reads anything).
    assert release["volumes"] == ["gsn-models:/models", "${GSN_REPO_GIT:-./.git}:/repo.git:ro"]
    assert "gsn-models" in _compose()["volumes"]


def test_release_uses_the_worker_role_and_receives_only_its_password():
    env = _env(_services()["release"])
    assert env["GSN_DB_USER"] == "gsn_worker"
    assert env["GSN_DB_PASSWORD"] == "${GSN_DB_WORKER_PASSWORD:?run python deploy/make_env.py first}"
    assert env["GSN_ACTOR"] == "${GSN_ACTOR:-owner}"
    # The fake embedder is chosen on the command line (-e GSN_EMBEDDER=fake), never by default.
    assert "GSN_EMBEDDER" not in env


# --- pinned images ---------------------------------------------------------------------------


def test_every_image_is_pinned():
    # Locally built images (a service with ``build:``) and the services that reuse them are exempt;
    # everything pulled from a registry needs an exact version tag or a digest.
    services = _services()
    built = {svc["image"] for svc in services.values() if "build" in svc}
    assert built == {"gsn-web:dev", "gsn-engine:dev", "gsn-release:dev"}
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


def test_superuser_password_reaches_only_the_postgres_service():
    for name, svc in _services().items():
        assert ("POSTGRES_PASSWORD" in yaml.safe_dump(svc)) == (name == "postgres"), name


def test_every_secret_value_comes_from_the_environment_file():
    # No literal password in compose.yaml: each *PASSWORD value must be a ${VAR...} reference.
    for name, svc in _services().items():
        for key, value in _env(svc).items():
            if "PASSWORD" in key:
                assert re.fullmatch(r"\$\{[A-Z_]+(:?[?-][^}]*)?\}", value), f"{name}.{key}"


def test_services_use_their_own_database_roles():
    services = _services()
    assert _env(services["migrate"])["GSN_DB_USER"] == "gsn_migrate"
    assert _env(services["web"])["GSN_DB_USER"] == "gsn_web"
    assert _env(services["worker"])["GSN_DB_USER"] == "gsn_worker"
    assert _env(services["release"])["GSN_DB_USER"] == "gsn_worker"
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


def test_engine_release_stage_is_the_runtime_plus_git_and_runs_as_the_dedicated_user():
    text = (ROOT / "engine" / "Dockerfile").read_text(encoding="utf-8")
    block = re.search(r"^FROM runtime AS release\b(.*?)(?=^FROM |\Z)", text, re.M | re.S).group(1)
    users = re.findall(r"^USER (\S+)", block, re.M)
    assert users[0] == "root" and users[-1] == "gsn"  # root only for the install, the entrypoint runs as gsn
    assert "--no-install-recommends git" in block and "/var/lib/apt/lists" in block
    assert "chown gsn:gsn /models" in block
    for setting in ("HF_HOME=/models", "HF_HUB_OFFLINE=1", "GIT_CONFIG_NOSYSTEM=1", "GSN_REPO_GIT=/repo.git"):
        assert setting in block, setting
    assert 'ENTRYPOINT ["gsn"]' in block and 'CMD ["--help"]' in block
    assert "EXPOSE" not in block


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


INIT_SCRIPTS = sorted((ROOT / "db" / "init").glob("*.sh"))


def test_the_init_scripts_are_the_expected_ones():
    # PostgreSQL runs them in file name order, once, on an empty volume: roles first, then the guard.
    assert [path.name for path in INIT_SCRIPTS] == ["10-roles.sh", "20-guard.sh"]


@pytest.mark.parametrize("script", INIT_SCRIPTS, ids=lambda path: path.name)
def test_init_scripts_stop_on_errors(script):
    text = script.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/bash\n") or text.startswith("#!/usr/bin/env bash\n")
    assert "set -euo pipefail" in text
    # every psql call stops at the first SQL error (otherwise a half-applied script still "succeeds")
    calls = re.findall(r"^psql\b[^\n]*", text, re.M)
    assert calls and all("ON_ERROR_STOP=1" in call for call in calls), calls


# --- proof that the guards fail on a compose file that breaks the rules (temporary copies) -----


def _mutated_compose(tmp_path, monkeypatch, mutate):
    data = _compose()
    mutate(data["services"])
    path = tmp_path / "compose.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setattr(__import__(__name__, fromlist=["COMPOSE"]), "COMPOSE", path)


def _add_env_file(services):
    services["caddy"]["env_file"] = [".env"]


def _leak_superuser_password(services):
    services["worker"]["environment"]["POSTGRES_PASSWORD"] = "${POSTGRES_PASSWORD}"


def _web_waits_for_healthy_engine(services):
    services["web"]["depends_on"]["engine"]["condition"] = "service_healthy"


def _web_skips_migrate(services):
    del services["web"]["depends_on"]["migrate"]


def _migrate_restarts(services):
    services["migrate"]["restart"] = "unless-stopped"


def _publish_migrate(services):
    services["migrate"]["ports"] = ["8082:8082"]


def _publish_postgres(services):
    services["postgres"]["ports"] = ["5432:5432"]


def _publish_admin_on_all_interfaces(services):
    services["web"]["ports"] = ["8081:8081"]


def _publish_release(services):
    services["release"]["ports"] = ["8082:8082"]


def _release_restarts(services):
    services["release"]["restart"] = "unless-stopped"


def _release_always_starts(services):
    del services["release"]["profiles"]


def _release_gets_every_password(services):
    services["release"]["environment"]["GSN_DB_MIGRATE_PASSWORD"] = "${GSN_DB_MIGRATE_PASSWORD}"


def _release_mounts_the_working_tree(services):
    services["release"]["volumes"] = ["gsn-models:/models", "./:/repo.git:ro"]


def _release_mounts_the_repository_writable(services):
    services["release"]["volumes"] = ["gsn-models:/models", "${GSN_REPO_GIT:-./.git}:/repo.git"]


@pytest.mark.parametrize(
    "guard, mutate",
    [
        (test_no_service_loads_an_env_file, _add_env_file),
        (test_superuser_password_reaches_only_the_postgres_service, _leak_superuser_password),
        (test_dependencies_wait_for_healthy_services, _web_waits_for_healthy_engine),
        (test_dependencies_wait_for_healthy_services, _web_skips_migrate),
        (test_long_running_services_are_exactly_five_plus_the_one_shot_jobs, _migrate_restarts),
        (test_only_caddy_and_admin_listener_are_published, _publish_migrate),
        (test_only_caddy_and_admin_listener_are_published, _publish_postgres),
        (test_only_caddy_and_admin_listener_are_published, _publish_admin_on_all_interfaces),
        (test_only_caddy_and_admin_listener_are_published, _publish_release),
        (test_long_running_services_are_exactly_five_plus_the_one_shot_jobs, _release_restarts),
        (test_release_is_a_one_shot_tool_that_is_never_started_by_a_plain_up, _release_always_starts),
        (test_each_service_receives_only_its_own_database_password, _release_gets_every_password),
        (test_release_mounts_only_the_models_volume_and_the_repository_read_only, _release_mounts_the_working_tree),
        (test_release_mounts_only_the_models_volume_and_the_repository_read_only, _release_mounts_the_repository_writable),
    ],
    ids=[
        "env_file",
        "superuser_password",
        "web_waits_for_engine",
        "web_skips_migrate",
        "migrate_restarts",
        "migrate_port",
        "postgres_port",
        "admin_on_all_interfaces",
        "release_port",
        "release_restarts",
        "release_without_profile",
        "release_gets_every_password",
        "release_mounts_working_tree",
        "release_mounts_repository_writable",
    ],
)
def test_guard_fails_on_a_violating_compose_file(tmp_path, monkeypatch, guard, mutate):
    guard()  # passes on the real file
    _mutated_compose(tmp_path, monkeypatch, mutate)
    with pytest.raises(AssertionError):
        guard()


# --- compose.test.yaml: the one-shot integration-test job ------------------------------------


def test_compose_test_file_adds_only_the_itest_job():
    services = _compose_test()["services"]
    assert set(services) == {"itest"}
    itest = services["itest"]
    assert not itest.get("ports")
    assert "env_file" not in itest
    assert itest["restart"] == "no"
    assert itest["build"] == {"context": "./engine", "target": "test"}
    # The tests are mounted read-only, never copied into an image.
    assert itest["volumes"] == ["./tests/integration:/itests:ro"]
    assert itest["command"][:4] == ["python", "-m", "pytest", "/itests"]
    # pytest's long traceback prints function arguments, which for a failed database login includes
    # the connection string with the password: short tracebacks are required.
    assert "--tb=short" in itest["command"]
    # A plain `docker compose up` must never start the job (it only runs on request, via its profile).
    assert itest["profiles"] == ["test"]
    # `run` starts and waits for exactly these dependencies; the tests also go through Caddy.
    assert itest["depends_on"] == {
        "web": {"condition": "service_healthy"},
        "engine": {"condition": "service_healthy"},
        "caddy": {"condition": "service_started"},
    }
    assert _env(itest)["GSN_DB_HOST"] == "postgres"


def test_itest_secrets_come_from_the_environment_file():
    # itest checks what each role may do, so it receives all four role passwords (and no others).
    env = _env(_compose_test()["services"]["itest"])
    passwords = {key: value for key, value in env.items() if "PASSWORD" in key}
    assert set(passwords) == DB_PASSWORD_VARS
    for key, value in passwords.items():
        assert re.fullmatch(r"\$\{" + key + r":\?[^}]*\}", value), key
    assert "POSTGRES_PASSWORD" not in yaml.safe_dump(_compose_test())
