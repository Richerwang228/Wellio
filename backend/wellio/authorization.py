"""Trusted user transport and authorization issuance; never a public model tool."""
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from .conditions import validate_id, validate_object, validate_version
from .errors import BackendError
from .user_intent import derive_user_intent


TARGETS = ('targetMealId', 'targetMealItemId', 'targetWorkoutId', 'targetExerciseId', 'targetOperationId')


def record_user_message(database, session_id, value):
    request = validate_object(value, {'requestId', 'resetEpoch', 'conversationId', 'content'}, {*TARGETS, 'attachmentIds', 'purpose'})
    validate_id(request['requestId'])
    validate_id(request['conversationId'])
    request['resetEpoch'] = validate_version(request['resetEpoch'])
    if not isinstance(request['content'], str) or not 1 <= len(request['content']) <= 4_000 or not request['content'].strip():
        raise BackendError('INVALID_INPUT', 400)
    for key in TARGETS:
        if key in request:
            validate_id(request[key])
    if 'purpose' in request and request['purpose'] not in ('food', 'menu'):
        raise BackendError('INVALID_INPUT', 400)
    if 'attachmentIds' in request:
        if not isinstance(request['attachmentIds'], list) or len(request['attachmentIds']) > 4:
            raise BackendError('INVALID_INPUT', 400)
        for attachment_id in request['attachmentIds']:
            validate_id(attachment_id)
    request['kind'] = 'record_user_message'

    def transition(snapshot):
        if snapshot['conversationId'] != request['conversationId']:
            raise BackendError('CONVERSATION_MISMATCH', 409)
        workout = snapshot.get('workout')
        if request.get('targetMealId') and not any(meal['id'] == request['targetMealId'] for meal in snapshot['meals']):
            raise BackendError('NOT_FOUND', 404)
        if request.get('targetMealItemId') and not any(item['id'] == request['targetMealItemId'] for meal in snapshot['meals'] if not request.get('targetMealId') or meal['id'] == request['targetMealId'] for item in meal['items']):
            raise BackendError('NOT_FOUND', 404)
        if request.get('targetWorkoutId') and (not workout or workout['id'] != request['targetWorkoutId']):
            raise BackendError('NOT_FOUND', 404)
        if request.get('targetExerciseId') and not any(item['id'] == request['targetExerciseId'] for item in (workout or {}).get('exercises', [])):
            raise BackendError('NOT_FOUND', 404)
        source = {key: deepcopy(item) for key, item in request.items() if key != 'kind'}
        source.update(id=str(uuid4()), sessionId=session_id, createdAt=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                      versions={'meal': snapshot['mealRevision'], 'conditions': snapshot['conditions']['version'], 'workout': workout['version'] if workout else 0, 'plan': snapshot['plan']['version']})
        if workout:
            source['workoutContext'] = {'workoutId': workout['id'], 'trainingSessionId': workout['trainingSessionId'], 'gymId': snapshot['conditions']['gymId']}
        database.store_user_input(source)
        message = {'id': source['id'], 'role': 'user', 'source': 'user', 'content': source['content'], 'createdAt': source['createdAt'], 'status': 'complete', 'steps': []}
        if source.get('targetMealId'):
            message['mealId'] = source['targetMealId']
        snapshot['messages'].append(message)
        snapshot['revision'] += 1
        return {'httpStatus': 200, 'result': {'requestId': request['requestId'], 'status': 'succeeded', 'messageId': source['id']}, 'snapshot': snapshot}

    return database.mutate(session_id, request, transition)


def authorize_user_mutation(database, session_id, value):
    request = validate_object(value, {'sourceMessageId', 'resetEpoch', 'runId'})
    validate_id(request['sourceMessageId'])
    validate_id(request['runId'])
    request['resetEpoch'] = validate_version(request['resetEpoch'])

    def derive(snapshot, source):
        intent = derive_user_intent(snapshot, source)
        if intent['kind'] == 'meal':
            return intent['constraint']
        if intent['kind'] == 'conditions':
            return {'scope': 'conditions_update', 'changes': intent['changes']}
        # The unified parser includes the earlier canonical grammar; a fallback must not bypass a denial.
        raise BackendError('EXPLICIT_USER_INTENT_REQUIRED', 422)

    return database.issue_authorization(session_id, request, derive)
