"""Full HTTP chain with real BuiltInAgent, an injected SDK test model, and PostgreSQL.

Run after both repositories are built. No real model or Exa requests are made.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx

from pg_cluster import temporary_postgres
from production_smoke import unused_port

BACKEND = Path(__file__).resolve().parents[1]


@contextmanager
def services(frontend, database_url, uploads, ports):
    api_port, agent_port, frontend_port = ports
    base = f'http://127.0.0.1:{frontend_port}'
    env = {**os.environ, 'DATABASE_URL': database_url, 'WELLIO_ATTACHMENTS_PATH': uploads,
           'WELLIO_API_BASE_URL': f'http://127.0.0.1:{api_port}', 'WELLIO_AGENT_BASE_URL': f'http://127.0.0.1:{agent_port}',
           'WELLIO_AGENT_PORT': str(agent_port), 'WELLIO_PUBLIC_ORIGIN': base, 'WELLIO_AGENT_TOKEN': 'test-only-private-service-token',
           'EXA_API_KEY': '', 'WELLIO_AI_MODEL': 'test-only-injected-model',
           'OPENROUTER_API_KEY': 'test-only-not-a-real-key', 'COPILOTKIT_TELEMETRY_DISABLED': 'true',
           'PORT': str(frontend_port), 'NITRO_PORT': str(frontend_port), 'HOST': '127.0.0.1', 'WELLIO_COOKIE_SECURE': '0'}
    children = []
    with tempfile.TemporaryFile(mode='w+') as output:
        try:
            commands = [
                (['uv', 'run', '--frozen', 'uvicorn', 'offline_knowledge_server:application', '--app-dir', 'tests', '--factory', '--host', '127.0.0.1', '--port', str(api_port), '--no-proxy-headers'], BACKEND, env['WELLIO_API_BASE_URL'] + '/healthz'),
                (['node', 'tests/fixture-server.mjs'], BACKEND / 'agent-runtime', env['WELLIO_AGENT_BASE_URL'] + '/healthz'),
                (['node', '.output/server/index.mjs'], frontend, base + '/today'),
            ]
            for command, cwd, ready in commands:
                child = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=output)
                children.append(child)
                for _ in range(200):
                    if child.poll() is not None:
                        output.seek(0); raise AssertionError(output.read())
                    try:
                        if httpx.get(ready, timeout=.5).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    output.seek(0); raise AssertionError(output.read())
            yield base
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()


def request_for(snapshot, message='Review my saved facts.'):
    return {'requestId': str(uuid4()), 'resetEpoch': snapshot['resetEpoch'], 'conversationId': snapshot['conversationId'],
            'locale': 'en', 'source': 'user', 'message': message, 'attachmentIds': []}


def body(request):
    return {'threadId': request['conversationId'], 'runId': request['requestId'], 'messages': [], 'state': {}, 'tools': [], 'context': [], 'forwardedProps': {'wellio': request}}


def run(client, request):
    response = client.post('/api/copilotkit/agent/wellio/run', json=body(request))
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert events[0]['type'] == 'RUN_STARTED', events
    assert events[-1]['type'] == 'RUN_FINISHED', events
    custom = [event['value'] for event in events if event['type'] == 'CUSTOM' and event['name'] == 'wellio']
    assert custom[-1]['type'] == 'done', custom
    return custom


def main(frontend):
    frontend = Path(frontend).resolve()
    with temporary_postgres() as database_url, tempfile.TemporaryDirectory(prefix='wellio-copilot-smoke-') as uploads:
        ports = [unused_port() for _ in range(3)]
        base = f'http://127.0.0.1:{ports[2]}'
        with httpx.Client(base_url=base, headers={'origin': base}, timeout=30) as first, httpx.Client(base_url=base, headers={'origin': base}, timeout=30) as second:
            with services(frontend, database_url, uploads, ports):
                snapshot = first.get('/api/state').json()
                assert snapshot['capabilities']['agent'] is True
                assert first.get('/api/copilotkit/info').status_code == 200
                other = second.get('/api/state').json()
                assert other['sessionId'] != snapshot['sessionId']
                request = request_for(snapshot)
                events = run(first, request)
                assert any(event['type'] == 'tool' and event['step']['operation'] == 'context' and event['step']['status'] == 'succeeded' for event in events), events
                assert any(event['type'] == 'tool' and event['step']['operation'] == 'knowledge_search' and event['step']['status'] == 'succeeded' for event in events), events
                saved = first.get('/api/state').json()
                complete = next(message for message in saved['messages'] if message['id'] == events[-1]['messageId'])
                assert complete['status'] == 'complete' and complete['content']
                assert saved['advice']['messageId'] == complete['id']
                run(first, request)
                assert first.get('/api/state').json()['messages'] == saved['messages']
                assert second.get('/api/state').json()['messages'] == other['messages']
                assert first.post('/api/copilotkit/agent/wellio/run', json={**body(request), 'threadId': other['conversationId']}).status_code == 400
                assert first.post('/internal/agent/open', json={'request': request}).status_code == 404
                correction = {**request_for(saved, 'I only ate half of this item'), 'targetMealId': 'meal-lunch', 'targetMealItemId': 'item-lunch'}
                corrected_events = run(first, correction)
                reads = [event for event in corrected_events if event['type'] == 'tool' and event['step']['operation'] == 'context' and event['step']['status'] == 'succeeded']
                assert len(reads) >= 2, corrected_events
                corrected = first.get('/api/state').json()
                meal = next(item for item in corrected['meals'] if item['id'] == 'meal-lunch')
                assert meal['items'][0]['consumedFraction'] == .5
                message = next(item for item in corrected['messages'] if item['id'] == corrected_events[-1]['messageId'])
                assert message['operationId'] and message['mealId'] == 'meal-lunch'
                run(first, correction)
                assert first.get('/api/state').json()['mealRevision'] == corrected['mealRevision']
                undo = first.post('/api/actions', json={'kind': 'undo_meal', 'requestId': str(uuid4()), 'resetEpoch': corrected['resetEpoch'], 'source': 'agent', 'operationId': message['operationId']})
                assert undo.status_code == 200 and undo.json()['status'] == 'succeeded', undo.text
                restored = next(item for item in undo.json()['snapshot']['meals'] if item['id'] == 'meal-lunch')
                assert restored['items'][0]['consumedFraction'] == 1
                pending = request_for(saved, 'WAIT_FOR_CANCEL')
                with first.stream('POST', '/api/copilotkit/agent/wellio/run', json=body(pending)) as response:
                    assert response.status_code == 200
                    for line in response.iter_lines():
                        if not line.startswith('data: '):
                            continue
                        event = json.loads(line[6:])
                        value = event.get('value', {})
                        if event['type'] == 'CUSTOM' and value.get('type') == 'tool' and value['step']['status'] == 'succeeded':
                            break
                    else:
                        raise AssertionError('No real context tool before cancellation')
                    stopped = second.post('/api/copilotkit/agent/wellio/stop/' + pending['conversationId'])
                    assert stopped.status_code == 200 and stopped.json()['stopped'] is False
                # Closing the browser-facing response must cancel through the proxy,
                # Node SDK and Python coordinator without waiting for the lease.
                for _ in range(60):
                    saved = first.get('/api/state').json()
                    last = saved['messages'][-1]
                    if last['status'] != 'streaming':
                        break
                    time.sleep(.1)
                assert last['status'] == 'stopped', last
            # A fresh runtime and FastAPI instance reuse the same persisted receipt.
            with services(frontend, database_url, uploads, ports):
                assert first.get('/api/state').json()['messages'] == saved['messages']
                run(first, request)
                assert first.get('/api/state').json()['messages'] == saved['messages']
                print('CopilotKit BuiltInAgent → FastAPI → PostgreSQL: context tool, saved reply, meal correction/read-after-write/Undo, session isolation, replay, disconnect cancellation and restart passed.')


if __name__ == '__main__':
    main(sys.argv[1])
