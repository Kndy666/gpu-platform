"""Translate the configured database parameters into Django settings."""

from typing import Any
import os
from pathlib import Path


def database_settings(configuration, data_directory):
    source = dict(configuration)
    engine = source.pop("engine", source.pop("ENGINE", "sqlite"))
    aliases = {
        "sqlite": "django.db.backends.sqlite3",
        "sqlite3": "django.db.backends.sqlite3",
        "postgres": "django.db.backends.postgresql",
        "postgresql": "django.db.backends.postgresql",
    }
    engine = aliases.get(engine, engine)
    if engine not in {"django.db.backends.sqlite3", "django.db.backends.postgresql"}:
        raise ValueError("Unsupported database engine")
    result: dict[str, Any] = {"ENGINE": engine}
    for key in ["name", "user", "password", "host", "port", "options", "conn_max_age", "test"]:
        if key in source:
            result[key.upper()] = source.pop(key)
        elif key.upper() in source:
            result[key.upper()] = source.pop(key.upper())
    password_env = source.pop("password_env", None)
    if password_env:
        result["PASSWORD"] = os.environ[password_env]
    if source:
        raise ValueError("Unknown database settings: " + ", ".join(sorted(source)))
    if engine == "django.db.backends.sqlite3":
        result.setdefault("NAME", str(Path(data_directory) / "control.sqlite3"))
        if result["NAME"] != ":memory:" and not Path(result["NAME"]).is_absolute():
            result["NAME"] = str(Path(data_directory) / result["NAME"])
        result["OPTIONS"] = {"timeout": 30, "transaction_mode": "IMMEDIATE", **result.get("OPTIONS", {})}
    elif not result.get("NAME"):
        raise ValueError("PostgreSQL requires a database name")
    return result
