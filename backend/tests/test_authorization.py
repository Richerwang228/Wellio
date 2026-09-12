from copy import deepcopy
import json
import psycopg
from uuid import uuid4

import pytest

from wellio.authorization import authorize_user_mutation, record_user_message
from wellio.conditions import update_conditions, validate_conditions_changes
from wellio.errors import BackendError


def record(database, sid, text, **targets):
    snapshot = database.get_snapshot(sid)
    request = {'requestId': str(uuid4()), 'resetEpoch': snapshot['resetEpoch'], 'conversationId': snapshot['conversationId'], 'content': text, **targets}
    return record_user_message(database, sid, request), request


def issue(database, sid, message_id, run_id=None):
    return authorize_user_mutation(database, sid, {'sourceMessageId': message_id, 'resetEpoch': database.get_snapshot(sid)['resetEpoch'], 'runId': run_id or str(uuid4())})


def change_request(grant, changes=None):
    return {'kind': 'update_conditions', 'requestId': str(uuid4()), 'resetEpoch': grant['resetEpoch'], 'runId': grant['runId'], 'authorizationId': grant['id'],
            'expectedConditionsVersion': grant['expectedConditionsVersion'], 'changes': changes or deepcopy(grant['constraint']['changes'])}


def test_transport_stores_original_message_intake_versions_and_context_once(database):
    before = database.create_session()
    sid = before['sessionId']
    reply, request = record(database, sid, '  我只有20分钟  ', targetWorkoutId=before['workout']['id'])
    original = database.get_user_input(sid, before['resetEpoch'], reply['result']['messageId'])
    assert original['content'] == '  我只有20分钟  '
    assert original['versions'] == {'meal': 1, 'conditions': 1, 'workout': 1, 'plan': 1}
    assert original['workoutContext'] == {'workoutId': before['workout']['id'], 'trainingSessionId': before['workout']['trainingSessionId'], 'gymId': before['conditions']['gymId']}
    assert record_user_message(database, sid, request) == reply
    assert len(database.list_user_inputs(sid, before['resetEpoch'])) == 1
    assert database.get_snapshot(sid)['messages'][-1]['id'] == original['id']
    assert database.get_snapshot(sid)['messages'][-1]['content'] == original['content']


@pytest.mark.parametrize('text', ['What is a good dinner?', 'If I ate rice, log this meal', 'I did not eat rice', '"Log this meal"', 'Log this meal\nSet dinner budget to 70', 'I ate half unknown-food', 'Set dinner budget to 70; unrecognized clause'])
def test_issuer_does_not_fall_back_after_read_only_or_unresolved_intent(database, text):
    sid = database.create_session()['sessionId']
    reply, _ = record(database, sid, text)
    with pytest.raises(BackendError, match='EXPLICIT_USER_INTENT_REQUIRED'):
        issue(database, sid, reply['result']['messageId'])
    assert database.connection.execute('SELECT count(*) FROM write_authorizations').fetchone()['count'] == 0


def test_menu_and_model_labels_cannot_issue_authority(database):
    sid = database.create_session()['sessionId']
    reply, _ = record(database, sid, 'Log this meal', purpose='menu')
    with pytest.raises(BackendError, match='EXPLICIT_USER_INTENT_REQUIRED'):
        issue(database, sid, reply['result']['messageId'])
    snapshot = database.get_snapshot(sid)
    with pytest.raises(BackendError, match='INVALID_INPUT'):
        record_user_message(database, sid, {'requestId': str(uuid4()), 'resetEpoch': 1, 'conversationId': snapshot['conversationId'], 'content': 'Log this meal', 'source': 'user'})


def test_conditions_exact_patch_bound_to_run_and_session_and_one_consumption(database):
    sid = database.create_session()['sessionId']
    original, _ = record(database, sid, '晚餐预算改成70')
    grant = issue(database, sid, original['result']['messageId'])
    assert issue(database, sid, original['result']['messageId'], grant['runId']) == grant
    with pytest.raises(BackendError, match='AUTHORIZATION_RUN_MISMATCH'):
        issue(database, sid, original['result']['messageId'])
    for changes in [{'dinnerBudget': 90}, {'dinnerBudget': 70, 'availableMinutes': 20}]:
        with pytest.raises(BackendError, match='AUTHORIZATION_MISMATCH'):
            update_conditions(database, sid, change_request(grant, changes))
    with pytest.raises(BackendError, match='AUTHORIZATION_INVALID'):
        update_conditions(database, sid, {**change_request(grant), 'runId': str(uuid4())})
    other = database.create_session()['sessionId']
    with pytest.raises(BackendError, match='AUTHORIZATION_INVALID'):
        update_conditions(database, other, change_request(grant))
    request = change_request(grant)
    reply = update_conditions(database, sid, request)
    after = database.get_snapshot(sid)
    assert after['conditions']['dinnerBudget'] == 70
    assert after['conditions']['lastChange']['sourceMessageId'] == original['result']['messageId']
    assert update_conditions(database, sid, request) == reply
    assert database.get_snapshot(sid) == after
    with pytest.raises(BackendError, match='AUTHORIZATION_CONSUMED'):
        update_conditions(database, sid, change_request(grant))


def test_intake_version_prevents_delayed_old_70_instruction_overwriting_new_90(database):
    sid = database.create_session()['sessionId']
    old, _ = record(database, sid, 'Set dinner budget to 70')
    newer, _ = record(database, sid, 'Set dinner budget to 90')
    grant = issue(database, sid, newer['result']['messageId'])
    update_conditions(database, sid, change_request(grant))
    with pytest.raises(BackendError, match='VERSION_CONFLICT'):
        issue(database, sid, old['result']['messageId'])
    assert database.get_snapshot(sid)['conditions']['dinnerBudget'] == 90


