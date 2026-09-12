"""Real PostgreSQL + FastAPI RPC; all model execution belongs to Node."""
import asyncio
import time
from copy import deepcopy
from uuid import uuid4

import pytest
import psycopg

from wellio.agent_service import AgentService
from wellio.attachments import AttachmentStore
from wellio.errors import BackendError
from wellio.main import agent_is_configured
from wellio.validation import canonical_json

TOKEN = 'test-internal-service-token-40-characters-long'
ANSWER = {'markdown': 'Recovery is 82/100; review the current Pull session.', 'trainingSummary': 'Keep the current plan.', 'nutritionSummary': 'Use the recorded intake.'}


def chat(snapshot, message='Review my day.', **changes):
    return {'requestId': str(uuid4()), 'resetEpoch': snapshot['resetEpoch'], 'conversationId': snapshot['conversationId'], 'locale': 'en', 'source': 'user', 'message': message, 'attachmentIds': [], **changes}


def rpc(client, operation, body, **kwargs):
    return client.post('/internal/agent/' + operation, json=body, headers={'Authorization': 'Bearer ' + TOKEN, **kwargs.pop('headers', {})}, **kwargs)


@pytest.fixture
def enabled_client(client_factory):
    return client_factory(agent_token=TOKEN, agent_enabled=True)


def opened(client, message='Review my day.', **changes):
    snapshot = client.get('/api/state').json()
    request = chat(snapshot, message, **changes)
    response = rpc(client, 'open', {'request': request})
    assert response.status_code == 200, response.text
    return snapshot, request, response.json()


def tool(client, run, name, input=None, call_id=None):
    return rpc(client, 'tool', {'runId': run['runId'], 'toolCallId': call_id or str(uuid4()), 'name': name, 'input': input or {}})


def test_internal_auth_requires_both_service_secret_and_original_signed_cookie(client_factory):
    client = client_factory(agent_token=TOKEN, agent_enabled=True)
    assert rpc(client, 'open', {'request': {}}).status_code == 401
    snapshot = client.get('/api/state').json()
    payload = {'request': chat(snapshot)}
    assert client.post('/internal/agent/open', json=payload).json()['errorCode'] == 'AGENT_AUTH_REQUIRED'
    assert rpc(client, 'open', payload, headers={'Authorization': 'Bearer wrong'}).status_code == 401
    assert rpc(client, 'open', payload, headers={'Origin': 'https://untrusted.example'}).status_code == 403
    assert rpc(client, 'open', payload, headers={'Cookie': 'wellio_session=forged'}).json()['errorCode'] == 'INVALID_SESSION'
    assert not client.app.state.database.list_agent_runs(snapshot['sessionId'])


def test_disabled_or_missing_token_never_advertises_agent_and_legacy_migrates(client_factory):
    for options in ({}, {'agent_enabled': True}, {'agent_token': TOKEN}, {'agent_token': 'bad\ntoken', 'agent_enabled': True}):
        client = client_factory(**options)
        snapshot = client.get('/api/state').json()
        assert not snapshot['capabilities']['agent']
        assert client.post('/api/chat', json=chat(snapshot)).status_code == 503
    client = client_factory(agent_token=TOKEN, agent_enabled=True)
    snapshot = client.get('/api/state').json()
    assert snapshot['capabilities']['agent']
    assert client.post('/api/chat', json=chat(snapshot)).status_code == 410
    assert client.post('/api/agent', json={}).status_code == 404
    action = {'kind': 'request_proposal', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'today'}
    assert client.post('/api/actions', json=action).status_code == 410


