"""Explicit condition patches and strict JSON helpers; no model-provided authority."""
from copy import deepcopy
from decimal import Decimal
import math
import re
from uuid import uuid4

from .errors import BackendError

MAX_SAFE_INTEGER = 9_007_199_254_740_991


def validate_object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise BackendError('INVALID_INPUT', 400)
    return deepcopy(value)


def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise BackendError('INVALID_INPUT', 400)
    return value


def validate_number(value, minimum, maximum, *, integer=False, positive=False):
    if type(value) not in (int, float) or value < minimum or value > maximum or not math.isfinite(value) or (positive and value <= 0):
        raise BackendError('INVALID_INPUT', 400)
    if integer and value != int(value):
        raise BackendError('INVALID_INPUT', 400)
    return int(value) if integer else value


def validate_version(value):
    return validate_number(value, 1, MAX_SAFE_INTEGER, integer=True)


def validate_localized(value, maximum=200):
    result = validate_object(value, {'en', 'zh-CN'})
    for key in result:
        if not isinstance(result[key], str) or not 1 <= len(result[key].strip()) <= maximum:
            raise BackendError('INVALID_INPUT', 400)
        result[key] = result[key].strip()
    return result


def invalidate_workout_advice(snapshot):
    for proposal in snapshot.get('proposals', []):
        if proposal['status'] == 'pending':
            proposal['status'] = 'stale'
    if snapshot.get('advice', {}).get('status') == 'valid':
        snapshot['advice']['status'] = 'stale'


def validate_conditions_changes(value):
    from .workouts import lookup_equipment

    result = validate_object(value, (), {'availableMinutes', 'dinnerBudget', 'gymId', 'equipmentStatus'})
    if not result:
        raise BackendError('INVALID_INPUT', 400)
    if 'availableMinutes' in result:
        result['availableMinutes'] = validate_number(result['availableMinutes'], 1, 180, integer=True)
    if 'dinnerBudget' in result:
        number = validate_number(result['dinnerBudget'], 0, 10_000)
        if Decimal(str(number)) % Decimal('0.01') != 0:
            raise BackendError('INVALID_INPUT', 400)
    if 'gymId' in result and result['gymId'] not in ('gym-a', 'gym-b'):
        raise BackendError('INVALID_INPUT', 400)
    if 'equipmentStatus' in result:
        statuses = result['equipmentStatus']
        if not isinstance(statuses, dict) or not statuses:
            raise BackendError('INVALID_INPUT', 400)
        for equipment_id, status in statuses.items():
            validate_id(equipment_id)
            if not lookup_equipment(equipment_id) or status not in ('available', 'temporarily_occupied', 'unavailable'):
                raise BackendError('INVALID_INPUT', 400)
    return result


def update_conditions(database, session_id, value):
    request = validate_object(value, {'kind', 'requestId', 'resetEpoch', 'runId', 'authorizationId', 'expectedConditionsVersion', 'changes'})
    if request['kind'] != 'update_conditions':
        raise BackendError('INVALID_INPUT', 400)
    for key in ('requestId', 'runId', 'authorizationId'):
        validate_id(request[key])
    for key in ('resetEpoch', 'expectedConditionsVersion'):
        request[key] = validate_version(request[key])
    request['changes'] = validate_conditions_changes(request['changes'])

    def transition(snapshot):
        grant = database.assert_authorization(session_id, request, {'scope': 'conditions_update', 'changes': request['changes']})
        current, changes = snapshot['conditions'], request['changes']
        if current['version'] != request['expectedConditionsVersion']:
            raise BackendError('VERSION_CONFLICT', 409)
        changed = any(changes[key] != current[key] for key in ('availableMinutes', 'dinnerBudget', 'gymId') if key in changes)
        changed = changed or any(status != current.get('equipmentStatus', {}).get(key, 'available') for key, status in changes.get('equipmentStatus', {}).items())
        database.consume_authorization(session_id, request['authorizationId'], request['requestId'])
        if not changed:
            return {'httpStatus': 200, 'result': {'requestId': request['requestId'], 'status': 'succeeded', 'snapshot': snapshot}}
        for key in ('availableMinutes', 'dinnerBudget', 'gymId'):
            if key in changes:
                current[key] = changes[key]
        if 'equipmentStatus' in changes:
            current['equipmentStatus'] = {**current.get('equipmentStatus', {}), **changes['equipmentStatus']}
        current['version'] += 1
        current['lastChange'] = {'sourceMessageId': grant['sourceMessageId'], 'requestId': request['requestId'], 'version': current['version']}
        snapshot['revision'] += 1
        invalidate_workout_advice(snapshot)
        return {'httpStatus': 200, 'result': {'requestId': request['requestId'], 'status': 'succeeded', 'operationId': str(uuid4())}, 'snapshot': snapshot}

    return database.mutate(session_id, request, transition)