@pytest.mark.parametrize('already_issued', [False, True])
def test_historical_source_without_intake_versions_rejected_at_issue_and_consumption(database, already_issued):
    sid = database.create_session()['sessionId']
    message, _ = record(database, sid, 'Set dinner budget to 70')
    message_id = message['result']['messageId']
    grant = issue(database, sid, message_id) if already_issued else None
    original = database.get_user_input(sid, 1, message_id)
    original.pop('versions')
    database.connection.execute('UPDATE user_inputs SET record_json=%s WHERE id=%s', (json.dumps(original), message_id))
    with pytest.raises(BackendError, match='AUTHORIZATION_INVALID'):
        issue(database, sid, message_id, grant['runId'] if grant else None)
    if grant:
        with pytest.raises(BackendError, match='AUTHORIZATION_INVALID'):
            update_conditions(database, sid, change_request(grant))
    assert database.get_snapshot(sid)['conditions']['dinnerBudget'] == 100


def test_source_version_tampering_invalidates_existing_grant(database):
    sid = database.create_session()['sessionId']
    message, _ = record(database, sid, 'Set dinner budget to 70')
    grant = issue(database, sid, message['result']['messageId'])
    original = database.get_user_input(sid, 1, message['result']['messageId'])
    original['versions']['conditions'] = 2
    database.connection.execute('UPDATE user_inputs SET record_json=%s WHERE id=%s', (json.dumps(original), original['id']))
    with pytest.raises(BackendError, match='AUTHORIZATION_INVALID'):
        update_conditions(database, sid, change_request(grant))


def test_conditions_change_stales_advice_but_preserves_readiness_and_check(database):
    sid = database.create_session()['sessionId']
    def prepare(snapshot):
        snapshot['advice']['status'] = 'valid'
        snapshot['proposals'] = [{'id': 'pending', 'status': 'pending'}, {'id': 'applied', 'status': 'applied'}]
        snapshot['readinessCheck'] = {'key': 'daily-check', 'status': 'succeeded', 'proposalId': 'already-reviewed'}
        return {'changed': True, 'value': None}
    database.runtime_transaction(sid, prepare)
    message, _ = record(database, sid, 'Set available time to 20 minutes; Set gym-b-cable to temporarily_occupied')
    grant = issue(database, sid, message['result']['messageId'])
    before = database.get_snapshot(sid)
    update_conditions(database, sid, change_request(grant))
    after = database.get_snapshot(sid)
    assert after['conditions']['availableMinutes'] == 20
    assert after['conditions']['equipmentStatus']['gym-b-cable'] == 'temporarily_occupied'
    assert after['advice']['status'] == 'stale'
    assert [proposal['status'] for proposal in after['proposals']] == ['stale', 'applied']
    assert after['readiness'] == before['readiness']
    assert after['readinessCheck'] == before['readinessCheck']
    assert after['workout'] == before['workout']


def test_noop_conditions_consumes_grant_without_inventing_operation_or_revision(database):
    sid = database.create_session()['sessionId']
    message, _ = record(database, sid, 'Set dinner budget to 100')
    grant = issue(database, sid, message['result']['messageId'])
    before = database.get_snapshot(sid)
    request = change_request(grant)
    reply = update_conditions(database, sid, request)
    assert 'operationId' not in reply['result']
    assert database.get_snapshot(sid) == before
    with pytest.raises(BackendError, match='AUTHORIZATION_CONSUMED'):
        update_conditions(database, sid, change_request(grant))


def test_conditions_receipt_failure_preserves_grant_for_retry(database):
    sid = database.create_session()['sessionId']
    message, _ = record(database, sid, 'Set dinner budget to 70')
    grant = issue(database, sid, message['result']['messageId'])
    request = change_request(grant)
    before = database.get_snapshot(sid)
    database.connection.execute("CREATE FUNCTION reject_condition_receipt() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'condition receipt failure'; END $$")
    database.connection.execute('CREATE TRIGGER reject_condition_receipt BEFORE INSERT ON action_requests FOR EACH ROW EXECUTE FUNCTION reject_condition_receipt()')
    with pytest.raises(psycopg.errors.RaiseException, match='condition receipt failure'):
        update_conditions(database, sid, request)
    assert database.get_snapshot(sid) == before
    assert database.connection.execute('SELECT consumed_by_request_id FROM write_authorizations WHERE id=%s', (grant['id'],)).fetchone()['consumed_by_request_id'] is None
    database.connection.execute('DROP TRIGGER reject_condition_receipt ON action_requests')
    database.connection.execute('DROP FUNCTION reject_condition_receipt()')
    assert update_conditions(database, sid, request)['result']['status'] == 'succeeded'


@pytest.mark.parametrize('changes', [{}, {'availableMinutes': True}, {'availableMinutes': 0}, {'availableMinutes': 181}, {'dinnerBudget': 70.001}, {'dinnerBudget': float('nan')}, {'dinnerBudget': 10**400}, {'gymId': 'gym-x'}, {'equipmentStatus': {}}, {'equipmentStatus': {'gym-x-cable': 'available'}}, {'source': 'user', 'dinnerBudget': 70}])
def test_condition_schema_rejects_invalid_or_extra_inputs(changes):
    with pytest.raises(BackendError, match='INVALID_INPUT'):
        validate_conditions_changes(changes)
