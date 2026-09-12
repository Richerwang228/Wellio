from copy import deepcopy
import psycopg
from uuid import uuid4

import pytest

from wellio.authorization import authorize_user_mutation, record_user_message
from wellio.errors import BackendError
from wellio.meals import mutate_meal_log, undo_meal, validate_meal_input


def authorize(database, sid, content, **targets):
    snapshot = database.get_snapshot(sid)
    reply = record_user_message(database, sid, {'requestId': str(uuid4()), 'resetEpoch': snapshot['resetEpoch'], 'conversationId': snapshot['conversationId'], 'content': content, **targets})
    return authorize_user_mutation(database, sid, {'sourceMessageId': reply['result']['messageId'], 'resetEpoch': snapshot['resetEpoch'], 'runId': str(uuid4())})


def request_for(grant, **payload):
    request = {'kind': 'mutate_meal_log', 'requestId': str(uuid4()), 'resetEpoch': grant['resetEpoch'], 'runId': grant['runId'], 'authorizationId': grant['id'], 'expectedMealRevision': grant['expectedMealRevision'], **payload}
    if 'expectedMealVersion' in grant:
        request['expectedMealVersion'] = grant['expectedMealVersion']
    return request


def meal_input():
    items = []
    for name, zh, kcal, protein in [('Chicken breast', '鸡胸肉', 200, 40), ('Rice', '米饭', 300, 6), ('Fries', '薯条', 400, 4)]:
        items.append({'name': {'en': name, 'zh-CN': zh}, 'portion': {'en': '100 g', 'zh-CN': '100 克'}, 'originalPortion': {'quantity': 100, 'unit': 'g'},
                      'base': {'kcal': kcal, 'protein': protein, 'carbs': 20, 'fat': 10}, 'nutrientUnits': {'energy': 'kcal', 'mass': 'g'}, 'consumedFraction': 1, 'estimated': True})
    return {'period': 'dinner', 'time': '19:00', 'items': items}


def add_meal(database, sid):
    grant = authorize(database, sid, 'Log this meal')
    request = request_for(grant, action='add', meal=meal_input())
    reply = mutate_meal_log(database, sid, request)
    return database.get_snapshot(sid)['meals'][-1], reply, request


def change_item(database, sid, meal, item, fraction):
    grant = authorize(database, sid, f'I ate {fraction * 100:g}% of this item', targetMealId=meal['id'], targetMealItemId=item['id'])
    request = request_for(grant, action='update', mealId=meal['id'], mealItemId=item['id'], changes={'consumedFraction': fraction})
    return mutate_meal_log(database, sid, request), request, grant


def undo(database, sid, operation_id):
    return undo_meal(database, sid, {'kind': 'undo_meal', 'source': 'today', 'resetEpoch': database.get_snapshot(sid)['resetEpoch'], 'requestId': str(uuid4()), 'operationId': operation_id})


def test_add_receipt_replay_has_one_meal_operation_and_authorization(database):
    snapshot = database.create_session()
    sid = snapshot['sessionId']
    meal, reply, request = add_meal(database, sid)
    after = database.get_snapshot(sid)
    assert len(after['meals']) == len(snapshot['meals']) + 1
    assert reply['result']['nutrition']['meal']['total']['kcal'] == 900
    assert reply['result']['nutrition']['day']['consumed']['kcal'] == 2550
    assert len({item['id'] for item in meal['items']}) == 3
    assert mutate_meal_log(database, sid, request) == reply
    assert database.get_snapshot(sid) == after
    assert database.connection.execute('SELECT count(*) FROM meal_operations').fetchone()['count'] == 1
    with pytest.raises(BackendError, match='AUTHORIZATION_CONSUMED'):
        mutate_meal_log(database, sid, {**request, 'requestId': str(uuid4())})


def test_fraction_update_is_absolute_noop_consumes_grant_and_preserves_other_items(database):
    sid = database.create_session()['sessionId']
    meal, _, _ = add_meal(database, sid)
    original_other_items = deepcopy(meal['items'][1:])
    reply, _, _ = change_item(database, sid, meal, meal['items'][0], .5)
    current = database.get_snapshot(sid)['meals'][-1]
    assert current['items'][0]['consumedFraction'] == .5
    assert current['items'][1:] == original_other_items
    assert reply['result']['nutrition']['meal']['items'][0]['total']['kcal'] == 100
    assert reply['result']['nutrition']['meal']['total']['kcal'] == 800
    grant = authorize(database, sid, '鸡胸肉我只吃了一半')
    before_noop = database.get_snapshot(sid)
    request = request_for(grant, action='update', mealId=meal['id'], mealItemId=meal['items'][0]['id'], changes={'consumedFraction': .5})
    noop = mutate_meal_log(database, sid, request)
    assert 'operationId' not in noop['result']
    assert database.get_snapshot(sid) == before_noop
    assert database.connection.execute('SELECT consumed_by_request_id FROM write_authorizations WHERE id=%s', (grant['id'],)).fetchone()['consumed_by_request_id'] == request['requestId']
    assert current['version'] == before_noop['meals'][-1]['version']


