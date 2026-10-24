# Development guide (milestone M0)

How to run the Government Service Navigator stack on your own machine (Windows) and how to run
each test suite. The stack is five containers: `caddy` (front door), `web` (Rust: public site, admin
panel, API edge), `engine` (Python: the answer service), `worker` (Python: background jobs) and
`postgres` (PostgreSQL 17 with pgvector). A sixth container, `migrate`, is a one-shot job: it applies
the database migrations and exits (see "Database migrations"). A seventh, `release`, is a one-off tool
you start by hand to build and publish data releases (see "Releases"); `docker compose up` never
starts it.

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
  .\.venv\Scripts\python -m pip install -r requirements-dev.txt
  ```

  Run it from the repository root. `requirements-dev.txt` contains no pins of its own: it installs
  the engine with its `[dev]` extra (pytest, pyyaml, ...) and pins every package to the versions in
  `engine/constraints.txt`, so your `.venv` matches what CI and the Docker image install (see
  "Python constraints"). All new Python tooling and tests run in `.venv`, never in the global Python.
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

### The guard watchdog

The tables that hold the release history and the audit log, and the triggers and functions that
protect them (the append-only rules, the state rules, the hash chain, publish, rollback and verify),
are owned by `gsn_migrate`, so that role could drop or switch off a protection. To prevent that, a
**watchdog** owned by the PostgreSQL superuser checks them after every schema change in the database.
The change is rolled back, with an error that starts with `gsn_guard:`, if it:

- drops, renames, moves, retypes or disables a registered table, column, trigger or function, or
  replaces one (`CREATE OR REPLACE`, `ALTER FUNCTION`, also for a trigger that keeps its name);
- **rewrites** a protected table: `ALTER COLUMN ... TYPE ... USING <expression>` (even to the same
  type), a column with a volatile default (`DEFAULT random()`), `SET UNLOGGED`. A rewrite changes
  every row without firing a row trigger, which no after-the-fact check could notice;
- adds a **trigger** to a protected table (a trigger that fires after a guard could change a row the
  guard had approved), gives it a rewrite rule or row-level security, or makes it part of an
  **inheritance** tree (rows of a child skip the parent's guards yet show up in plain reads);
- changes who may execute a protected function (`GRANT` / `REVOKE`), its owner, language,
  strictness, settings or body.

Not affected: adding columns (with no default or a constant one), indexes, views and unrelated
tables, a scratch table that references a protected table (foreign-key triggers are internal),
scratch copies of a protected table (`CREATE TABLE ... AS SELECT`), and writes to `audit_chain`.

- `db/init/20-guard.sh` creates the watchdog (schema `gsn_guard`: a registry, two registration
  functions and three event triggers, on `ddl_command_end`, `sql_drop` and `table_rewrite`) when the
  database is first created, and moves the database itself to the superuser, so that `gsn_migrate` can
  no longer create schemas or temporary objects.
- `db/migrations/0004_guard_registration.sql` registers the protected tables, their guard triggers and
  the guard functions. A later migration that adds a guard ends with
  `SELECT gsn_guard.protect_table('public.new_table', ARRAY['new_table_guard_row']);` or
  `SELECT gsn_guard.protect_function('public.new_function(bigint)');`. A table is registered with
  **all** of its triggers at once, and registration refuses a table with an unlisted trigger, so
  create the triggers first and register last. There is no "unregister" and no "add a trigger".
  A function that lives in `public` pins `SET search_path = pg_catalog, pg_temp` (never `public`:
  `gsn_migrate` owns that schema, and a look-alike function created there would win the call). Every
  function in `public` does, including `audit_log_reject_change()` from the applied migration `0001`,
  which can no longer be edited: migration `0004` pins it (and closes it to PUBLIC) just before it
  registers it.
- Look at the registry with `SELECT kind, name, parent FROM gsn_guard.registry;` as `gsn_migrate`.
- **One-time upgrade.** `db/init/` runs only on an empty volume, so a local database created before the
  watchdog existed does not have it, and migration `0004` stops with "database predates the M1 guard".
  Recreate it once with `docker compose down -v` (the local database holds no real data) and then
  `docker compose up -d`. The same migration also stops if the watchdog is only partly there (one of
  its three event triggers missing or disabled).

**Break-glass: changing a guard on purpose.** This needs the superuser, which is reachable only from
a shell on the server (on this PC: `docker compose exec postgres psql -U postgres -d gsn`; the
connection from inside the container needs no password). `gsn_migrate` cannot do it, by design, so a
migration can never change a registered object. As the superuser:

```sql
SET event_triggers = off;      -- this session only: the watchdog does not check what follows
-- 1. make the change, for example CREATE OR REPLACE FUNCTION public.releases_guard() ...
-- 2. bring the registry up to date (otherwise the NEXT schema change anyone makes is rolled back):
UPDATE gsn_guard.protected_functions
   SET fingerprint = gsn_guard.function_fingerprint(to_regprocedure(signature))
 WHERE signature = 'public.releases_guard()';
