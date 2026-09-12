"""Owned, short-lived PostgreSQL clusters for tests and smoke scripts."""

from contextlib import contextmanager
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import tempfile
from collections.abc import Iterator


def _postgres_bin() -> Path:
    candidates = [Path(value) for key in ("WELLIO_PG_BIN", "PG_BIN") if (value := os.getenv(key))]
    pg_config = shutil.which("pg_config")
    if pg_config:
        result = subprocess.run([pg_config, "--bindir"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            candidates.append(Path(result.stdout.strip()))
    candidates.extend(Path(prefix) / "bin" for prefix in (
        "/opt/homebrew/opt/postgresql@18", "/opt/homebrew/opt/postgresql@17",
        "/opt/homebrew/opt/postgresql@16", "/opt/homebrew/opt/postgresql",
        "/usr/local/opt/postgresql@18", "/usr/local/opt/postgresql@17",
        "/usr/local/opt/postgresql", "/usr/lib/postgresql/18", "/usr/lib/postgresql/17",
        "/usr/lib/postgresql/16",
    ))
    if initdb := shutil.which("initdb"):
        candidates.append(Path(initdb).parent)
    for candidate in candidates:
        if all(os.access(candidate / executable, os.X_OK) for executable in ("initdb", "pg_ctl", "postgres")):
            return candidate
    raise RuntimeError(
        "PostgreSQL server binaries are required for integration tests. Install PostgreSQL "
        "(macOS: brew install postgresql@18; Linux: install the postgresql server package), "
        "then set WELLIO_PG_BIN to the directory containing initdb, pg_ctl and postgres. "
        "Alternatively set WELLIO_TEST_DATABASE_URL to a dedicated test database."
    )


def _run(command: list[str], *, timeout: int = 40) -> None:
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f"{Path(command[0]).name} failed: {result.stderr or result.stdout}")


@contextmanager
def temporary_postgres() -> Iterator[str]:
    """Yield a loopback test URL and stop only the cluster created by this call."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError("Temporary PostgreSQL must run as a non-root user; use a dedicated test database URL when running as root.")
    binaries = _postgres_bin()
    # /tmp keeps the Unix-domain socket path below the macOS 104-byte limit.
    temporary = tempfile.TemporaryDirectory(prefix="wellio-pg-", dir="/tmp")
    root = Path(temporary.name)
    root.chmod(0o700)
    data = root / "data"
    sockets = root / "sockets"
    sockets.mkdir(mode=0o700)
    log = root / "postgres.log"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    try:
        _run([str(binaries / "initdb"), "-D", str(data), "--auth=trust", "--no-locale", "--encoding=UTF8", "--username=wellio_test"])
        options = shlex.join(["-h", "127.0.0.1", "-p", str(port), "-k", str(sockets), "-F"])
        try:
            _run([str(binaries / "pg_ctl"), "-D", str(data), "-l", str(log), "-o", options, "-w", "-t", "25", "start"])
        except RuntimeError as error:
            details = log.read_text() if log.exists() else ""
            raise RuntimeError(f"{error}\n{details}") from error
        yield f"postgresql://wellio_test@127.0.0.1:{port}/postgres"
    finally:
        if (data / "postmaster.pid").exists():
            try:
                _run([str(binaries / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "-t", "15", "stop"], timeout=20)
            except (RuntimeError, subprocess.TimeoutExpired):
                _run([str(binaries / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "-t", "10", "stop"], timeout=15)
        temporary.cleanup()