def test_open_uses_trusted_messages_raw_targets_and_scoped_run_identity(enabled_client):
    client = enabled_client
    snapshot, request, run = opened(client, '  我只有20分钟  ')
    assert run['runId'] != request['requestId'] and run['request'] == request
    assert run['messages'][-1] == {'role': 'user', 'content': request['message']}
    assert run['contextRequired'] and len(run['tools']) == 8
    assert set(run['tools']) == {'get_day_context', 'get_gym_equipment', 'query_history', 'search_restaurant_menu', 'mutate_meal_log', 'undo_meal_change', 'propose_workout', 'record_workout_progress'}
    assert not any(key in run['tools']['mutate_meal_log']['properties'] for key in ('sessionId', 'runId', 'authorizationId', 'resetEpoch'))
    current = client.get('/api/state').json()
    assert current['conditions']['availableMinutes'] == 20
    assert current['workout'] == snapshot['workout']
    source = client.app.state.database.list_user_inputs(snapshot['sessionId'], 1)[0]
    assert source['content'] == '  我只有20分钟  ' and source['versions']['conditions'] == 1
    assert source['workoutContext']['trainingSessionId'] == snapshot['workout']['trainingSessionId']
    assert '20' in run['instructions']


def test_arbitrary_browser_state_history_labels_and_unknown_tools_cannot_authorize(enabled_client):
    client = enabled_client
    snapshot = client.get('/api/state').json()
    for payload in [{'request': chat(snapshot), 'state': snapshot}, {'request': {**chat(snapshot), 'source': 'model'}}, {'request': chat(snapshot), 'messages': []}]:
        assert rpc(client, 'open', payload).status_code == 400
    _, _, run = opened(client)
    assert tool(client, run, 'apply_proposal').status_code == 400
    response = tool(client, run, 'mutate_meal_log', {'action': 'delete', 'mealId': 'meal-lunch'}).json()
    assert response['result']['errorCode'] == 'CONTEXT_READ_REQUIRED'
    tool(client, run, 'get_day_context')
    response = tool(client, run, 'mutate_meal_log', {'action': 'delete', 'mealId': 'meal-lunch'}).json()
    assert response['result']['errorCode'] == 'USER_INTENT_REQUIRED'
    assert len(client.get('/api/state').json()['meals']) == 2


def test_finish_requires_real_fresh_read_and_preserves_structured_summaries(enabled_client):
    client = enabled_client
    snapshot, request, run = opened(client)
    early = rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER})
    assert early.status_code == 409 and early.json()['errorCode'] == 'CONTEXT_READ_REQUIRED'
    context = tool(client, run, 'get_day_context').json()
    assert not {'messages', 'history', 'advice', 'readinessCheck'} & context['result']['snapshot'].keys()
    assert context['result']['readiness']['score'] == 82 and not context['contextRequired']
    response = rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER})
    assert response.status_code == 200, response.text
    events = response.json()['events']
    assert [event['type'] for event in events] == ['snapshot', 'done']
    final = events[0]['snapshot']
    assert final['advice']['contextReadId'] == context['result']['id']
    assert final['advice']['training']['en'] == ANSWER['trainingSummary']
    assert final['messages'][-1]['content'] == ANSWER['markdown'] and final['messages'][-1]['status'] == 'complete'
    repeated = rpc(client, 'open', {'request': request}).json()
    assert repeated['terminal'] and repeated['events'][-1]['type'] == 'done'
    assert len(client.app.state.database.list_agent_runs(snapshot['sessionId'])) == 1
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).status_code == 200
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': {**ANSWER, 'markdown': 'changed'}}).status_code == 409


def test_meal_write_step_and_undo_pointer_are_atomic_and_force_new_context(enabled_client):
    client = enabled_client
    initial, _, run = opened(client, 'I only ate half of this item', targetMealId='meal-lunch', targetMealItemId='item-lunch')
    assert '"mealId":"meal-lunch"' in run['instructions']
    assert '"mealItemId":"item-lunch"' in run['instructions']
    assert '"consumedFraction":0.5' in run['instructions']
    old_context = tool(client, run, 'get_day_context').json()['result']
    result = tool(client, run, 'mutate_meal_log', {'action': 'update', 'mealId': 'meal-lunch', 'mealItemId': 'item-lunch', 'changes': {'consumedFraction': .5}}).json()
    assert result['result']['result']['status'] == 'succeeded' and result['contextRequired']
    assert 'snapshot' not in result['result']['result']
    saved = next(event['snapshot'] for event in result['events'] if event['type'] == 'snapshot')
    assistant = next(item for item in saved['messages'] if item['id'] == run['messageId'])
    assert assistant['operationId'] == result['result']['result']['operationId'] and assistant['mealId'] == 'meal-lunch'
    assert assistant['steps'][-1]['status'] == 'succeeded'
    assert saved['meals'][1]['items'][0]['consumedFraction'] == .5
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).json()['errorCode'] == 'CONTEXT_READ_REQUIRED'
    new_context = tool(client, run, 'get_day_context').json()['result']
    assert new_context['id'] != old_context['id'] and new_context['versions']['meal'] == initial['mealRevision'] + 1
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).status_code == 200