UPDATE gsn_guard.protected_triggers AS g
   SET definition = gsn_guard.trigger_definition(t.oid)
  FROM pg_trigger AS t
 WHERE t.tgrelid = to_regclass(g.table_name) AND t.tgname = g.trigger_name
   AND g.table_name = 'public.releases' AND g.trigger_name = 'releases_guard_row';
-- a changed column list or a new trigger: delete the table's rows from gsn_guard.protected_triggers and
-- gsn_guard.protected_tables, then SELECT gsn_guard.protect_table('public.releases', ARRAY[...]);
-- (list ALL of its triggers). A changed GRANT on a protected function is picked up by the
-- function_fingerprint UPDATE above.
INSERT INTO audit_log (actor, action, target, details)   -- keep a record of why
VALUES ('owner', 'guard.break-glass', 'public.releases_guard()', '{"why": "..."}');
RESET event_triggers;
CREATE TABLE gsn_guard_probe (id int); DROP TABLE gsn_guard_probe;   -- passes when the registry is right
```

The superuser itself is out of scope of the watchdog: nothing in this repository can protect against
someone who holds it, which is why it is reachable only from the server console.

### Full reset, including the database

```powershell
docker compose down -v
```

`-v` also deletes the `pgdata` volume and the `gsn-models` volume (the 2.3 GB embedding model, see
"Model download"; use plain `docker compose down` when you only want to stop the stack and keep the
model). Do this after changing anything in `db/init/` or any database
password in `.env`, because PostgreSQL runs the init scripts (`db/init/10-roles.sh`, then
`db/init/20-guard.sh`) only on an empty volume. All data in the database is lost. (The guard watchdog
arrived after the first local databases were made: recreate an older one once with
`docker compose down -v`, see "The guard watchdog".)

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

The Python command finds its settings in `engine/pyproject.toml` (pytest looks for the first
configuration file above the first argument that has one; `deploy/tests` and `tools/tests` have none
of their own). Those settings include `--import-mode=importlib`, so test files in different folders may
share a file name (as `engine/tests/test_store.py` and `tests/integration/test_store.py` do);
`deploy/tests/test_repo_hygiene.py` guards it. Keep `engine/tests` among the arguments when you run
the other two folders together, or add `--import-mode=importlib` yourself.

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
| Python, release image (`engine/constraints-ml.txt`) | see the note below: the `+cpu` label must be removed first |
| Frontend, in each of `frontend\public` and `frontend\admin` | `npm audit --audit-level=high` |
| Rust (`web/Cargo.lock`) | `cargo audit` in CI; see below for running it by hand |

Notes:

- **Python:** the audit reads the lock file, not the packages installed in `.venv`, so it does not
  report the `pip` that ships with Python.
- **Python, ML tree:** the advisory database knows torch as `2.13.0`, not as `2.13.0+cpu`, so CI strips
  the label before auditing (`sed 's/+cpu//' engine/constraints-ml.txt > /tmp/ml.txt`). By hand, in
  PowerShell:

  ```powershell
  (Get-Content engine\constraints-ml.txt) -replace '\+cpu', '' | Set-Content -Encoding ascii $env:TEMP\ml.txt
  .\.venv\Scripts\python -m pip_audit -r $env:TEMP\ml.txt --no-deps --disable-pip --progress-spinner off
  ```
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
and fails if a new port is published, a service other than the five plus the one-shot `migrate` and
`release` jobs appears, either job gets a restart policy or a port, `release` loses its `tools`
profile or mounts anything but the model volume and the repository's `.git` folder read-only, an image
is not pinned to an exact version, a service loads an `env_file`, or a service receives another
service's database password (or the superuser password). It also checks that `web` waits for a healthy database and the finished
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

The `itest` image contains a copy of the `gsn` package, and `docker compose run` reuses an image it
already has, so after changing anything under `engine\src` rebuild it first (a plain `docker compose
build` and `--profile tools build` skip it, because `itest` belongs to the `test` profile; CI builds
it fresh):

```powershell
docker compose -f compose.yaml -f compose.test.yaml build itest
```

They check that each of the four database roles can log in and has exactly the rights it should
(`gsn_web` reads and appends `audit_log`, `gsn_worker` only appends, `gsn_engine` has no access, no
role except the owner can create tables). `tests/integration/test_grants.py` lists the database
catalog and compares, against one table of expectations, the table, column and sequence privileges and
the EXECUTE right that `gsn_migrate`, `gsn_web`, `gsn_worker`, `gsn_engine` and PUBLIC hold on every
table, view, sequence and function in the `public` schema (an object nobody listed is expected to be
closed to everybody but its owner, so a stray `GRANT` in a later migration fails there); that
`gsn_migrate` owns all of them; that no ACL is left at its default or has an entry for PUBLIC; that
every function pins its `search_path`; and the rights on the database and the `public` schema. It does
not cover the `gsn_guard` schema (`test_guard.py`), types, or default privileges for objects created
later. Its last tests break a rolled-back copy of the database on purpose to prove that the comparison
notices. The same stack tests also check that callers can insert only `actor`, `action`,
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

## Releases (the data in PostgreSQL)

A **release** is one complete, frozen copy of the knowledge base: every record of every live service,
with its vector, as it was in one commit of this repository. Nothing in a release is ever edited. A
change to the data is a new commit, then a new release; the chatbot (from milestone M2) answers from
the one release that is *active*, and you can go back to the previous one in a second. A release is
`draft` while it is being built, then `validated` (it passed the checks) or `rejected` (it did not),
and only a validated release can be published.

`gsn` runs these commands in the one-off `release` container (compose profile `tools`: a plain
`docker compose up` never starts it). Build its image once, and again after any change in
`engine\src`; the stack must be running (`docker compose up -d`), because the container waits for the
finished migrations and talks to `postgres`:

```powershell
docker compose --profile tools build
```

Every command has the form `docker compose --profile tools run --rm -T release release <command>` (the
container's entry point is `gsn`, so the first `release` is the compose service and the second is the
`gsn` subcommand; `-T` switches off the pseudo-terminal, which keeps the output clean when you save or
pipe it). The commands:

| Command | What it does |
|---|---|
| `release build [--commit REF] [--note TEXT] [--override REASON]` | Reads the inputs of a commit (default `HEAD`; a branch, tag or short SHA also works), checks them, embeds the new texts, writes a draft, runs the gold queries against it and validates or rejects it. Prints `release_id=<n> status=<status>` first, then a summary. |
| `release publish ID [--allow-fake]` | Makes a validated release the active one. |
| `release rollback` | Undoes the latest publish or rollback (the previous active release becomes active again). |
| `release list` | One line per release; the active one is marked `yes`. |
| `release show ID` | The release as JSON, with its gate report and the records added, removed and changed compared with the active release (changed: the release holds a different version of the record, which includes a changed derivation such as a new `verified_at_field` mapping, not only edited content). |
| `eval --release ID [--json]` | Runs the gold queries against any existing release (queries are read from that release's own commit). |

For example, a build, a look at what changed, and the switch (use the number `build` printed):

```powershell
docker compose --profile tools run --rm -T release release build --note "after the August crawl"
docker compose --profile tools run --rm -T release release show 5
docker compose --profile tools run --rm -T release release publish 5
```

Things to know:

- **Only committed content is read.** The container sees the repository's `.git` folder, read-only,
  and never the working tree, so a file you have not committed is not in the release, and neither are
  edits you have made but not committed. Commit first, then build. `GSN_REPO_GIT` can point the
  mount at another `.git` folder. The build refuses to start (exit 2, with the paths) if any commit in
  that history ever contained a `setenv*.bat` or `.env` file, because those hold secrets.
- **Building the same commit again is cheap.** Versions and vectors that are already stored are
  reused (the summary shows `embedded: 0`), so a rejected build can be fixed and repeated without
  re-embedding anything. A stored version is identified by the record's content *and* by the columns
  derived from it (the verification date and its manifest mapping, the link kind, the official and
  verified flags). If only that derivation changes, for example you map `verified_at_field` for a
  service, its records get new versions but the vectors are copied, so still nothing is embedded.
- **Rejected releases.** If any gold query fails that is not listed under `known_failures` in
  `data/release_thresholds.json`, the release is `rejected`. Fix the data and build again, or add
  `--override "why this is fine"` after looking at the failures: the reason is stored on the release
  and in the audit log (`release.override`). `--override` has no effect when nothing failed (the
  summary then says `override not needed` and no reason is recorded).
- **Audit trail.** Every build writes `release.build` and then one of `release.validate`,
  `release.reject` or `release.override`; publish and rollback write their own rows. The draft and its
  `release.build` row are saved together, so a release never exists without its build row. The actor is
  `GSN_ACTOR` (default `owner`); set it in `.env` or the shell to record who ran a command.
- **Checking the audit log.** Every audit row carries the hash of the row before it, so a changed,
  removed or forged row breaks the chain. Check it with:

  ```powershell
  docker compose --profile tools run --rm -T release audit verify
  ```

  It prints `audit chain ok: <n> rows checked` and exits 0, or `audit chain BROKEN at chain_seq <n>`
  and exits 1. It runs through a database function, so the worker can verify the log without being
  able to read it.
- **The real embedding model** (BAAI/bge-m3, about 2.3 GB) is what `build`, `eval` and `parity` use
  when `GSN_EMBEDDER` is not set. Download it once first (see "Model download" below); without it the
  command stops with exit code 2 and tells you how. For tests and smoke runs, `-e GSN_EMBEDDER=fake`
  selects a stand-in whose vectors carry **no meaning**:

  ```powershell
  docker compose --profile tools run --rm -T -e GSN_EMBEDDER=fake release release build --commit HEAD --override "local smoke"
  docker compose --profile tools run --rm -T release release list
  ```

  A release built that way is stamped `fake-sha256-v1`, its gold queries fail (hence `--override`), and
  `publish` refuses it unless you add `--allow-fake`. `GSN_EMBEDDER=fake` is for tests only; never
  publish such a release on a server that serves real users. `docker compose down -v` wipes the
  smoke data along with the rest of the local database.

**Exit codes** (scripts and CI read these):

| Code | Meaning |
|---|---|
| 0 | Success (a build that is `validated`, or any other command that worked). |
| 1 | Runtime error: the database could not be reached or a statement failed (one `error: database: ...` line), or a bug (a traceback is printed). |
| 2 | Usage or configuration error, or a refusal: a bad argument or ref, a missing `GSN_DB_*` variable, an embedder that cannot be made or does not match the release, the embedding model not downloaded yet, secret files in the repository history, or the database said no (`release 7 is not validated`, `nothing to roll back`). The message is printed on stderr. |
| 3 | The build finished but the release is `rejected` (for `eval`: the gate would reject). |
| 4 | Integrity problems in the inputs: the full list is printed and **nothing was written**, not even an audit row. |

### Model download

The real model, BAAI/bge-m3 (about 2.3 GB), is not part of the image. It lives in the `gsn-models`
volume, mounted at `/models` (`HF_HOME`), and the `release` container is offline
(`HF_HUB_OFFLINE=1`) so that a build can never fetch anything by surprise: whatever that variable says,
the embedder always loads the model from the files in the volume and never reaches the hub, and `build`
checks that all ten files are there before it writes anything. Download the model once, with the one
command that switches the hub on:

```powershell
docker compose --profile tools run --rm -T --no-deps -e HF_HUB_OFFLINE=0 release models fetch
```

(`--no-deps` stops Compose from starting the database and the migration job, which the `release` service
depends on, for a download that needs neither.)

It downloads exactly the ten files of BAAI/bge-m3 at the pinned revision (`5617a9f6...`, the one
the ChromaDB baseline was made with; the repository's other formats are skipped) and prints the
snapshot folder, relative to the cache, and the total size in bytes, for example
`snapshot: models--BAAI--bge-m3/snapshots/5617a9f6...`. Running it again downloads nothing that is
already there, and an interrupted download is completed by running it again. While the hub is switched
off (`HF_HUB_OFFLINE` or `TRANSFORMERS_OFFLINE` set to 1, on, yes or true) the command refuses (exit 2)
and prints the command to use.

Things to know:

- It needs about 2.3 GB free on the Docker drive (`G:`) and an internet connection; nothing else in the
  project uses the network at build time.
- `docker compose down` keeps the volume and `docker compose down -v` deletes it together with the
  database, so after a full reset run the command again.
- If a `build`, `eval` or `parity` finds the model missing or incomplete, it stops with exit 2 and one
  line: `error: the embedding model is not in the model cache; download it once with: docker compose
  --profile tools run --rm --no-deps -e HF_HUB_OFFLINE=0 release models fetch`. For `build` this happens
  before anything is written, even when every vector would have been reused.
- To check that the model loads and what it produces (prints the model id and the first four numbers
  of each vector; the sentences are arbitrary):

  ```powershell
  docker compose --profile tools run --rm -T --entrypoint python release -c "from gsn.embed import get_embedder; e = get_embedder({}); print(e.model_id); [print(len(v), v[:4]) for v in e.encode(['How do I renew my passport?', 'amar birth certificate hariye gese'])]"
  ```

### Real-model evals

Ordinary CI (`ci.yml`) builds its test release with the fake embedder, whose vectors carry no meaning, so
it cannot tell whether retrieval still works. The workflow `.github/workflows/evals.yml` ("Evals (real
model)") does: it builds a release of the checked-out commit with the real BAAI/bge-m3 model and requires
the release gate to pass, that is, every gold query passes except the ones listed under `known_failures`
in `data/release_thresholds.json`.

- **When it runs.** On a pull request, and on a push to `master`, that changes anything the gate
  measures: the `gsn` package (`engine/src/gsn/`, except its `engine/` and `worker/` apps, which the gate
  does not use), `engine/Dockerfile`, `engine/constraints.txt`, `engine/constraints-ml.txt`,
  `engine/pyproject.toml`, `db/migrations/`, `compose.yaml`, `data/`, the `*_gold_queries.json` files,
  `concept_registry.json`, the ChromaDB baseline in `docs/reports/m1-parity/` or the workflow itself;
  and by hand from the Actions tab ("Run workflow"), which GitHub offers once the workflow is on
  `master`. Pull requests and pushes use the same list (a test in `deploy/tests/test_repo_hygiene.py`
  keeps them equal), so a change that does not touch these paths, such as an edit of this guide, does
  not download or rebuild anything.
- **What it does.** Builds the images, starts PostgreSQL and applies the migrations, downloads the model
  if the cache has none, runs `release build --commit HEAD` (the job fails unless the release is
  `validated`), runs `eval --release <id> --json` for the release it just built, and finishes with
  `audit verify`. It then throws the database away. Nothing is published.
- **The model download.** The first run downloads about 2.3 GB from the Hugging Face hub and saves it in
  the GitHub Actions cache under the key `bge-m3-<revision>`; later runs restore it instead. GitHub
  deletes a cache nobody used for 7 days, so an idle repository downloads again. The key names the
  pinned revision: when `MODEL_REVISION` in `engine/src/gsn/embed.py` changes, change `MODEL_CACHE_KEY`
  in the workflow too (a test in `deploy/tests/test_repo_hygiene.py` fails until you do).
- **Who can use the cache.** A cache created by a pull request run can only be read by runs of that
  same pull request, so every new pull request downloads the model once, until a run on `master` has
  saved it (a push to `master` that touches the paths above, such as the merge of the first pull
  request). Runs on pull requests can always read what `master` saved. The repository's caches share a
  10 GB limit with the Rust build cache (`rust-cache` in `ci.yml`) and the pip and npm caches; when it is
  full, GitHub deletes the least recently used caches first, and the model (about 2.3 GB) can be among
  them, in which case the next run downloads it again.
- **Where the results are.** The build summary (verdict counts per service, known and unexpected
  failures) is on the run's summary page. The artifact `release-eval` holds `build.log` and `eval.json`
  (the result of every gold query), kept for 30 days.

To run the same steps on your own machine, commit your changes first (a release is built from a commit,
never from the working tree) and start the stack. The model lives in the `gsn-models` volume here, so no
`-v` is needed:

```powershell
docker compose --profile tools build
docker compose up -d
docker compose --profile tools run --rm -T --no-deps -e HF_HUB_OFFLINE=0 release models fetch   # once, about 2.3 GB
docker compose --profile tools run --rm -T release release build --commit HEAD
docker compose --profile tools run --rm -T release eval --release 5 --json   # use the number build printed
docker compose --profile tools run --rm -T release audit verify
```

The workflow builds on a fresh database; a local database may already hold releases, so the id that
`build` prints is not necessarily 1.

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

## ML constraints (`engine/constraints-ml.txt`)

The `release` image has what runs the embedding model: `sentence-transformers`, CPU-only `torch` (from
the PyTorch CPU index, `https://download.pytorch.org/whl/cpu`, so no CUDA libraries) and everything they
pull in. They are the `ml` extra of `engine/pyproject.toml`, and `engine/constraints-ml.txt` locks what
the extra adds on top of the runtime image: every package of the ML tree that the engine's runtime
dependencies do not already bring. (A few of them, such as `packaging` and `filelock`, are also in
`engine/constraints.txt`, because the dev tree needs them; the versions are always the same.) The
Dockerfile has a separate `ml` stage that mounts only this file and installs it with
`pip install --no-deps -r constraints-ml.txt` (pip resolves nothing at build time). The `release` stage is
built on it: it installs the engine the way the runtime stage does, under both lock files, adds git and
`/models`, and runs `pip check`, which fails the build if anything is missing. Because the `ml` stage sees
nothing but the lock file, editing engine code rebuilds only the small `release` layers, not the 1.7 GB
ML layer (BuildKit prints `CACHED` for it); it is rebuilt only when `constraints-ml.txt` changes. The
Windows `.venv` does not install `[ml]` (drive `C:` is small); unit tests there use the fake embedder.

