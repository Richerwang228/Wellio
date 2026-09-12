"""Short PostgreSQL transactions for agent leases, message identity and receipts."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from uuid import uuid4

from .database import context_versions
from .errors import BackendError
from .runtime import _public_check, _stop_record, _valid_readiness, readiness_key, synchronize_runtime
from .user_intent import derive_user_intent
from .validation import canonical_json


def claim_run(db, session_id, request, timeout_ms, now, source=None, requested_gym_id=None, action_request=None):
    source = source or request['source']

    def claim(snapshot):
        if snapshot['resetEpoch'] != request['resetEpoch']:
            raise BackendError('STALE_EPOCH', 409)
        if snapshot['conversationId'] != request['conversationId']:
            raise BackendError('CONVERSATION_MISMATCH', 409)
        if action_request:
            db.get_mutation_reply(session_id, action_request)
        identity = action_request or {**request, 'actualSource': source, **({'requestedGymId': requested_gym_id} if requested_gym_id else {})}
        digest = sha256(canonical_json(identity).encode()).hexdigest()
        prior = db.find_agent_run(session_id, request['requestId'])
        if prior:
            if prior['payloadHash'] != digest:
                raise BackendError('IDEMPOTENCY_CONFLICT', 409)
            return {'value': {'type': 'replay', 'run': prior}, 'changed': False}
        check = None
        if source == 'app_open':
            check = db.get_readiness_check(session_id, readiness_key(snapshot))
            outcome = ('not_available' if not snapshot['capabilities']['agent'] or not _valid_readiness(snapshot) or not check or check['status'] == 'unavailable'
                       else 'in_progress' if check['status'] == 'pending' else 'reused' if check['status'] in ('completed', 'applied', 'dismissed')
                       else 'not_needed' if check['status'] in ('failed', 'stopped') and request.get('checkMode') != 'retry' else None)
            if outcome:
                return {'value': {'type': 'check', 'outcome': outcome, 'snapshot': snapshot}, 'changed': False}
        for other in db.list_agent_runs(session_id):
            if other['resetEpoch'] != snapshot['resetEpoch'] or other['status'] != 'pending':
                continue
            if source != 'app_open' and other['source'] == 'app_open':
                _stop_record(db, snapshot, other, 'stopped', 'USER_PRIORITY')
            elif source == 'app_open':
                return {'value': {'type': 'check', 'outcome': 'not_needed', 'snapshot': snapshot}, 'changed': False}
            else:
                raise BackendError('RUN_IN_PROGRESS', 409)
        run = {'id': str(uuid4()), 'sessionId': session_id, 'requestId': request['requestId'], 'resetEpoch': request['resetEpoch'],
               'payloadHash': digest, 'request': deepcopy(request), 'source': source, 'messageId': str(uuid4()), 'status': 'pending',
               'leaseExpiresAt': now + timeout_ms + 2000, 'searchUsed': False, 'toolIds': []}
        if action_request:
            run['actionRequest'] = deepcopy(action_request)
        if requested_gym_id:
            run['requestedGymId'] = requested_gym_id
        created = datetime.fromtimestamp(now / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        workout = snapshot.get('workout')
        if source == 'user':
            if request.get('targetMealId') and not any(meal['id'] == request['targetMealId'] for meal in snapshot['meals']):
                raise BackendError('NOT_FOUND', 404)
            if request.get('targetWorkoutId') and (workout or {}).get('id') != request['targetWorkoutId']:
                raise BackendError('NOT_FOUND', 404)
            if request.get('targetExerciseId') and not any(exercise['id'] == request['targetExerciseId'] for exercise in (workout or {}).get('exercises', [])):
                raise BackendError('NOT_FOUND', 404)
            if request.get('targetMealItemId') and not any(item['id'] == request['targetMealItemId'] for meal in snapshot['meals'] if not request.get('targetMealId') or meal['id'] == request['targetMealId'] for item in meal['items']):
                raise BackendError('NOT_FOUND', 404)
            original = {'id': str(uuid4()), 'sessionId': session_id, 'requestId': 'chat-input-' + run['id'], 'resetEpoch': snapshot['resetEpoch'],
                        'conversationId': snapshot['conversationId'], 'content': request['message'], 'createdAt': created,
                        'versions': {'meal': snapshot['mealRevision'], 'conditions': snapshot['conditions']['version'], 'workout': workout['version'] if workout else 0, 'plan': snapshot['plan']['version']},
                        'attachmentIds': request['attachmentIds']}
            for key in ('purpose', 'targetMealId', 'targetMealItemId', 'targetWorkoutId', 'targetExerciseId', 'targetOperationId'):
                if key in request:
                    original[key] = request[key]
            if workout:
                original['workoutContext'] = {'workoutId': workout['id'], 'trainingSessionId': workout['trainingSessionId'], 'gymId': snapshot['conditions']['gymId']}
            db.store_user_input(original)
            run['sourceMessageId'] = original['id']
            # Derive once from reception state. Later tool calls never reinterpret a pronoun.
            run['preparedIntent'] = derive_user_intent(snapshot, original)
            user_message = {'id': original['id'], 'role': 'user', 'source': 'user', 'content': original['content'], 'createdAt': created, 'status': 'complete', 'steps': []}
            if original.get('targetMealId'):
                user_message['mealId'] = original['targetMealId']
            if request['attachmentIds']:
                user_message['attachmentUrl'] = '/api/attachments/' + request['attachmentIds'][0]
            snapshot['messages'].append(user_message)
        message = {'id': run['messageId'], 'role': 'assistant', 'source': 'app_open' if source == 'app_open' else 'agent', 'content': '', 'createdAt': created, 'status': 'streaming', 'steps': []}
        snapshot['messages'].append(message)
        from .agent_knowledge import knowledge_policy
        run['knowledgePolicy'] = knowledge_policy(run)
        if check:
            check.update(status='pending', runId=run['id'], attemptId=str(uuid4()), messageId=message['id'], leaseExpiresAt=run['leaseExpiresAt'])
            check.pop('errorCode', None)
            check.pop('proposalId', None)
            run.update(checkKey=check['key'], checkAttemptId=check['attemptId'])
            snapshot['readinessCheck'] = _public_check(check)
            snapshot['advice'] = {'status': 'pending', 'messageId': message['id']}
            db.save_readiness_check(check)
        db.save_agent_run(run)
        return {'value': {'type': 'run', 'run': run}, 'changed': True}

    return db.runtime_transaction(session_id, claim)


def assert_run(db, run, now):
    now = now() if callable(now) else now
    snapshot = db.get_snapshot(run['sessionId'])
    if snapshot['resetEpoch'] != run['resetEpoch']:
        raise BackendError('STALE_EPOCH', 409)
    current = db.get_agent_run(run['sessionId'], run['id'])
    if not current or current['status'] != 'pending' or current['leaseExpiresAt'] <= now:
        raise BackendError('RUN_NOT_ACTIVE', 409)
    return current


def update_run(db, run, now, update):
    def change(snapshot):
        current = assert_run(db, run, now)
        message = next((item for item in snapshot['messages'] if item['id'] == current['messageId']), None)
        if message is None:
            raise BackendError('RUN_NOT_ACTIVE', 409)
        update(snapshot, current, message)
        assert_run(db, run, now)
        db.save_agent_run(current)
        return {'value': snapshot, 'changed': True}
    return db.runtime_transaction(run['sessionId'], change)


def save_tool_step(db, run, step, now, *, initial=False):
    def change(snapshot, current, message):
        index = next((i for i, item in enumerate(message['steps']) if item.get('toolCallId') == step['toolCallId']), -1)
        if initial and step['toolCallId'] in current['toolIds']:
            raise BackendError('TOOL_CALL_ID_REUSED', 409)
        if index < 0:
            message['steps'].append(step)
        else:
            message['steps'][index] = step
        if step['toolCallId'] not in current['toolIds']:
            current['toolIds'].append(step['toolCallId'])
        if len(current['toolIds']) > 48:
            raise BackendError('TOOL_LIMIT_EXCEEDED', 429)
    return update_run(db, run, now, change)


def finish_run(db, run, output, now, evidence_validator=None):
    def finish(snapshot, current, message):
        if not current.get('lastContextReadId'):
            raise BackendError('CONTEXT_READ_REQUIRED', 409)
        context = db.get_context_read(run['sessionId'], current['lastContextReadId'], run['id'], run['resetEpoch'])
        sources = evidence_validator(current) if evidence_validator else []
        from .agent_knowledge import cited_markdown
        markdown = cited_markdown(output['markdown'], sources, current['request']['locale']) if sources else output['markdown']
        message.update(content=markdown, status='complete')
        if sources:
            message.update(sources=sources, evidenceReadId=current['knowledgeRead']['id'], knowledgeVersion=current['knowledgeRead']['knowledgeVersion'])
        message.pop('phase', None)
        current['status'] = 'completed'
        current['outputHash'] = sha256(canonical_json(output).encode()).hexdigest()
        updates = {domain: {locale: output[key] for locale in ('en', 'zh-CN')}
                   for domain, key in (('training', 'trainingSummary'), ('nutrition', 'nutritionSummary')) if output[key] is not None}
        if updates:
            previous = snapshot['advice']
            retained = {key: previous[key] for key in ('training', 'nutrition') if key in previous} if previous.get('status') == 'valid' and previous.get('versions') == context_versions(snapshot) else {}
            snapshot['advice'] = {'status': 'valid', **retained, **updates, 'messageId': message['id'],
                                  'contextReadId': context['id'], 'versions': context_versions(snapshot)}
        elif snapshot['advice'].get('messageId') == message['id'] and snapshot['advice']['status'] == 'pending':
            snapshot['advice'] = {'status': 'unavailable'}
        if sources and updates:
            snapshot['advice'].update(evidenceReadId=current['knowledgeRead']['id'], knowledgeVersion=current['knowledgeRead']['knowledgeVersion'], sources=sources)
        if current.get('checkKey'):
            check = db.get_readiness_check(run['sessionId'], current['checkKey'])
            if check and check.get('attemptId') == current['checkAttemptId'] and check['status'] == 'pending':
                check['status'] = 'completed'
                db.save_readiness_check(check)
                if snapshot.get('readinessCheck', {}).get('key') == check['key']:
                    snapshot['readinessCheck'] = _public_check(check)
        if current.get('actionRequest'):
            snapshot['revision'] += 1
            result = {'status': 'succeeded', 'proposalId': current['proposalId']} if current.get('proposalId') else {'status': 'needs_input', 'errorCode': 'PROPOSAL_NOT_CREATED'}
            db.store_agent_action_reply(snapshot, current['actionRequest'], result)
    return update_run(db, run, now, finish)


def fail_run(db, run, status, code):
    def fail(snapshot):
        if snapshot['resetEpoch'] != run['resetEpoch']:
            return {'value': None, 'changed': False}
        current = db.get_agent_run(run['sessionId'], run['id'])
        if not current or current['status'] != 'pending':
            return {'value': snapshot, 'changed': False}
        _stop_record(db, snapshot, current, status, code)
        return {'value': snapshot, 'changed': True}
    return db.runtime_transaction(run['sessionId'], fail)
