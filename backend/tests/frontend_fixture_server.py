"""Real isolated FastAPI/PostgreSQL for frontend state-recovery contract tests."""
import json
import os
from pathlib import Path
import socket
import signal
import sys
import tempfile

import uvicorn

from pg_cluster import temporary_postgres
from wellio.app import create_app


def stop_fixture(_signum, _frame):
    # Uvicorn re-raises SIGTERM after graceful shutdown. Unwind the outer
    # PostgreSQL context instead of letting the default signal kill Python.
    raise SystemExit(0)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop_fixture)
    with temporary_postgres() as url, tempfile.TemporaryDirectory(prefix='wellio-client-test-') as directory:
        app = create_app(url, attachments_path=Path(directory) / 'uploads', agent_enabled='--agent-service' in sys.argv, agent_token=os.getenv('WELLIO_AGENT_TOKEN'), public_origins=tuple(filter(None, os.getenv('WELLIO_PUBLIC_ORIGIN', '').split(','))))
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(128)
            print(json.dumps({'url': f'http://127.0.0.1:{listener.getsockname()[1]}'}), flush=True)
            uvicorn.Server(uvicorn.Config(app, log_level='error')).run(sockets=[listener])
