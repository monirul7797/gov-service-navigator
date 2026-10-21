# Development guide (milestone M0)

How to run the Government Service Navigator stack on your own machine (Windows) and how to run
each test suite. The stack is five containers: `caddy` (front door), `web` (Rust: public site, admin
panel, API edge), `engine` (Python: the answer service), `worker` (Python: background jobs) and
`postgres` (PostgreSQL 17 with pgvector). A sixth container, `migrate`, is a one-shot job: it applies
the database migrations and exits (see "Database migrations").

**Shell: every command below is for Windows PowerShell (5.1)**, run from the repository root unless a
step says otherwise. Paths use backslashes where PowerShell needs them.

## Prerequisites

Set up once (the M0 plan calls this "Task 0"):

- **Docker Desktop** with Docker Compose v5 (the `docker compose` command). On this machine it lives
  in `G:\Docker`. If `docker` is not found in a shell, add its folder to `PATH` for the current
  PowerShell session:

  ```powershell
  $env:Path = "G:\Docker\DockerDesktop\resources\bin;" + $env:Path
  docker --version
  ```

  (This lasts until you close the window; the folder also holds `docker-credential-desktop`, which
  image pulls need.)
- **Python 3.12.10** and the project virtual environment `.venv` at the repository root:

  ```powershell
  python -m venv .venv
  .\.venv\Scripts\python -m pip install -r requirements-dev.txt -c engine\constraints.txt -e ".\engine[dev]"
  ```

  The `-c engine\constraints.txt` part pins every package to the versions in the lock file, so your
  `.venv` matches what CI and the Docker image install (see "Python constraints"). All new Python
  tooling and tests run in `.venv`, never in the global Python.
- **Node 24** (`>=24 <25`) for the two frontends. Run host-side npm with `--cache G:/gsn-cache/npm`
  (drive `C:` is small).
- **Rust 1.99.1** is pinned in `web/rust-toolchain.toml`, but there is no C++ build toolchain on this
  machine, so Rust is compiled and tested only inside Docker (`python tools/cargo_docker.py ...`).
- **At least 10 GB free** on the drive that holds Docker's disk image (`G:` here). See Troubleshooting.

## First run

```powershell
python deploy/make_env.py          # creates .env with random secrets (never commit it, never paste it)
docker compose build               # first build takes several minutes (Rust + two frontends)
docker compose up -d
python deploy/smoke.py --url http://localhost/readyz --timeout 300
```

The smoke script polls until the stack answers; success prints
`{"checks":{"database":"ok","engine":"ok"},"status":"ok"}`.

If `make_env.py` says `.env` already exists it leaves it alone: it never overwrites your secrets.

## URLs

| What | Address |
|---|---|
| Public site and API (through Caddy) | <http://localhost> |
| Public site health / readiness | <http://localhost/healthz>, <http://localhost/readyz> |
| Admin panel | <http://127.0.0.1:8081> (this machine only) |

Only Caddy listens on all network interfaces (port 80). PostgreSQL, the engine and the web service's
public port are not published at all.

### The admin port and `GSN_ADMIN_PUBLISH`

The admin panel is published on the address given by `GSN_ADMIN_PUBLISH` in `.env`
(`127.0.0.1:8081` by default, i.e. only this machine). On a server it should be reached over
Tailscale, so set it to the server's Tailscale address **including the port**:

```
GSN_ADMIN_PUBLISH=100.101.102.103:8081
```

> **Warning:** a value without an address, such as `GSN_ADMIN_PUBLISH=8081`, publishes the admin
> panel on **all** network interfaces, including the public internet. Always write `<ip>:8081`.