def test_nullable_summaries_preserve_existing_advice_for_greeting(enabled_client):
    client = enabled_client
    _, _, first = opened(client)
    tool(client, first, 'get_day_context')
    assert rpc(client, 'finish', {'runId': first['runId'], 'output': ANSWER}).status_code == 200
    before = client.get('/api/state').json()['advice']
    _, _, greeting = opened(client, '你好')
    tool(client, greeting, 'get_day_context')
    output = {'markdown': '你好！今天准备度为82分。', 'trainingSummary': None, 'nutritionSummary': None}
    assert rpc(client, 'finish', {'runId': greeting['runId'], 'output': output}).status_code == 200
    assert client.get('/api/state').json()['advice'] == before


def test_query_history_failure_returns_correctable_inclusive_bounds(enabled_client):
    client = enabled_client
    _, _, run = opened(client, 'Show my training history')
    tool(client, run, 'get_day_context')
    result = tool(client, run, 'query_history', {'metric': 'training', 'from': '2026-08-12', 'to': '2026-09-12'}).json()['result']
    assert result['errorCode'] == 'INVALID_DATE_RANGE'
    assert result['bounds'] == {'dayKey': '2026-09-12', 'maxInclusiveDays': 31,
                                'latestValidRange': {'from': '2026-08-13', 'to': '2026-09-12'}}
    corrected = tool(client, run, 'query_history', {'metric': 'training', **result['bounds']['latestValidRange']}).json()['result']
    assert corrected['summary']['recordCount'] > 0


@pytest.mark.asyncio
async def test_slow_tool_database_phase_does_not_block_event_loop(database, tmp_path, monkeypatch):
    service = AgentService(database, AttachmentStore(tmp_path / 'attachments'), enabled=True)
    snapshot = database.create_session()
    run = service.open(snapshot['sessionId'], {'request':chat(snapshot, 'Hi')})
    original = database.capture_context
    def slow_context(*args, **kwargs):
        time.sleep(.25)
        return original(*args, **kwargs)
    monkeypatch.setattr(database, 'capture_context', slow_context)
    ticks, finished = [], False
    async def heartbeat():
        while not finished:
            ticks.append(asyncio.get_running_loop().time())
            await asyncio.sleep(.01)
    heartbeat_task = asyncio.create_task(heartbeat())
    try:
        reply = await service.tool(snapshot['sessionId'], {'runId':run['runId'], 'name':'get_day_context', 'input':{}, 'toolCallId':str(uuid4())})
        assert reply['contextRequired'] is False
    finally:
        finished = True
        await heartbeat_task
        await service.close()
    assert len(ticks) > 10
    assert max(b - a for a,b in zip(ticks, ticks[1:])) < .15


