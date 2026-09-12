"""Explicit opt-in paid-provider smoke against isolated PG and production runtimes.

Run: uv run --frozen python scripts/live_smoke.py --live --env-file PATH --frontend PATH
Normal pytest never loads credentials or imports this script.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
from dotenv import dotenv_values

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND / 'tests'))
from pg_cluster import temporary_postgres
from production_smoke import unused_port
from copilot_smoke import request_for, body


@contextmanager
def services(frontend, database_url, uploads, credentials):
    ports = [unused_port() for _ in range(3)]
    base = f'http://127.0.0.1:{ports[2]}'
    env = {**os.environ, **credentials, 'DATABASE_URL': database_url, 'WELLIO_ATTACHMENTS_PATH': uploads,
           'WELLIO_API_BASE_URL': f'http://127.0.0.1:{ports[0]}', 'WELLIO_AGENT_BASE_URL': f'http://127.0.0.1:{ports[1]}',
           'WELLIO_AGENT_PORT': str(ports[1]), 'WELLIO_PUBLIC_ORIGIN': base, 'WELLIO_AGENT_TOKEN': secrets.token_hex(32),
           'COPILOTKIT_TELEMETRY_DISABLED': 'true', 'PORT': str(ports[2]), 'NITRO_PORT': str(ports[2]),
           'HOST': '127.0.0.1', 'WELLIO_COOKIE_SECURE': '0'}
    children = []
    with tempfile.TemporaryFile(mode='w+') as output:
        try:
            commands = [
                (['uv', 'run', '--frozen', 'uvicorn', 'wellio.main:application', '--factory', '--host', '127.0.0.1', '--port', str(ports[0]), '--no-proxy-headers'], BACKEND, env['WELLIO_API_BASE_URL'] + '/healthz'),
                (['node', 'dist/server.js'], BACKEND / 'agent-runtime', env['WELLIO_AGENT_BASE_URL'] + '/healthz'),
                (['node', '.output/server/index.mjs'], frontend, base + '/today'),
            ]
            for command, cwd, ready in commands:
                child = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=output)
                children.append(child)
                for _ in range(200):
                    if child.poll() is not None:
                        raise RuntimeError('Service startup failed: ' + cwd.name)
                    try:
                        if httpx.get(ready, timeout=.5).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('Service health check timed out')
            yield base
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill(); child.wait()
            output.seek(0)
            for line in output:
                if line.startswith(('[wellio:model-step]', '[wellio:model-error]')):
                    print(line.rstrip(), flush=True)


def chat(client, label, prompt, report, **extra):
    snapshot = client.get('/api/state').json()
    request = {**request_for(snapshot, prompt), **extra}
    started = time.monotonic()
    response = client.post('/api/copilotkit/agent/wellio/run', json=body(request))
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    custom = [e['value'] for e in events if e['type'] == 'CUSTOM' and e['name'] == 'wellio']
    saved = client.get('/api/state').json()
    message_id = next((e['messageId'] for e in custom if 'messageId' in e), None)
    message = next((m for m in saved['messages'] if m['id'] == message_id), {})
    result = {'scenario': label, 'httpStatus': response.status_code, 'elapsedSeconds': round(time.monotonic() - started, 2),
              'status': message.get('status'), 'tools': [e['step'] for e in custom if e['type'] == 'tool' and e['step']['status'] != 'started'],
              'errorCodes': [e.get('errorCode') for e in custom if e['type'] == 'error'],
              'reply': message.get('content'), 'summaries': saved.get('advice'), 'model': 'deepseek/deepseek-v4.1-flash'}
    report.append(result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    assert message.get('status') == 'complete', label + ': ' + str(result['errorCodes'])
    return saved, message, request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--frontend', type=Path, required=True)
    parser.add_argument('--scenario', choices=['facts', 'meal', 'search', 'image', 'all'], default='facts')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    settings = dotenv_values(args.env_file)
    credentials = {key: settings.get(key) or '' for key in ['OPENROUTER_API_KEY', 'EXA_API_KEY', 'WELLIO_AI_MODEL']}
    if not all(credentials[key] for key in ['OPENROUTER_API_KEY', 'EXA_API_KEY']):
        raise SystemExit('Both provider keys must be configured')
    report = []
    try:
        with temporary_postgres() as database_url, tempfile.TemporaryDirectory(prefix='wellio-live-uploads-') as uploads:
            # Production now requires knowledge. Import cached demo vectors into
            # this dedicated test DB; never read the development DATABASE_URL.
            from wellio.knowledge import KnowledgeStore, EmbeddingConfig
            corpus = BACKEND / '.data/knowledge/nutrition-v1/releases/demo-6c1a7913caf33172c82b3482'
            bundle = json.loads((corpus / 'bundle.json').read_text())
            cached = json.loads((corpus / 'vectors.json').read_text())
            knowledge = KnowledgeStore(database_url)
            knowledge.initialize()
            knowledge.import_release(bundle['releaseId'], EmbeddingConfig(), bundle['documents'], bundle['chunks'], [cached['vectors'][c['id']] for c in bundle['chunks']])
            knowledge.activate(bundle['releaseId'])
            with services(args.frontend.resolve(), database_url, uploads, credentials) as base:
                scenarios = ['facts', 'meal', 'search', 'image'] if args.scenario == 'all' else [args.scenario]
                for scenario in scenarios:
                    with httpx.Client(base_url=base, headers={'origin': base}, timeout=130) as client:
                        if scenario == 'facts':
                            saved, message, request = chat(client, 'facts', 'Briefly summarize my saved lunch and today\'s scheduled workout. Do not change anything or give health advice.', report)
                            assert len(str(message.get('content', ''))) > 30, 'Fact summary is empty or only a placeholder'
                            replay = client.post('/api/copilotkit/agent/wellio/run', json=body(request))
                            assert replay.status_code == 200 and client.get('/api/state').json()['messages'] == saved['messages']
                            print('Exact request replay preserved messages.', flush=True)
                        elif scenario == 'meal':
                            saved, message, _ = chat(client, 'meal', 'I only ate half of this item', report, targetMealId='meal-lunch', targetMealItemId='item-lunch')
                            meal = next(m for m in saved['meals'] if m['id'] == 'meal-lunch')
                            assert meal['items'][0]['consumedFraction'] == .5 and message.get('operationId')
                            undo = client.post('/api/actions', json={'kind': 'undo_meal', 'requestId': str(uuid4()), 'resetEpoch': saved['resetEpoch'], 'source': 'agent', 'operationId': message['operationId']})
                            assert undo.status_code == 200 and undo.json()['status'] == 'succeeded'
                            meal = next(m for m in undo.json()['snapshot']['meals'] if m['id'] == 'meal-lunch')
                            assert meal['items'][0]['consumedFraction'] == 1
                            print('Real model portion mutation and explicit Undo passed.', flush=True)
                        elif scenario == 'search':
                            saved, _, _ = chat(client, 'search', 'Search the public menu of Pret A Manger in Hong Kong. Give me two items supported by the retrieved sources and their source links. Do not record food or make nutrition recommendations.', report)
                            assert any(s['operation'] == 'menu_search' and s['status'] == 'succeeded' for s in report[-1]['tools'])
                            assert saved['mealRevision'] == 1
                        else:
                            from PIL import Image, ImageDraw, ImageFont
                            from io import BytesIO
                            card = Image.new('RGB', (720, 300), 'white')
                            draw = ImageDraw.Draw(card)
                            font = ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf', 36) if Path('/System/Library/Fonts/Supplemental/Arial.ttf').exists() else ImageFont.load_default(size=36)
                            draw.text((30, 30), 'TEST CAFE MENU\nChicken rice - HK$58\nTofu salad - HK$42', fill='black', font=font, spacing=20)
                            image = BytesIO(); card.save(image, format='PNG')
                            snapshot = client.get('/api/state').json()
                            uploaded = client.post('/api/attachments', files={'file': ('test-menu.png', image.getvalue(), 'image/png')}, data={'purpose': 'menu'})
                            assert uploaded.status_code == 200
                            attachment = uploaded.json()
                            attachment_id = attachment.get('id') or attachment.get('attachment', {}).get('id')
                            assert attachment_id
                            saved, message, _ = chat(client, 'image', 'Read the two dish names and their prices from the attached test menu. Do not search the web or record any food.', report, attachmentIds=[attachment_id], purpose='menu')
                            content = message['content'] if isinstance(message['content'], str) else json.dumps(message['content'])
                            assert '58' in content and '42' in content and saved['mealRevision'] == snapshot['mealRevision']
                            print('Uploaded menu image read correctly without meal mutation.', flush=True)
    finally:
        if args.report:
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
