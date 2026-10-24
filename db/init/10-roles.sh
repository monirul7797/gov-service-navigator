#!/bin/bash
# Creates the database and its four login roles (spec section 6.1). The PostgreSQL image runs
# every script in /docker-entrypoint-initdb.d ONCE, when the data volume is empty. After changing
# this file or a DB password, reset with: docker compose down -v
#
#   gsn_migrate  owns the public schema (and creates the database; 20-guard.sh then hands the
#                database itself to the superuser); used only by `gsn-web migrate`
#   gsn_web      the public/admin web service
#   gsn_engine   the engine (read-only; no table grants in M0)
#   gsn_worker   tracker, OCR, embedding and release workers
#
# Passwords come from the environment and are handed to psql with `-v` and quoted with :'name',
# so a value containing quotes, $ or backslashes cannot break out of the SQL string.
set -euo pipefail

: "${GSN_DB_MIGRATE_PASSWORD:?GSN_DB_MIGRATE_PASSWORD is not set}"
: "${GSN_DB_WEB_PASSWORD:?GSN_DB_WEB_PASSWORD is not set}"
: "${GSN_DB_ENGINE_PASSWORD:?GSN_DB_ENGINE_PASSWORD is not set}"
: "${GSN_DB_WORKER_PASSWORD:?GSN_DB_WORKER_PASSWORD is not set}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v migrate_pw="$GSN_DB_MIGRATE_PASSWORD" \
  -v web_pw="$GSN_DB_WEB_PASSWORD" \
  -v engine_pw="$GSN_DB_ENGINE_PASSWORD" \
  -v worker_pw="$GSN_DB_WORKER_PASSWORD" <<'SQL'
CREATE ROLE gsn_migrate LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'migrate_pw';
CREATE ROLE gsn_web     LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'web_pw';
CREATE ROLE gsn_engine  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'engine_pw';
CREATE ROLE gsn_worker  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD :'worker_pw';

CREATE DATABASE gsn OWNER gsn_migrate;
REVOKE CONNECT ON DATABASE gsn FROM PUBLIC;
GRANT CONNECT ON DATABASE gsn TO gsn_migrate, gsn_web, gsn_engine, gsn_worker;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname gsn <<'SQL'
-- pgvector is not a "trusted" extension, so the superuser creates it (once, here).
CREATE EXTENSION vector;

ALTER SCHEMA public OWNER TO gsn_migrate;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO gsn_web, gsn_engine, gsn_worker;
SQL
