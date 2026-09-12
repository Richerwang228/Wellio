"""Explicit live latency/regression probe against isolated PostgreSQL and built UI.

No development database is read. Output contains synthetic fixture results only.
Run: python scripts/performance_smoke.py --live --env-file .env --frontend PATH --report PATH
"""
import argparse
import json
from pathlib import Path
import tempfile
import time

import httpx
from dotenv import dotenv_values
from live_smoke import BACKEND, services, temporary_postgres, request_for, body
from wellio.knowledge import KnowledgeStore, EmbeddingConfig

SCENARIOS = {
    'facts': ("Briefly summarize my saved lunch and today's scheduled workout. Do not change anything or give health advice.", {}),
    'sleep': ('解释睡眠不足对运动表现的影响，给一句谨慎建议即可，不需要生成或修改训练计划。', {}),
    'meal': ('I only ate half of this item', {'targetMealId': 'meal-lunch', 'targetMealItemId': 'item-lunch'}),
    'training': ('今天只有十五分钟，帮我生成一个精简训练方案。', {}),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--env-file', type=Path, required=True)
    parser.add_argument('--frontend', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--scenario', choices=['all', *SCENARIOS], default='all')
    args = parser.parse_args()
    settings = dotenv_values(args.env_file)
    credentials = {key: settings.get(key) or '' for key in ['OPENROUTER_API_KEY', 'EXA_API_KEY', 'WELLIO_AI_MODEL']}
    if not credentials['OPENROUTER_API_KEY']:
        raise SystemExit('OPENROUTER_API_KEY_REQUIRED')
    results = []
    with temporary_postgres() as database_url, tempfile.TemporaryDirectory(prefix='wellio-performance-uploads-') as uploads:
        corpus = BACKEND / '.data/knowledge/nutrition-v1/releases/demo-6c1a7913caf33172c82b3482'
        bundle, cached = [json.loads((corpus / name).read_text()) for name in ['bundle.json', 'vectors.json']]
        store = KnowledgeStore(database_url)
        store.initialize()
        store.import_release(bundle['releaseId'], EmbeddingConfig(), bundle['documents'], bundle['chunks'], [cached['vectors'][c['id']] for c in bundle['chunks']])
        store.activate(bundle['releaseId'])
        with services(args.frontend.resolve(), database_url, uploads, credentials) as base:
            for label in SCENARIOS if args.scenario == 'all' else [args.scenario]:
                prompt, extra = SCENARIOS[label]
                with httpx.Client(base_url=base, headers={'origin': base}, timeout=140) as client:
                    snapshot = client.get('/api/state').json()
                    request = {**request_for(snapshot, prompt), **extra, 'locale': 'zh-CN' if label in ['sleep', 'training'] else 'en'}
                    started = time.perf_counter()
                    events, first_text, first_event = [], None, None
                    print(json.dumps({'scenario': label, 'phase': 'started'}), flush=True)
                    with client.stream('POST', '/api/copilotkit/agent/wellio/run', json=body(request)) as response:
                        for line in response.iter_lines():
                            if not line.startswith('data: '):
                                continue
                            event = json.loads(line[6:])
                            elapsed = round(time.perf_counter() - started, 3)
                            first_event = elapsed if first_event is None else first_event
                            if event.get('type') != 'CUSTOM' or event.get('name') != 'wellio':
                                continue
                            value = event['value']
                            if value['type'] == 'text' and value.get('delta') and first_text is None:
                                first_text = elapsed
                                print(json.dumps({'scenario': label, 'phase': 'first_text', 'seconds': elapsed}), flush=True)
                            if value['type'] == 'tool':
                                step = {key: value['step'].get(key) for key in ['operation', 'status', 'errorCode']}
                                events.append({'seconds': elapsed, **step})
                                if step['status'] != 'started':
                                    print(json.dumps({'scenario': label, 'seconds': elapsed, **step}), flush=True)
                    elapsed = round(time.perf_counter() - started, 3)
                    saved = client.get('/api/state').json()
                    message = saved['messages'][-1]
                    fraction = next(meal for meal in saved['meals'] if meal['id'] == 'meal-lunch')['items'][0]['consumedFraction']
                    passed = message['status'] == 'complete' and bool(message.get('content'))
                    if label == 'facts':
                        content = str(message.get('content', '')).lower()
                        passed = passed and '35' in content and any(word in content for word in ['row', 'pulldown', 'curl'])
                    if label == 'meal':
                        passed = passed and fraction == .5
                    if label == 'training':
                        passed = passed and any(e['operation'] == 'workout_proposal' and e['status'] == 'succeeded' for e in events)
                        passed = passed and saved['workout'] == snapshot['workout'] and saved['conditions'] == snapshot['conditions']
                    row = {'scenario': label, 'httpStatus': response.status_code, 'totalSeconds': elapsed,
                           'firstEventSeconds': first_event, 'firstTextSeconds': first_text,
                           'status': message['status'], 'errorCode': message.get('errorCode'),
                           'passed': passed, 'content': message.get('content'), 'events': events}
                    results.append(row)
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n')
                    print(json.dumps({k: v for k, v in row.items() if k not in ['content', 'events']}, ensure_ascii=False), flush=True)
    print('Owned services and temporary PostgreSQL stopped.', flush=True)
    if not all(row['passed'] for row in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
