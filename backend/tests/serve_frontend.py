"""E2E lifecycle: isolated real PostgreSQL + FastAPI + built frontend."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

from pg_cluster import temporary_postgres


def main():
    frontend = Path(sys.argv[1]).resolve()
    with temporary_postgres() as database_url, tempfile.TemporaryDirectory(prefix='wellio-e2e-uploads-') as uploads:
        env = {**os.environ, 'DATABASE_URL': database_url, 'WELLIO_ATTACHMENTS_PATH': uploads, 'EXA_API_KEY': '', 'OPENROUTER_API_KEY': '', 'WELLIO_AI_MODEL': '', 'WELLIO_BACKEND_DIR': str(Path(__file__).resolve().parents[1])}
        child = subprocess.Popen(['node', 'scripts/run-stack.mjs', 'start'], cwd=frontend, env=env)
        def stop(_signum, _frame):
            if child.poll() is None:
                child.terminate()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, stop)
        try:
            return child.wait()
        finally:
            stop(None, None)
            try:
                child.wait(timeout=6)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()


if __name__ == '__main__':
    sys.exit(main())