def test_fixed_progress_intent_cannot_move_to_next_exercise_or_execute_twice(enabled_client):
    client = enabled_client
    snapshot = client.get('/api/state').json()
    start = {'kind': 'start_workout', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'today', 'workoutId': snapshot['workout']['id'], 'expectedWorkoutVersion': 1}
    assert client.post('/api/actions', json=start).status_code == 200
    _, _, run = opened(client, 'Complete this exercise', targetExerciseId='exercise-seated-cable-row')
    tool(client, run, 'get_day_context')
    wrong = tool(client, run, 'record_workout_progress', {'kind': 'complete_exercise', 'workoutId': snapshot['workout']['id'], 'exerciseId': 'exercise-lat-pulldown'}).json()
    assert wrong['result']['errorCode'] == 'USER_INTENT_REQUIRED'
    first = tool(client, run, 'record_workout_progress', {'kind': 'complete_exercise', 'workoutId': snapshot['workout']['id'], 'exerciseId': 'exercise-seated-cable-row'}).json()
    assert first['result']['result']['status'] == 'succeeded'
    tool(client, run, 'get_day_context')
    second = tool(client, run, 'record_workout_progress', {'kind': 'complete_exercise', 'workoutId': snapshot['workout']['id'], 'exerciseId': 'exercise-seated-cable-row'}).json()
    assert second['result']['status'] == 'failed'
    assert client.get('/api/state').json()['workout']['exercises'][1]['completed'] is False


def test_cancel_is_durable_rejects_late_tools_and_keeps_actual_success(enabled_client):
    client = enabled_client
    _, _, run = opened(client, 'I ate half of this item', targetMealId='meal-lunch', targetMealItemId='item-lunch')
    tool(client, run, 'get_day_context')
    tool(client, run, 'mutate_meal_log', {'action': 'update', 'mealId': 'meal-lunch', 'mealItemId': 'item-lunch', 'changes': {'consumedFraction': .5}})
    response = rpc(client, 'cancel', {'runId': run['runId'], 'status': 'failed', 'errorCode': 'TIMEOUT'})
    assert response.status_code == 200
    state = client.get('/api/state').json()
    assistant = next(item for item in state['messages'] if item['id'] == run['messageId'])
    assert assistant['status'] == 'failed' and assistant['errorCode'] == 'TIMEOUT'
    assert assistant['steps'][-1]['status'] == 'succeeded' and assistant['operationId']
    assert tool(client, run, 'get_day_context').status_code == 409
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).status_code == 409
    assert rpc(client, 'status', {'runId': run['runId']}).json()['active'] is False


def test_readiness_dedup_invalid_user_priority_and_replay_do_not_create_new_runs(enabled_client):
    client = enabled_client
    snapshot = client.get('/api/state').json()
    request = chat(snapshot, '', source='app_open', checkMode='auto')
    auto = rpc(client, 'open', {'request': request}).json()
    repeat = rpc(client, 'open', {'request': chat(snapshot, '', source='app_open', checkMode='auto')}).json()
    assert repeat['terminal'] and repeat['events'][-1]['outcome'] == 'in_progress'
    invalid = chat(snapshot, 'Complete this exercise', targetExerciseId='does-not-exist')
    assert rpc(client, 'open', {'request': invalid}).status_code == 404
    assert rpc(client, 'status', {'runId': auto['runId']}).json()['active']
    user = rpc(client, 'open', {'request': chat(snapshot)}).json()
    assert not user['terminal']
    assert rpc(client, 'status', {'runId': auto['runId']}).json()['errorCode'] == 'USER_PRIORITY'


def test_lease_expiry_and_session_reset_reject_late_finish(client_factory):
    now = [1000]
    client = client_factory(agent_token=TOKEN, agent_enabled=True, now=lambda: now[0], agent_timeout_seconds=.1)
    _, _, run = opened(client)
    assert client.get('/api/state').json()['messages'][-1]['status'] == 'streaming'
    now[0] += 2101
    assert rpc(client, 'status', {'runId': run['runId']}).json()['errorCode'] == 'RUN_LEASE_EXPIRED'
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).status_code == 409
    snapshot = client.get('/api/state').json()
    new = rpc(client, 'open', {'request': chat(snapshot)}).json()
    action = {'kind': 'reset_demo', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'today', 'scenario': 'normal'}
    assert client.post('/api/actions', json=action).status_code == 200
    assert rpc(client, 'finish', {'runId': new['runId'], 'output': ANSWER}).status_code == 409


