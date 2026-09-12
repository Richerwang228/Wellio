"""Transactional PostgreSQL migrations, independent of JSON snapshot versions."""
from hashlib import sha256
import os
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

DATABASE_MIGRATION_VERSION = 1
_MIGRATION_LOCK = 0x57454C4C494F0001
_MIGRATIONS = ((1, "001_initial.sql"),)


def validate_database_url(database_url):
    if not isinstance(database_url, str):
        raise ValueError("POSTGRESQL_DATABASE_URL_REQUIRED")
    try:
        parsed = urlsplit(database_url)
        valid = parsed.scheme in ("postgresql", "postgres") and parsed.hostname and parsed.path not in ("", "/") and not parsed.fragment
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("POSTGRESQL_DATABASE_URL_REQUIRED")
    return database_url


def migrate(connection):
    """Serialize schema initialization across workers; no partially applied DDL."""
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK,))
        connection.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            checksum TEXT NOT NULL,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        with connection.cursor(row_factory=dict_row) as cursor:
            applied = {row["version"]: row for row in cursor.execute("SELECT version, name, checksum FROM schema_migrations ORDER BY version")}
        if any(version > DATABASE_MIGRATION_VERSION for version in applied):
            raise ValueError("DATABASE_SCHEMA_TOO_NEW")
        for version, name in _MIGRATIONS:
            sql = (Path(__file__).parent / "data" / name).read_text(encoding="utf-8")
            checksum = sha256(sql.encode("utf-8")).hexdigest()
            if version in applied:
                if applied[version]["name"] != name or applied[version]["checksum"] != checksum:
                    raise ValueError("DATABASE_MIGRATION_CHANGED")
                continue
            connection.execute(sql, prepare=False)
            connection.execute("INSERT INTO schema_migrations(version, name, checksum) VALUES (%s, %s, %s)", (version, name, checksum))
    return DATABASE_MIGRATION_VERSION


def main():
    database_url = validate_database_url(os.environ.get("DATABASE_URL"))
    with psycopg.connect(database_url, autocommit=True) as connection:
        version = migrate(connection)
    print(f"PostgreSQL migrations applied: {version}; snapshot schema: 4")


if __name__ == "__main__":
    main()