The seven pins in the extra (`torch`, `sentence-transformers`, `transformers`, `tokenizers`,
`safetensors`, `numpy`, `huggingface-hub`) are the versions of the ChromaDB baseline's environment
(`docs/reports/m1-parity/chroma_baseline.json`), because the vectors a release stores are compared with
the baseline's. `engine/tests/test_constraints.py` fails if the file drifts from the baseline or from the
extra, and Dependabot ignores these libraries. They change only at the M2 engine switch, together with a
new baseline and a parity re-run.

The file cannot come from the Windows venv: it is generated inside a throw-away Linux container with the
same Python as the image (`python:3.12.14-slim-bookworm`, the tag of `engine/Dockerfile`). It needs the
internet (PyPI and download.pytorch.org), downloads a few hundred MB that are discarded with the
container, and takes a few minutes. From the repository root, in PowerShell (`docker` on `PATH`, see
Prerequisites):

```powershell
@'
import pathlib
import re
import shutil
import subprocess

INDEX = "https://download.pytorch.org/whl/cpu"
HEADER = [
    "# Exact versions of everything the engine's `ml` extra needs that the runtime image does not already",
    "# install: sentence-transformers, CPU-only torch and their dependencies. (A few of them, such as",
    "# packaging, are also in constraints.txt because the dev tree needs them; the versions are the same.)",
    "# The release image installs this file with --no-deps (Dockerfile target `release`), so it must list",
    "# the whole difference. Do not edit by hand; regenerate it inside a Linux container as described in",
    "# docs/DEVELOPMENT.md (section ML constraints). The pins of torch, sentence-transformers, transformers,",
    "# tokenizers and numpy are the versions the ChromaDB baseline was made with",
    "# (docs/reports/m1-parity/chroma_baseline.json); torch carries the +cpu label of the PyTorch CPU index.",
]


def run(*command, **options):
    return subprocess.run(command, check=True, **options)


def installed(name, *pip_args):
    """Install the engine as the Docker stages do (constraints.txt only) and return its frozen pins by name."""
    shutil.copytree("/engine", f"/tmp/{name}", ignore=shutil.ignore_patterns("tests", "__pycache__", "*.egg-info"))
    run("python", "-m", "venv", f"/tmp/{name}-venv")
    pip = f"/tmp/{name}-venv/bin/pip"
    run(pip, "install", "--no-cache-dir", "-c", "/engine/constraints.txt", "--extra-index-url", INDEX, *pip_args)
    frozen = run(pip, "freeze", "--exclude-editable", stdout=subprocess.PIPE, text=True).stdout.split()
    return {re.sub(r"[-_.]+", "-", line.split("==")[0]).lower(): line for line in frozen}


runtime = installed("runtime", "/tmp/runtime")
with_ml = installed("ml", "-e", "/tmp/ml[ml]")
pins = sorted((line for name, line in with_ml.items() if name not in runtime), key=str.lower)
pathlib.Path("/engine/constraints-ml.txt").write_text("\n".join(HEADER + pins) + "\n", encoding="ascii", newline="\n")
print(len(pins), "packages written to engine/constraints-ml.txt")
'@ | docker run --rm -i -e PIP_DISABLE_PIP_VERSION_CHECK=1 --mount "type=bind,src=$PWD\engine,dst=/engine" python:3.12.14-slim-bookworm python -
```