def test_ui_proposal_action_receipt_is_atomic_replayed_and_locale_independent(enabled_client):
    client = enabled_client
    snapshot = client.get('/api/state').json()
    action = {'kind': 'request_proposal', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'today'}
    run = rpc(client, 'open', {'action': action}).json()
    context = tool(client, run, 'get_day_context').json()['result']
    assert tool(client, run, 'propose_workout', {'scope': 'workout', 'contextReadId': context['id'], 'reason': {'en': 'Keep plan', 'zh-CN': '保持计划'}, 'workout': snapshot['workout']}).json()['result']['result']['status'] == 'succeeded'
    # Even proposal-only writes require a new read before summarizing them.
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).json()['errorCode'] == 'CONTEXT_READ_REQUIRED'
    tool(client, run, 'get_day_context')
    reply = rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).json()['reply']
    assert reply['result']['status'] == 'succeeded' and reply['result']['proposalId']
    locale = {'kind': 'set_locale', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'profile', 'locale': 'zh-CN'}
    client.post('/api/actions', json=locale)
    replay = rpc(client, 'open', {'action': action}).json()
    assert replay['terminal'] and replay['reply'] == reply
    assert len(client.app.state.database.list_agent_runs(snapshot['sessionId'])) == 1


def test_ui_final_receipt_failure_rolls_back_message_run_and_summary(enabled_client):
    client = enabled_client
    snapshot = client.get('/api/state').json()
    action = {'kind': 'request_proposal', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'today'}
    run = rpc(client, 'open', {'action': action}).json()
    tool(client, run, 'get_day_context')
    db = client.app.state.database
    before = db.get_snapshot(snapshot['sessionId'])
    db.connection.execute("CREATE FUNCTION fail_final() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'final receipt failure'; END $$")
    db.connection.execute('CREATE TRIGGER fail_final BEFORE INSERT ON action_requests FOR EACH ROW EXECUTE FUNCTION fail_final()')
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).status_code == 500
    assert db.get_snapshot(snapshot['sessionId']) == before
    assert db.get_agent_run(snapshot['sessionId'], run['runId'])['status'] == 'pending'
    db.connection.execute('DROP TRIGGER fail_final ON action_requests')
    db.connection.execute('DROP FUNCTION fail_final()')
    assert rpc(client, 'finish', {'runId': run['runId'], 'output': ANSWER}).json()['reply']['result']['status'] == 'needs_input'


@pytest.mark.parametrize('output', [{**ANSWER, 'reasoning': 'secret'}, {**ANSWER, 'markdown': ''}, {**ANSWER, 'markdown': 'x' * 16001}, {'markdown': 'partial'}, 'raw-text'])
def test_invalid_output_never_leaks_or_completes(enabled_client, output):
    _, _, run = opened(enabled_client)
    tool(enabled_client, run, 'get_day_context')
    response = rpc(enabled_client, 'finish', {'runId': run['runId'], 'output': output})
    assert response.status_code == 502 and response.json()['errorCode'] == 'INVALID_MODEL_OUTPUT'
    assert enabled_client.get('/api/state').json()['messages'][-1]['content'] == ''


def test_cross_session_cannot_get_run_or_operate_it(enabled_client, client_factory):
    _, _, run = opened(enabled_client)
    other = client_factory(agent_token=TOKEN, agent_enabled=True)
    other.get('/api/state')
    assert rpc(other, 'status', {'runId': run['runId']}).status_code == 409
    assert tool(other, run, 'get_day_context').status_code == 409


