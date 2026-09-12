"""Real PostgreSQL failure recovery without replaying uncertain writes."""
import asyncio
from contextlib import closing, contextmanager
from pathlib import Path
from threading import Event
import time
from urllib.parse import urlsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from pg_cluster import temporary_postgres, _postgres_bin, _run
from wellio.app import create_app
from wellio.database import Database
from wellio.errors import BackendError


def request(snapshot):
    return {'kind': 'set_locale', 'requestId': 'recover-locale',
            'resetEpoch': snapshot['resetEpoch'], 'source': 'profile', 'locale': 'zh-CN'}


def locale_outcome(snapshot):
    snapshot.update(locale='zh-CN', revision=snapshot['revision'] + 1)
    return {'httpStatus': 200, 'result': {'status': 'succeeded'}, 'snapshot': snapshot}


def terminate_connection(database_url, connection):
    with psycopg.connect(database_url, autocommit=True) as admin:
        assert admin.execute('SELECT pg_terminate_backend(%s)', (connection.info.backend_pid,)).fetchone()[0]


def test_terminated_connection_recovers_on_next_http_request_with_same_cookie(client, database_url):
    initial = client.get('/api/state').json()
    database = client.app.state.database
    original_key = database.signing_key
    original_cookie = client.cookies.get('wellio_session')
    terminate_connection(database_url, database.connection)

    failed = client.get('/api/state')
    assert failed.status_code == 503
    assert failed.json()['errorCode'] == 'DATABASE_UNAVAILABLE'
    recovered = client.get('/api/state')
    assert recovered.status_code == 200
    assert recovered.json() == initial
    assert database.signing_key == original_key
    assert client.cookies.get('wellio_session') == original_cookie
    assert client.get('/healthz').status_code == 200


def test_terminated_transaction_rolls_back_and_callback_is_not_replayed(database, database_url):
    initial = database.create_session()
    calls = []

    def interrupted(snapshot):
        calls.append('called')
        terminate_connection(database_url, database.connection)
        return locale_outcome(snapshot)

    with pytest.raises(BackendError, match='DATABASE_UNAVAILABLE'):
        database.mutate(initial['sessionId'], request(initial), interrupted)
    assert calls == ['called']
    assert database.get_snapshot(initial['sessionId']) == initial
    assert database.get_mutation_reply(initial['sessionId'], request(initial)) is None
    assert database.mutate(initial['sessionId'], request(initial), locale_outcome)['result']['status'] == 'succeeded'


def test_lost_commit_acknowledgement_recovers_receipt_without_replaying_callback(database):
    initial = database.create_session()
    calls = []

    class LostCommitAcknowledgement:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        @contextmanager
        def transaction(self):
            with self.connection.transaction():
                yield
            # The real transaction has committed. Only its acknowledgement is
            # unavailable to the repository/caller.
            self.connection.close()
            raise psycopg.OperationalError('injected lost commit acknowledgement')

    def change(snapshot):
        calls.append('called')
        return locale_outcome(snapshot)

    database.connection = LostCommitAcknowledgement(database.connection)
    with pytest.raises(BackendError, match='DATABASE_UNAVAILABLE'):
        database.mutate(initial['sessionId'], request(initial), change)
    assert calls == ['called']
    replay = database.mutate(initial['sessionId'], request(initial), change)
    assert calls == ['called']
    assert replay['result']['snapshot']['revision'] == initial['revision'] + 1
    assert database._one('SELECT count(*) AS count FROM action_requests')['count'] == 1


def test_sql_timeout_is_bounded_and_next_request_recovers(database_url):
    with closing(Database(database_url, statement_timeout_ms=200)) as database:
        initial = database.create_session()
        started = time.monotonic()
        with pytest.raises(BackendError, match='DATABASE_TIMEOUT'):
            database._one('SELECT pg_sleep(3)')
        assert time.monotonic() - started < 1.5
        assert database.get_snapshot(initial['sessionId']) == initial
        assert database._one('SHOW statement_timeout')['statement_timeout'] == '200ms'


