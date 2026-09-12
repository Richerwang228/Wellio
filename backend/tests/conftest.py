from __future__ import annotations

import os
import struct
from contextlib import ExitStack
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
import zlib

import pytest
import psycopg
from psycopg import sql
from fastapi.testclient import TestClient
from pg_cluster import temporary_postgres


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for name in (
        "WELLIO_AI_MODEL", "OPENROUTER_API_KEY",
        "DATABASE_URL", "WELLIO_EXA_API_KEY", "EXA_API_KEY",
        "WELLIO_PUBLIC_ORIGIN", "WELLIO_COOKIE_SECURE",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def postgres_url():
    if external := os.getenv("WELLIO_TEST_DATABASE_URL"):
        yield external
    else:
        with temporary_postgres() as url:
            yield url


@pytest.fixture
def database_url(postgres_url):
    """Use only a fresh schema, even when an external test database is supplied."""
    schema = "wellio_test_" + uuid4().hex
    with psycopg.connect(postgres_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    parts = urlsplit(postgres_url)
    parameters = dict(parse_qsl(parts.query, keep_blank_values=True))
    parameters["options"] = (parameters.get("options", "") + f" -csearch_path={schema}").strip()
    url = urlunsplit(parts._replace(query=urlencode(parameters)))
    try:
        yield url
    finally:
        with psycopg.connect(postgres_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


@pytest.fixture
def database_path(database_url):
    """Transitional fixture name for domain tests; the value is a PostgreSQL URL."""
    return database_url


@pytest.fixture
def client_factory(tmp_path, database_url):
    from wellio.app import create_app

    with ExitStack() as stack:
        def create(url=None, attachments_path=None, **options):
            app = create_app(
                database_url=url or database_url,
                attachments_path=attachments_path or tmp_path / "attachments",
                **options,
            )
            return stack.enter_context(TestClient(app, base_url="http://testserver", raise_server_exceptions=False))
        yield create


@pytest.fixture
def client(client_factory, database_path):
    return client_factory(database_path)


@pytest.fixture
def database(database_path):
    from wellio.database import Database

    db = Database(database_path)
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def png_bytes():
    def chunk(kind: bytes, content: bytes) -> bytes:
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)

    # Fully decodable 2-by-2 red RGB PNG with valid CRCs, not just a signature/header.
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 2) * 2))
        + chunk(b"IEND", b"")
    )