async def test_one_menu_search_and_cancel_during_async_search_preserve_truth(database, tmp_path):
    class Search:
        available = True
        def __init__(self):
            self.started, self.release, self.cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
            self.calls = 0
        async def search_restaurant_menu(self, input):
            self.calls += 1
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            return {'status': 'not_found', 'results': []}
    search = Search()
    service = AgentService(database, AttachmentStore(tmp_path / 'attachments'), search, enabled=True)
    snapshot = database.create_session()
    run = service.open(snapshot['sessionId'], {'request': chat(snapshot)})
    sid = snapshot['sessionId']
    await service.tool(sid, {'runId': run['runId'], 'toolCallId': 'context', 'name': 'get_day_context', 'input': {}})
    task = asyncio.create_task(service.tool(sid, {'runId': run['runId'], 'toolCallId': 'search', 'name': 'search_restaurant_menu', 'input': {'restaurant': 'Cafe', 'city': 'HK'}}))
    await search.started.wait()
    second = await service.tool(sid, {'runId': run['runId'], 'toolCallId': 'search-2', 'name': 'search_restaurant_menu', 'input': {'restaurant': 'Cafe', 'city': 'HK'}})
    assert second['result']['errorCode'] == 'SEARCH_LIMIT_REACHED' and search.calls == 1
    service.cancel(sid, {'runId': run['runId']})
    with pytest.raises(asyncio.CancelledError):
        await task
    assert search.cancelled.is_set()
    current = database.get_snapshot(sid)
    assert current['messages'][-1]['status'] == 'stopped'
    assert all(step['status'] != 'started' for step in current['messages'][-1]['steps'])
    await service.close()


def test_main_requires_openrouter_key_and_internal_token_with_default_model():
    config = {'WELLIO_AGENT_TOKEN': TOKEN, 'OPENROUTER_API_KEY': 'offline-key'}
    assert agent_is_configured(config)
    assert agent_is_configured({**config, 'WELLIO_AI_MODEL': 'explicit-model'})
    for key in config:
        assert not agent_is_configured({name: value for name, value in config.items() if name != key})
    assert not agent_is_configured({**config, 'OPENROUTER_API_KEY': 'bad\nheader'})


def test_cancel_before_conditions_preparation_rolls_back_authorization_and_state(database, tmp_path, monkeypatch):
    service = AgentService(database, AttachmentStore(tmp_path / 'attachments'), enabled=True)
    initial = database.create_session()
    sid = initial['sessionId']
    real_write = database.with_agent_tool
    def cancel_first(context, execute):
        service.cancel(sid, {'runId': context['runId']})
        return real_write(context, execute)
    monkeypatch.setattr(database, 'with_agent_tool', cancel_first)
    with pytest.raises(BackendError, match='RUN_NOT_ACTIVE'):
        service.open(sid, {'request': chat(initial, 'Set available time to 15 minutes.')})
    assert database.get_snapshot(sid)['conditions'] == initial['conditions']
    assert database.connection.execute('SELECT count(*) AS n FROM write_authorizations').fetchone()['n'] == 0
    assert database.connection.execute('SELECT count(*) AS n FROM action_requests').fetchone()['n'] == 0
    assert database.list_agent_runs(sid)[0]['status'] == 'stopped'


async def test_mutation_checks_live_lease_inside_commit_and_rolls_back(database, tmp_path, monkeypatch):
    import wellio.agent_tools as module
    clock = [1000]
    service = AgentService(database, AttachmentStore(tmp_path / 'attachments'), enabled=True, now=lambda: clock[0])
    initial = database.create_session()
    sid = initial['sessionId']
    run = service.open(sid, {'request': chat(initial, 'Start this workout')})
    await service.tool(sid, {'runId': run['runId'], 'toolCallId': 'context', 'name': 'get_day_context', 'input': {}})
    real_progress = module.record_workout_progress
    def expire_then_write(*args):
        clock[0] = run['leaseExpiresAt'] + 1
        return real_progress(*args)
    monkeypatch.setattr(module, 'record_workout_progress', expire_then_write)
    with pytest.raises(BackendError, match='RUN_NOT_ACTIVE'):
        await service.tool(sid, {'runId': run['runId'], 'toolCallId': 'start', 'name': 'record_workout_progress', 'input': run['preparedIntent']['action']})
    assert database.get_snapshot(sid)['workout'] == initial['workout']
    assert database.connection.execute('SELECT count(*) AS n FROM action_requests').fetchone()['n'] == 0