def test_database_row_lock_timeout_does_not_write_or_replay(database_url):
    with closing(Database(database_url, sql_lock_timeout_ms=100)) as database:
        initial = database.create_session()
        calls = []

        def change(snapshot):
            calls.append('called')
            return locale_outcome(snapshot)

        with psycopg.connect(database_url) as blocker:
            blocker.execute('SELECT id FROM sessions WHERE id=%s FOR UPDATE', (initial['sessionId'],))
            started = time.monotonic()
            with pytest.raises(BackendError, match='DATABASE_TIMEOUT'):
                database.mutate(initial['sessionId'], request(initial), change)
            assert time.monotonic() - started < 1.5
        assert calls == []
        assert database.get_snapshot(initial['sessionId']) == initial
        database.mutate(initial['sessionId'], request(initial), change)
        assert calls == ['called']


def test_health_reports_busy_instead_of_waiting_forever(client):
    database = client.app.state.database
    database.lock_timeout_seconds = 0.05
    with database._lock:
        started = time.monotonic()
        response = client.get('/healthz')
        assert time.monotonic() - started < 1
    assert response.status_code == 503
    assert response.json()['errorCode'] == 'DATABASE_BUSY'
    assert client.get('/healthz').status_code == 200


def test_health_tracks_owned_postgres_stop_and_restart(tmp_path):
    # Own this entire cluster: never stop the shared/dedicated test database or
    # any development service, even when WELLIO_TEST_DATABASE_URL is provided.
    with temporary_postgres() as url:
        with TestClient(create_app(url, tmp_path / 'attachments')) as client:
            initial = client.get('/api/state').json()
            database = client.app.state.database
            data = Path(database._one('SHOW data_directory')['data_directory'])
            assert str(data).startswith('/tmp/wellio-pg-')
            binary = str(_postgres_bin() / 'pg_ctl')
            _run([binary, '-D', str(data), '-m', 'fast', '-w', '-t', '10', 'stop'])
            started = time.monotonic()
            assert client.get('/healthz').status_code == 503
            assert client.get('/healthz').status_code == 503
            assert client.get('/api/state').status_code == 503
            assert time.monotonic() - started < 8
            port = urlsplit(url).port
            _run([binary, '-D', str(data), '-l', str(data.parent / 'postgres.log'),
                  '-o', f'-h 127.0.0.1 -p {port} -k {data.parent / "sockets"} -F',
                  '-w', '-t', '10', 'start'])
            assert client.get('/healthz').status_code == 200
            assert client.get('/api/state').json() == initial


def test_readiness_exposes_missing_dependencies_without_disabling_fact_api(client):
    response = client.get('/readyz')
    assert response.status_code == 503
    checks = response.json()['checks']
    assert checks['database']['status'] == 'ok'
    assert checks['agent']['status'] == 'unconfigured'
    assert checks['search']['status'] == 'unconfigured'
    assert checks['knowledge']['errorCode'] == 'KNOWLEDGE_NOT_READY'
    assert client.get('/healthz').status_code == 200
    assert client.get('/api/state').status_code == 200


@pytest.mark.parametrize('operation', ['open', 'finish', 'cancel', 'status'])
async def test_sync_agent_endpoints_leave_event_loop_responsive(database_url, tmp_path, monkeypatch, operation):
    app = create_app(database_url, tmp_path / 'attachments', agent_token='test-token', agent_enabled=True)
    entered, release = Event(), Event()

    def blocking(_sid, _value):
        entered.set()
        assert release.wait(timeout=2), 'event loop could not release worker'
        return {'status': 'ok'}

    monkeypatch.setattr(app.state.agent, operation, blocking)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://testserver') as client:
            assert (await client.get('/api/state')).status_code == 200
            pending = asyncio.create_task(client.post('/internal/agent/' + operation, json={}, headers={'Authorization': 'Bearer test-token'}))
            try:
                started = time.monotonic()
                assert await asyncio.to_thread(entered.wait, 1)
                assert (await client.get('/healthz')).status_code == 200
                assert time.monotonic() - started < 1
            finally:
                release.set()
                response = await pending
            assert response.status_code == 200
