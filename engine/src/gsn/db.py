"""Database connection settings, read from separate environment variables (never a URL).

Passing the parts as keyword arguments means a password containing ``@ / : # % ?`` needs no
escaping and cannot corrupt a connection string.
"""
from __future__ import annotations

from collections.abc import Mapping


def _required(env: Mapping[str, str], name: str) -> str:
    value = env.get(name)
    if not value:
        raise ValueError(f"{name} is required but not set")
    return value


def connect_kwargs(env: Mapping[str, str]) -> dict[str, str | int]:
    """Build ``psycopg.connect`` keyword arguments from ``GSN_DB_*`` variables.

    ``GSN_DB_USER`` and ``GSN_DB_PASSWORD`` are required; host, port and database name have defaults.
    The error text names the missing variable only, never any value.
    """
    user = _required(env, "GSN_DB_USER")
    password = _required(env, "GSN_DB_PASSWORD")
    raw_port = env.get("GSN_DB_PORT") or "5432"
    try:
        port = int(raw_port)
    except ValueError:
        raise ValueError("GSN_DB_PORT must be an integer") from None
    return {
        "host": env.get("GSN_DB_HOST") or "postgres",
        "port": port,
        "dbname": env.get("GSN_DB_NAME") or "gsn",
        "user": user,
        "password": password,
    }