def test_baseline_correction_preserves_fraction_and_uses_server_totals(database):
    sid = database.create_session()['sessionId']
    meal, _, _ = add_meal(database, sid)
    change_item(database, sid, meal, meal['items'][0], .5)
    grant = authorize(database, sid, 'Correct Chicken breast: 200 g; 240 kcal; protein 44 g; carbs 3 g; fat 4 g')
    baseline = grant['constraint']['changes']['baseline']
    reply = mutate_meal_log(database, sid, request_for(grant, action='update', mealId=meal['id'], mealItemId=meal['items'][0]['id'], changes={'baseline': baseline}))
    current = database.get_snapshot(sid)['meals'][-1]['items'][0]
    assert current['consumedFraction'] == .5
    assert current['originalPortion'] == {'quantity': 200, 'unit': 'g'}
    assert reply['result']['nutrition']['meal']['items'][0]['total'] == {'kcal': 120, 'protein': 22, 'carbs': 1.5, 'fat': 2}


def test_single_item_delete_and_tombstone_restore_monotonic_undo_chain(database):
    sid = database.create_session()['sessionId']
    meal, added, _ = add_meal(database, sid)
    update, _, _ = change_item(database, sid, meal, meal['items'][0], .5)
    grant = authorize(database, sid, 'Delete Fries')
    removed = mutate_meal_log(database, sid, request_for(grant, action='delete', mealId=meal['id'], mealItemId=meal['items'][2]['id']))
    assert len(database.get_snapshot(sid)['meals'][-1]['items']) == 2
    with pytest.raises(BackendError, match='UNDO_CONFLICT'):
        undo(database, sid, update['result']['operationId'])
    grant = authorize(database, sid, 'Delete this meal', targetMealId=meal['id'])
    deleted = mutate_meal_log(database, sid, request_for(grant, action='delete', mealId=meal['id']))
    assert all(item['id'] != meal['id'] for item in database.get_snapshot(sid)['meals'])
    assert deleted['result']['nutrition']['meal'] is None
    undo(database, sid, deleted['result']['operationId'])
    restored = database.get_snapshot(sid)['meals'][-1]
    assert restored['version'] == 5 and len(restored['items']) == 2
    assert restored['operationId'] == removed['result']['operationId']
    undo(database, sid, removed['result']['operationId'])
    restored = database.get_snapshot(sid)['meals'][-1]
    assert restored['version'] == 6 and len(restored['items']) == 3
    undo(database, sid, update['result']['operationId'])
    restored = database.get_snapshot(sid)['meals'][-1]
    assert restored['version'] == 7 and restored['items'][0]['consumedFraction'] == 1
    before_repeat = database.get_snapshot(sid)
    undo(database, sid, update['result']['operationId'])
    assert database.get_snapshot(sid) == before_repeat
    undo(database, sid, added['result']['operationId'])
    assert all(item['id'] != meal['id'] for item in database.get_snapshot(sid)['meals'])
    entity = database.connection.execute('SELECT version,head_operation_id FROM meal_entities WHERE meal_id=%s', (meal['id'],)).fetchone()
    assert entity == {'version': 8, 'head_operation_id': None}


def test_deleting_last_item_removes_meal_and_undo_restores_seed_business_values(database):
    initial = database.create_session()
    sid = initial['sessionId']
    meal, item = initial['meals'][0], initial['meals'][0]['items'][0]
    grant = authorize(database, sid, 'Delete this item', targetMealId=meal['id'], targetMealItemId=item['id'])
    reply = mutate_meal_log(database, sid, request_for(grant, action='delete', mealId=meal['id'], mealItemId=item['id']))
    assert len(database.get_snapshot(sid)['meals']) == 1
    undo(database, sid, reply['result']['operationId'])
    restored = database.get_snapshot(sid)['meals'][0]
    assert restored == {**meal, 'version': 3}


