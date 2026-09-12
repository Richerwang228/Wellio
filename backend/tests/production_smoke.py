"""Run against built frontend + real FastAPI + private PostgreSQL, then restart both."""
from contextlib import contextmanager
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
from PIL import Image

from pg_cluster import temporary_postgres
from wellio.database import Database
from wellio.authorization import record_user_message, authorize_user_mutation
from wellio.meals import mutate_meal_log


def unused_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


@contextmanager
def stack(frontend, database_url, uploads, port):
    env = {**os.environ, 'DATABASE_URL': database_url, 'EXA_API_KEY': '', 'OPENROUTER_API_KEY': '', 'WELLIO_AI_MODEL': '', 'PORT': str(port), 'NITRO_PORT': str(port), 'HOST': '127.0.0.1', 'WELLIO_COOKIE_SECURE': '0', 'WELLIO_ATTACHMENTS_PATH': uploads, 'WELLIO_BACKEND_DIR': str(Path(__file__).resolve().parents[1]), 'WELLIO_PUBLIC_ORIGIN': f'http://127.0.0.1:{port}'}
    with tempfile.TemporaryFile(mode='w+') as output:
        child = subprocess.Popen(['node', 'scripts/run-stack.mjs', 'start'], cwd=frontend, env=env, stdout=output, stderr=output)
        try:
            for attempt in range(150):
                if child.poll() is not None:
                    output.seek(0); raise AssertionError(output.read())
                try:
                    if httpx.get(f'http://127.0.0.1:{port}/today', timeout=.5).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(.1)
            else:
                output.seek(0); raise AssertionError(output.read())
            yield
        finally:
            child.terminate()
            try:
                child.wait(timeout=6)
            except subprocess.TimeoutExpired:
                child.kill(); child.wait()


def act(client, snapshot, kind, expected=200, **fields):
    request = {'requestId': str(uuid4()), 'resetEpoch': snapshot['resetEpoch'], 'source': 'today', 'kind': kind, **fields}
    response = client.post('/api/actions', json=request)
    assert response.status_code == expected, response.text
    return request, response.json()


