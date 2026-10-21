# Development guide (milestone M0)

How to run the Government Service Navigator stack on your own machine (Windows) and how to run
each test suite. The stack is five containers: `caddy` (front door), `web` (Rust: public site, admin
panel, API edge), `engine` (Python: the answer service), `worker` (Python: background jobs) and
`postgres` (PostgreSQL 17 with pgvector).

## Prerequisites

Set up once (the M0 plan calls this "Task 0"):

- **Docker Desktop** with Compose v2. On this machine it lives in `G:\Docker`; if `docker` is not
  found in a shell, add `G:\Docker\DockerDesktop\resources\bin` to `PATH`.
- **Python 3.12.10** and the project virtual environment `.venv` at the repository root
  (`python -m venv .venv`, then `.venv\Scripts\python -m pip install -e "./engine[dev]"`). All new
  Python tooling and tests run in `.venv`, never in the global Python.
- **Node 24** (`>=24 <25`) for the two frontends. The disk holding `C:` is small, so run host-side npm
  with `--cache G:/gsn-cache/npm`.
- **Rust 1.99.1** is pinned in `web/rust-toolchain.toml`, but there is no C++ build toolchain on this
  machine, so Rust is compiled and tested only inside Docker (`python tools/cargo_docker.py ...`).

## First run

From the repository root:

```
python deploy/make_env.py          # creates .env with random secrets (never commit it, never paste it)
docker compose build               # first build takes several minutes (Rust + two frontends)
docker compose up -d
python deploy/smoke.py --url http://localhost/readyz --timeout 300
```

The smoke script polls until the stack answers; success prints
`{"status":"ok","checks":{"database":"ok","engine":"ok"}}`.

If `make_env.py` says `.env` already exists it leaves it alone: it never overwrites your secrets.

## URLs

| What | Address |
|---|---|
| Public site and API (through Caddy) | <http://localhost> |
| Public site health / readiness | <http://localhost/healthz>, <http://localhost/readyz> |
| Admin panel | <http://127.0.0.1:8081> (this machine only) |

Only Caddy listens on all network interfaces (port 80). The admin panel is published on
`127.0.0.1:8081` only; on a server it is reached over Tailscale by setting `GSN_ADMIN_PUBLISH` in
`.env` to the server's Tailscale address. PostgreSQL, the engine and the web service's public port
are not published at all. To prove it from another machine:

```
python deploy/check_exposure.py --host <ip-of-this-machine> --expect-open 80 --expect-closed 5432,8000,8080
```

## Everyday commands

```
docker compose ps                    # state and health of the five services
docker compose logs -f web           # follow one service (also: engine, worker, postgres, caddy)
docker compose logs --tail 50        # recent lines of everything
docker compose restart worker        # restart one service
docker compose down                  # stop and remove the containers; the database is kept
```

Every service writes one JSON object per line (`ts`, `level`, `service`, `msg`, ...). The worker
prints `worker ready` once it has reached the database, then a `heartbeat` line every minute.

### Looking inside the database

```
docker compose exec postgres psql -U postgres -d gsn
```

This opens an administrator session without a password (the container is local). To test what an
application role may do, connect over the container network so the password is enforced:
`docker compose exec postgres bash -c 'PGPASSWORD="$GSN_DB_WEB_PASSWORD" psql -h postgres -U gsn_web -d gsn'`.
A connection to `127.0.0.1` from inside the PostgreSQL container is trusted without a password by the
image, so it proves nothing about passwords.

### Full reset, including the database

```
docker compose down -v
```

`-v` also deletes the `pgdata` volume. Do this after changing anything in `db/init/` or any database
password in `.env`, because PostgreSQL runs the init scripts (`db/init/10-roles.sh`) only on an empty
volume. All data in the database is lost.

### After editing code

`docker compose build web` (or `engine`) and `docker compose up -d` again. The `worker` reuses the
`gsn-engine:dev` image, so rebuild `engine` and then `docker compose up -d --force-recreate worker`.

## Running the tests

Run these from the repository root.

| Suite | Command |
|---|---|
| Python: deploy scripts, compose rules, engine, worker, tools | `.venv\Scripts\python -m pytest deploy/tests engine/tests tools/tests -q` |
| Rust (inside Docker) | `python tools/cargo_docker.py test` |
| Rust lint (inside Docker) | `python tools/cargo_docker.py clippy --all-targets -- -D warnings` |
| Frontend, per app (`frontend/public`, `frontend/admin`) | `npm run check`, `npm run lint`, `npm run test:build` (add `--cache G:/gsn-cache/npm` to `npm ci`) |
| Routing regression (legacy code, no database or LLM) | `set PYTHONIOENCODING=utf-8` then `python test_multiturn.py` with the global Python |

`deploy/tests/test_compose_exposure.py` reads `compose.yaml` and the Dockerfiles and fails if a new
port is published, a sixth service appears, an image is not pinned to an exact version, or a service
receives another service's database password.

## Python constraints (`engine/constraints.txt`)

`engine/constraints.txt` pins every package in the engine's dependency tree (including the
dependencies of dependencies), so the Docker image gets the same versions on every build. The
engine image installs with `pip install -c constraints.txt`. After changing a dependency in
`engine/pyproject.toml`, regenerate the file from a throw-away virtual environment so that only the
engine's own tree is listed:

```
python -m venv .venv-constraints
.venv-constraints\Scripts\python -m pip install -e "./engine[dev]"
.venv-constraints\Scripts\python -m pip freeze --exclude-editable > constraints.raw
```

Then replace the package lines of `engine/constraints.txt` with the contents of `constraints.raw`
(sorted case-insensitively, keep the comment header), delete `.venv-constraints` and `constraints.raw`,
and run `.venv\Scripts\python -m pytest engine/tests/test_constraints.py`. The entries `colorama` and
`tzdata` only matter on Windows and are harmless in the Linux image.

## Troubleshooting

- **`required variable ... is missing a value: run python deploy/make_env.py first`** - there is no
  `.env`; run `python deploy/make_env.py`.
- **The database role or password seems wrong after editing `.env`** - the roles are created only on
  an empty volume; run `docker compose down -v` and start again.
- **Port 80 is already in use** - another program holds it (IIS, Skype, another web server). Stop
  that program; the compose file deliberately does not move Caddy to another port.
- **`docker` errors such as "read-only file system" or "input/output error"** - Docker's virtual disk
  (`G:\Docker\wsl\disk\docker_data.vhdx`) cannot grow because the drive is full. Free space on the
  drive, restart Docker Desktop, then run `docker builder prune -f` to drop the build cache. Keep at
  least 5 GB free on that drive before a full `docker compose build`.
- **Out of disk space during builds** - `docker builder prune -f` removes only the build cache
  (images and volumes stay).