def test_receipt_failure_rolls_back_grant_operation_entity_and_snapshot(database):
    sid = database.create_session()['sessionId']
    grant = authorize(database, sid, 'Log this meal')
    request = request_for(grant, action='add', meal=meal_input())
    before = database.get_snapshot(sid)
    database.connection.execute("CREATE FUNCTION fail_meal_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'receipt failure'; END $$")
    database.connection.execute('CREATE TRIGGER fail_meal_receipt BEFORE INSERT ON action_requests FOR EACH ROW EXECUTE FUNCTION fail_meal_receipt()')
    with pytest.raises(psycopg.errors.RaiseException, match='receipt failure'):
        mutate_meal_log(database, sid, request)
    assert database.get_snapshot(sid) == before
    assert database.connection.execute('SELECT count(*) FROM meal_operations').fetchone()['count'] == 0
    assert database.connection.execute('SELECT count(*) FROM meal_entities').fetchone()['count'] == 0
    assert database.connection.execute('SELECT consumed_by_request_id FROM write_authorizations WHERE id=%s', (grant['id'],)).fetchone()['consumed_by_request_id'] is None
    database.connection.execute('DROP TRIGGER fail_meal_receipt ON action_requests')
    database.connection.execute('DROP FUNCTION fail_meal_receipt()')
    assert mutate_meal_log(database, sid, request)['result']['status'] == 'succeeded'


def test_undo_receipt_failure_rolls_back_business_state_head_and_operation_status(database):
    sid = database.create_session()['sessionId']
    meal, added, _ = add_meal(database, sid)
    operation_id = added['result']['operationId']
    before = database.get_snapshot(sid)
    database.connection.execute("CREATE FUNCTION fail_undo_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'undo receipt failure'; END $$")
    database.connection.execute('CREATE TRIGGER fail_undo_receipt BEFORE INSERT ON action_requests FOR EACH ROW EXECUTE FUNCTION fail_undo_receipt()')
    with pytest.raises(psycopg.errors.RaiseException, match='undo receipt failure'):
        undo(database, sid, operation_id)
    assert database.get_snapshot(sid) == before
    with database.transaction():
        assert database.get_meal_operation(sid, 1, operation_id)['status'] == 'applied'
        assert database.get_meal_entity(sid, 1, meal['id']) == {'mealId': meal['id'], 'version': 1, 'headOperationId': operation_id}
    database.connection.execute('DROP TRIGGER fail_undo_receipt ON action_requests')
    database.connection.execute('DROP FUNCTION fail_undo_receipt()')
    undo(database, sid, operation_id)
    assert all(item['id'] != meal['id'] for item in database.get_snapshot(sid)['meals'])


def test_delayed_meal_source_cannot_upgrade_to_current_revision(database):
    snapshot = database.create_session()
    sid = snapshot['sessionId']
    old = record_user_message(database, sid, {'requestId': str(uuid4()), 'resetEpoch': 1, 'conversationId': snapshot['conversationId'],
                                             'content': 'I ate half of this item', 'targetMealId': 'meal-lunch', 'targetMealItemId': 'item-lunch'})
    add_meal(database, sid)
    with pytest.raises(BackendError, match='VERSION_CONFLICT'):
        authorize_user_mutation(database, sid, {'sourceMessageId': old['result']['messageId'], 'resetEpoch': 1, 'runId': str(uuid4())})
    assert database.get_snapshot(sid)['meals'][1]['items'][0]['consumedFraction'] == 1


def test_undo_is_scoped_to_session_and_rejects_non_user_surface(database):
    sid = database.create_session()['sessionId']
    _, reply, _ = add_meal(database, sid)
    other = database.create_session()['sessionId']
    with pytest.raises(BackendError, match='NOT_FOUND'):
        undo(database, other, reply['result']['operationId'])
    with pytest.raises(BackendError, match='MEAL_REQUIRES_USER_ACTION'):
        undo_meal(database, sid, {'kind': 'undo_meal', 'requestId': str(uuid4()), 'resetEpoch': 1, 'source': 'app_open', 'operationId': reply['result']['operationId']})


@pytest.mark.parametrize('bad', [True, -1, float('nan'), float('inf'), 10**400])
def test_nutrition_and_fraction_reject_invalid_numeric_inputs(bad):
    value = meal_input()
    value['items'][0]['base']['kcal'] = bad
    with pytest.raises(BackendError, match='INVALID_INPUT'):
        validate_meal_input(value)


def test_meal_does_not_accept_model_source_as_authority(database):
    sid = database.create_session()['sessionId']
    grant = authorize(database, sid, 'Log this meal')
    request = request_for(grant, action='add', meal=meal_input(), source='user')
    with pytest.raises(BackendError, match='INVALID_INPUT'):
        mutate_meal_log(database, sid, request)
    forged = request_for({**grant, 'id': 'model-invented-token'}, action='add', meal=meal_input())
    with pytest.raises(BackendError, match='AUTHORIZATION_INVALID'):
        mutate_meal_log(database, sid, forged)