def test_successful_ui_start_preempts_auto_atomically_but_failed_action_does_not(enabled_client):
    client = enabled_client
    initial = client.get('/api/state').json()
    auto = rpc(client, 'open', {'request': chat(initial, '', source='app_open', checkMode='auto')}).json()
    context = tool(client, auto, 'get_day_context').json()['result']
    action = {'kind': 'start_workout', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'today', 'workoutId': initial['workout']['id'], 'expectedWorkoutVersion': 99}
    assert client.post('/api/actions', json=action).status_code == 409
    assert rpc(client, 'status', {'runId': auto['runId']}).json()['active']
    action.update(requestId=str(uuid4()), expectedWorkoutVersion=1)
    started = client.post('/api/actions', json=action)
    assert started.status_code == 200
    snapshot = started.json()['snapshot']
    assert snapshot['workout']['status'] == 'in_progress'
    message = next(item for item in snapshot['messages'] if item['id'] == auto['messageId'])
    assert message['status'] == 'stopped' and message['errorCode'] == 'USER_PRIORITY'
    late = tool(client, auto, 'propose_workout', {'scope': 'workout', 'contextReadId': context['id'], 'reason': {'en': 'Review', 'zh-CN': '复查'}, 'workout': initial['workout']})
    assert late.status_code == 409 and not client.get('/api/state').json()['proposals']
    assert client.post('/api/actions', json=action).json() == started.json()


def test_pending_ui_request_hash_survives_locale_and_blocks_other_action_uuid(enabled_client):
    client = enabled_client
    snapshot = client.get('/api/state').json()
    action = {'kind': 'request_proposal', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'today'}
    run = rpc(client, 'open', {'action': action}).json()
    locale = {'kind': 'set_locale', 'requestId': action['requestId'], 'resetEpoch': 1, 'source': 'profile', 'locale': 'zh-CN'}
    assert client.post('/api/actions', json=locale).status_code == 409
    assert client.get('/api/state').json()['locale'] == snapshot['locale']
    locale['requestId'] = str(uuid4())
    assert client.post('/api/actions', json=locale).status_code == 200
    replay = rpc(client, 'open', {'action': action}).json()
    assert replay['terminal'] and replay['runId'] == run['runId']
    assert rpc(client, 'status', {'runId': run['runId']}).json()['active']


def test_equipment_tool_exposes_only_valid_gym_catalog_and_load_basis(enabled_client):
    from wellio.workouts import lookup_equipment
    _, _, run = opened(enabled_client)
    tool(enabled_client, run, 'get_day_context')
    a = tool(enabled_client, run, 'get_gym_equipment', {'gymId': 'gym-a'}).json()['result']
    b = tool(enabled_client, run, 'get_gym_equipment', {'gymId': 'gym-b'}).json()['result']
    assert len(a['catalog']) == 12 and len(b['catalog']) == 11
    assert 'pull-up' not in {item['catalogId'] for item in b['catalog']}
    for item in a['catalog'] + b['catalog']:
        equipment = lookup_equipment(item['equipmentId'])
        assert item['basis'] == equipment['load']['basis']
        assert item['unit'] == 'kg' and item['name']['en'] and item['name']['zh-CN']
        assert all(lookup_equipment(id)['gymId'] == equipment['gymId'] for id in item['requiresEquipmentIds'])
    assert next(item for item in a['catalog'] if item['catalogId'] == 'one-arm-dumbbell-row')['requiresEquipmentIds'] == ['gym-a-dumbbells', 'gym-a-bench']


def test_verified_meal_request_cannot_finish_with_a_false_saved_claim(enabled_client):
    client = enabled_client
    _, _, run = opened(client, 'I only ate half of this item', targetMealId='meal-lunch', targetMealItemId='item-lunch')
    tool(client, run, 'get_day_context')
    result = rpc(client, 'finish', {'runId': run['runId'], 'output': {**ANSWER, 'markdown': 'I saved half of your lunch.'}})
    assert result.status_code == 409 and result.json()['errorCode'] == 'REQUESTED_ACTION_NOT_ATTEMPTED'
    snapshot = client.get('/api/state').json()
    assert snapshot['meals'][1]['items'][0]['consumedFraction'] == 1
    assert snapshot['messages'][-1]['status'] == 'streaming'