To prove from another machine what is reachable, run (replace `<public-ip>` with the address the
internet uses to reach the server; for a home test, this machine's LAN address):

```powershell
python deploy/check_exposure.py --host <public-ip> --expect-open 80 --expect-closed 8081,5432,8000,8080
```

If the admin panel is published on a Tailscale address, also check it there, from a machine on the
same Tailscale network: `python deploy/check_exposure.py --host <tailscale-ip> --expect-open 8081`.
A probe from the server itself says nothing about what others can reach.

## Everyday commands

```powershell
docker compose ps                    # state and health of the five services
docker compose ps -a                 # also shows the one-shot migrate job ("Exited (0)" is correct)
docker compose logs -f web           # follow one service (also: engine, worker, postgres, caddy)
docker compose logs --tail 50        # recent lines of everything
docker compose restart worker        # restart one service
docker compose down                  # stop and remove the containers; the database is kept
```

Every service writes one JSON object per line (`ts`, `level`, `service`, `msg`, ...). The worker
prints `worker ready` once it has reached the database, then a `heartbeat` line every minute.

### Looking inside the database

```powershell
docker compose exec postgres psql -U postgres -d gsn
```

This opens an administrator session without a password (the container is local). To test what an
application role may do, connect over the container network so the password is enforced (the single
quotes keep PowerShell from touching `$GSN_DB_WEB_PASSWORD`; it is expanded inside the container):

```powershell
docker compose exec postgres bash -c 'PGPASSWORD=$GSN_DB_WEB_PASSWORD psql -h postgres -U gsn_web -d gsn'
```

A connection to `127.0.0.1` from inside the PostgreSQL container is trusted without a password by the
image, so it proves nothing about passwords.

### Database migrations

The database schema lives in numbered SQL files in `db/migrations/` (`0001_audit_log.sql`, ...). The
one-shot `migrate` service runs `gsn-web migrate` as the schema-owner role `gsn_migrate`; it applies
every file that has not been applied yet, records it in the `_sqlx_migrations` table and exits.
`web` and `worker` start only after `migrate` has finished successfully, so a failing migration stops
the stack from coming up instead of leaving it half-updated. Running `migrate` again is harmless.

The migration files are compiled into the `gsn-web` binary (`sqlx::migrate!`), so after adding or
editing one, rebuild the image before starting the stack:

```powershell
docker compose build web
docker compose up -d
docker compose logs migrate          # "migrations applied"
```

Never edit a migration that has already been applied anywhere (the recorded checksum would no longer
match and `migrate` refuses to run); add a new numbered file instead. (`0001_audit_log.sql` was edited
once in place, before it existed anywhere but on local development stacks; if `migrate` says that
migration 1 was "previously applied but has been modified", reset your local database with
`docker compose down -v`.) The `audit_log` table is
append-only: UPDATE, DELETE and TRUNCATE are rejected by triggers for every role, including its owner.

### Full reset, including the database

```powershell
docker compose down -v
```

`-v` also deletes the `pgdata` volume. Do this after changing anything in `db/init/` or any database
password in `.env`, because PostgreSQL runs the init scripts (`db/init/10-roles.sh`) only on an empty
volume. All data in the database is lost.

### After editing code

`docker compose build web` (or `engine`) and `docker compose up -d` again. The `worker` reuses the
`gsn-engine:dev` image, so rebuild `engine` and then `docker compose up -d --force-recreate worker`.

## Running the tests

| Suite | Command |
|---|---|
| Python: deploy scripts, compose rules, engine, worker, tools | `.\.venv\Scripts\python -m pytest deploy/tests engine/tests tools/tests -q` |
| Rust (inside Docker) | `python tools/cargo_docker.py test --locked` |
| Rust format check (inside Docker) | `python tools/cargo_docker.py fmt --check` |
| Rust lint (inside Docker) | `python tools/cargo_docker.py clippy --all-targets --locked -- -D warnings` |
| Stack integration tests (running stack) | `docker compose -f compose.yaml -f compose.test.yaml run --rm itest` |
| Routing regression (legacy code; no database or LLM; global Python) | `$env:PYTHONIOENCODING = "utf-8"; python test_multiturn.py` |

Frontend, once per app (`frontend\public` and `frontend\admin`); `npm ci` always comes first:

```powershell
cd frontend\public
npm ci --cache G:/gsn-cache/npm
npm run check
npm run lint
npm run test:build
npm audit --audit-level=high
cd ..\..
```

### Dependency audits (CI runs these too)

Each one needs internet access, because it asks a public database of known vulnerabilities. A new
advisory can make a check fail without any change in this repository.

| Audit | Command |
|---|---|
| Python (the locked set in `engine/constraints.txt`) | `.\.venv\Scripts\python -m pip_audit -r engine/constraints.txt --no-deps --disable-pip --progress-spinner off` |
| Frontend, in each of `frontend\public` and `frontend\admin` | `npm audit --audit-level=high` |
| Rust (`web/Cargo.lock`) | `cargo audit` in CI; see below for running it by hand |

Notes:

- **Python:** the audit reads the lock file, not the packages installed in `.venv`, so it does not
  report the `pip` that ships with Python.
- **npm:** audit the full tree, without `--omit=dev`. Every package in both apps is listed as a
  development dependency (the build tools), yet the Svelte and SvelteKit code they contain ends up in
  the JavaScript that browsers download, so leaving development packages out would audit nothing. The
  expected result is `found 0 vulnerabilities`. The `--audit-level=high` part only makes the command
  fail (exit code 1) for high or critical findings; lower ones are still printed.
- **Rust:** `cargo-audit` is not installed in the Rust image behind `tools/cargo_docker.py`, so
  `python tools/cargo_docker.py audit` does not work (cargo answers "no such command: `audit`"). CI
  installs `cargo-audit` 0.22.2 and runs `cargo audit`. To run the same check by hand, build the tool
  inside a throw-away container (several minutes, because it compiles `cargo-audit`; `web\` is
  mounted read-only and nothing is kept afterwards):

  ```powershell
  docker run --rm --mount "type=bind,src=$PWD\web,dst=/work/web,readonly" -w /work/web rust:1.99.1-bookworm sh -c "cargo install cargo-audit --version 0.22.2 --locked && cargo audit"
  ```

  The image tag follows `channel` in `web/rust-toolchain.toml`. A clean run ends with a line such as
  `Scanning Cargo.lock for vulnerabilities (199 crate dependencies)` and no warnings or errors.

`deploy/tests/test_compose_exposure.py` reads `compose.yaml`, `compose.test.yaml` and the Dockerfiles
and fails if a new port is published, a service other than the five plus the one-shot `migrate` job
appears, `migrate` gets a restart policy or a port, an image is not pinned to an exact version, a
service loads an `env_file`, or a service receives another service's database password (or the
superuser password). It also checks that `web` waits for a healthy database and the finished
migrations but only for the engine to have started, so a slow engine leaves the site up and degraded
instead of down.

### Integration tests (the running stack)

`tests/integration/` holds tests that talk to the real containers. They run inside the stack's
network as the one-shot `itest` job (`compose.test.yaml`), which uses the `test` image of the engine
(pytest and psycopg installed) and mounts the test files read-only:

```powershell
docker compose up -d
python deploy/smoke.py --url http://localhost/readyz --timeout 300
docker compose -f compose.yaml -f compose.test.yaml run --rm itest
```

They check that each of the four database roles can log in and has exactly the rights it should
(`gsn_web` reads and appends `audit_log`, `gsn_worker` only appends, `gsn_engine` has no access, no
role except the owner can create tables; `tests/integration/test_grants.py` compares the complete
privilege matrix read from the database catalog), that callers can insert only `actor`, `action`,
`target` and `details` (they cannot backdate `at` or choose `id`), that `audit_log` rejects UPDATE,
DELETE and TRUNCATE even for its owner, and that the stack answers correctly over HTTP (health and
readiness, no admin routes on the public side, security headers through Caddy). The build of the
`itest` image is slow the first time and quick afterwards.

`itest` belongs to the compose profile `test`, so a plain `docker compose up` never starts it;
`run itest` enables the profile by itself. The job waits for `web` and `engine` to be healthy
(`run` starts and waits for them). If a test cannot log in to the database, the output says
`cannot connect as gsn_<role>` without the password: the tests convert connection errors, and the
job runs with `--tb=short`, because pytest's long traceback would otherwise print psycopg's
connection string including the password. `tests/integration/test_connection_errors.py` proves this
with a random canary value.

`itest` receives all four role passwords, and every run adds a few rows to `audit_log` (rows can
never be deleted). Run it only against this local development stack; `docker compose down -v`
resets the database. Never run it against a server that holds real data.

## Python constraints (`engine/constraints.txt`)

`engine/constraints.txt` pins every package in the engine's dependency tree (including the
dependencies of dependencies), so the Docker image gets the same versions on every build. The
engine image installs with `pip install -c constraints.txt`. After changing a dependency in
`engine/pyproject.toml`, regenerate the file from a throw-away virtual environment, so that only the
engine's own tree is listed. In PowerShell 5.1 do not write the file with `>`: that produces UTF-16.
These commands keep the comment header, write plain ASCII and sort the pins the same way every time:

```powershell
python -m venv .venv-constraints
.\.venv-constraints\Scripts\python -m pip install -e ".\engine[dev]"
.\.venv-constraints\Scripts\python -m pip freeze --exclude-editable | Out-File -Encoding ascii constraints.raw
$header = Get-Content engine\constraints.txt | Where-Object { $_.StartsWith("#") }
$pins = [string[]](Get-Content constraints.raw)
[Array]::Sort($pins, [System.StringComparer]::OrdinalIgnoreCase)
Set-Content -Encoding ascii engine\constraints.txt ($header + $pins)
Remove-Item -Recurse -Force .venv-constraints, constraints.raw
```

Then run `.\.venv\Scripts\python -m pytest engine/tests/test_constraints.py -q` and check
`git diff engine/constraints.txt`. The entries `colorama` and `tzdata` only matter on Windows and are
harmless in the Linux image. (`.venv-constraints` and `constraints.raw` are in `.gitignore` and
`.dockerignore` in case a step is interrupted.)

## Troubleshooting

- **`required variable ... is missing a value: run python deploy/make_env.py first`** - there is no
  `.env`; run `python deploy/make_env.py`.
- **`password authentication failed for user "gsn_..."` in the logs of `migrate`, `web` or `worker`
  (or the database role or password seems wrong after editing `.env`)** - the database roles are
  created, with the passwords from `.env`, only when the `pgdata` volume is empty. If `.env` was
  recreated or edited while the volume survived (for example after `docker compose down` without
  `-v`), the database still holds the old passwords and rejects the new ones. Run
  `docker compose down -v` (this deletes the local database) and start again.
- **`migrate` fails with "migration 1 was previously applied but has been modified"** - your local
  database was created from an older version of `0001_audit_log.sql`. Reset it with
  `docker compose down -v` (this deletes the local database) and start again.
- **Port 80 is already in use** - another program holds it (IIS, Skype, another web server). Stop
  that program; the compose file deliberately does not move Caddy to another port.
- **`docker` errors such as "read-only file system" or "input/output error"** - Docker's virtual disk
  (`G:\Docker\wsl\disk\docker_data.vhdx`) cannot grow because the drive is full. Free space on the
  drive, restart Docker Desktop, then run `docker builder prune -f`.
- **Disk space** - keep at least 10 GB free on the Docker drive; a clean `docker compose build` needs
  several GB for the build cache. `docker builder prune -f` reclaims the build cache (images and
  volumes stay); it is safe to run at any time, the next build is just slower.