What the program does: it installs the engine twice in the container, each time into a fresh virtual
environment and under `engine/constraints.txt`, the way the Docker stages do: once as the runtime image
has it, once with the `ml` extra (the pins in the extra force the baseline's versions). It lists both
with `pip freeze --exclude-editable`, keeps the packages that only the second environment has, sorts them
without regard to case and writes them as plain ASCII under a comment header. The only thing it changes
in your checkout is `engine/constraints-ml.txt`, through the read-write mount. If pip cannot satisfy one
of the seven pins on Linux the command fails and nothing is written: stop and find out why before
changing a pin.

Then run `.\.venv\Scripts\python -m pytest engine/tests/test_constraints.py -q`, check
`git diff engine/constraints-ml.txt`, and rebuild the image with
`docker compose --profile tools build release` (its `pip check` must stay clean).

## Dependency updates (Dependabot)

Once a week GitHub's Dependabot (configured in `.github/dependabot.yml`) looks for newer versions of
the libraries and base images this project uses and opens a pull request for each; the small
(minor and patch) updates of one app or of the engine arrive together as one grouped pull request,
while major updates come one at a time. These are ordinary pull requests: nothing merges by itself,
and one is merged only after CI is green and you have approved it. Some updates are ignored on
purpose, because the toolchains are pinned deliberately and a robot bumping them would either break
CI or quietly change what runs on the server (the ML libraries of the `ml` extra are ignored too, see
"ML constraints"): new Python minor/major versions (we stay on 3.12;
patch releases such as 3.12.11 are still proposed), new Node majors (we run 24), any `rust` image
update, Postgres (pgvector) and Caddy majors (a Postgres major needs a dump and restore of the
data), and `@types/node` majors (they must match the Node major we actually run). A deliberate
toolchain upgrade is done by you, by hand, as one pull request that changes every place together:
the toolchain file (`web/rust-toolchain.toml`, `requires-python` in `engine/pyproject.toml`, or
`engines.node` in both `package.json` files), the image tags in `engine/Dockerfile`,
`web/Dockerfile` and `compose.yaml`, the versions in `.github/workflows/ci.yml`, the tests that
tie them together, and `engine/constraints.txt` regenerated (see "Python constraints"). The
same regeneration is needed after you accept a Dependabot pull request for a Python package:
Dependabot edits only `engine/pyproject.toml`, so check out that branch, regenerate
`engine/constraints.txt`, and push it to the pull request before merging.

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