def main(frontend):
    frontend = Path(frontend).resolve()
    # Native production routes must not import SQLite, old Agent or provider credentials.
    for file in (frontend / '.output/server').rglob('*.mjs'):
        text = file.read_text()
        assert 'node:sqlite' not in text and 'session_signing_key_v1' not in text, file
    with temporary_postgres() as database_url, tempfile.TemporaryDirectory(prefix='wellio-production-') as uploads:
        port = unused_port()
        base = f'http://127.0.0.1:{port}'
        with httpx.Client(base_url=base, headers={'origin': base}) as first, httpx.Client(base_url=base, headers={'origin': base}) as second:
            with stack(frontend, database_url, uploads, port):
                a, b = first.get('/api/state').json(), second.get('/api/state').json()
                assert a['sessionId'] != b['sessionId']
                assert a['capabilities'] == {'agent': False, 'menuSearch': False, 'persistence': 'server'}
                locale_request, locale = act(first, a, 'set_locale', locale='zh-CN')
                reset_request, reset = act(first, locale['snapshot'], 'reset_demo', scenario='low_recovery')
                assert reset['snapshot']['readiness']['score'] == 42 and reset['snapshot']['sleep']['minutes'] == 240
                assert reset['snapshot']['locale'] == 'zh-CN'
                assert first.post('/api/actions', json=reset_request).json() == reset
                assert first.post('/api/actions', json=locale_request).status_code == 409
                _, started = act(second, b, 'start_workout', workoutId=b['workout']['id'], expectedWorkoutVersion=b['workout']['version'])
                _, completed = act(second, started['snapshot'], 'complete_exercise', workoutId=b['workout']['id'], exerciseId=b['workout']['exercises'][0]['id'], expectedWorkoutVersion=started['snapshot']['workout']['version'])
                finish_request, finished = act(second, completed['snapshot'], 'finish_workout', workoutId=b['workout']['id'], actualMinutes=12, confirmIncomplete=True, expectedWorkoutVersion=completed['snapshot']['workout']['version'])
                assert finished['snapshot']['workout']['status'] == 'completed'
                assert len(finished['snapshot']['history']['training']) == len(b['history']['training']) + 1
                with_database = Database(database_url)
                try:
                    snapshot = with_database.get_snapshot(b['sessionId'])
                    source = record_user_message(with_database, b['sessionId'], {'requestId': 'production-input', 'resetEpoch': snapshot['resetEpoch'], 'conversationId': snapshot['conversationId'], 'content': 'Log this meal.'})
                    grant = authorize_user_mutation(with_database, b['sessionId'], {'sourceMessageId': source['result']['messageId'], 'resetEpoch': snapshot['resetEpoch'], 'runId': 'production-run'})
                    meal_request = {'kind': 'mutate_meal_log', 'action': 'add', 'requestId': 'production-meal', 'resetEpoch': snapshot['resetEpoch'], 'runId': 'production-run', 'authorizationId': grant['id'], 'expectedMealRevision': snapshot['mealRevision'], 'meal': {'period': 'dinner', 'time': '18:45', 'items': [{'name': {'en': 'Fries', 'zh-CN': '薯条'}, 'portion': {'en': '100 g', 'zh-CN': '100 g'}, 'originalPortion': {'quantity': 100, 'unit': 'g'}, 'nutrientUnits': {'energy': 'kcal', 'mass': 'g'}, 'base': {'kcal': 300, 'protein': 4, 'carbs': 35, 'fat': 16}, 'consumedFraction': 1, 'estimated': True}]}}
                    meal = mutate_meal_log(with_database, b['sessionId'], meal_request)
                    assert meal['result']['status'] == 'succeeded'
                    assert second.get('/api/state').json() == meal['result']['snapshot']
                finally:
                    with_database.close()
                png = io.BytesIO(); Image.new('RGB', (3, 2), 'red').save(png, format='PNG'); image = png.getvalue()
                uploaded = first.post('/api/attachments', files={'file': ('food.png', image, 'image/png')}, data={'purpose': 'food'})
                assert uploaded.status_code == 200, uploaded.text
                attachment = uploaded.json()
                assert first.get(attachment['url']).content == image
                assert second.get(attachment['url']).status_code == 404
                saved_a = first.get('/api/state').json()
                saved_b = second.get('/api/state').json()
            # Same PG data, cookies, uploads; brand new frontend and FastAPI processes.
            with stack(frontend, database_url, uploads, port):
                assert first.get('/api/state').json() == saved_a
                assert second.get('/api/state').json() == saved_b
                assert second.post('/api/actions', json=finish_request).json() == finished
                assert first.post('/api/actions', json=reset_request).json() == reset
                assert first.get(attachment['url']).content == image
                undo_request, undone = act(second, saved_b, 'undo_meal', operationId=meal['result']['operationId'])
                assert undone['snapshot']['meals'] == finished['snapshot']['meals']
                assert second.post('/api/actions', json=undo_request).json() == undone
                _, reset_again = act(first, saved_a, 'reset_demo', scenario='normal')
                assert reset_again['snapshot']['resetEpoch'] == 3
                assert first.get(attachment['url']).status_code == 404
                assert first.post('/api/chat', json={}).status_code == 503
                assert second.get('/api/state').json() == undone['snapshot']
                assert first.get('/today').status_code == 200
            with stack(frontend, database_url, uploads, port):
                assert second.get('/api/state').json() == undone['snapshot']
                assert second.post('/api/actions', json=undo_request).json() == undone
                assert first.get(attachment['url']).status_code == 404
    print('PASS: built frontend → FastAPI → real PostgreSQL; cookie/session isolation, locale/reset/epochs, workout lifecycle, native meal authorization+Undo, receipts and uploads persist across two full stack restarts; no external model/search calls.')


if __name__ == '__main__':
    main(sys.argv[1])
