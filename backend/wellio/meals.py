"""Transactional meal edits, absolute fractions and monotonic-version undo chains."""
from copy import deepcopy
import re
from uuid import uuid4

from .conditions import invalidate_workout_advice, validate_id, validate_localized, validate_number, validate_object, validate_version
from .errors import BackendError


def validate_baseline(value):
    result = validate_object(value, {'portion', 'originalPortion', 'base', 'nutrientUnits'})
    result['portion'] = validate_localized(result['portion'])
    original = validate_object(result['originalPortion'], {'quantity', 'unit'})
    original['quantity'] = validate_number(original['quantity'], 0, 100_000, positive=True)
    if original['unit'] not in ('g', 'ml', 'piece', 'serving'):
        raise BackendError('INVALID_INPUT', 400)
    result['originalPortion'] = original
    nutrients = validate_object(result['base'], {'kcal', 'protein', 'carbs', 'fat'})
    for key in nutrients:
        nutrients[key] = validate_number(nutrients[key], 0, 10_000 if key == 'kcal' else 2_000)
    result['base'] = nutrients
    units = validate_object(result['nutrientUnits'], {'energy', 'mass'})
    if units != {'energy': 'kcal', 'mass': 'g'}:
        raise BackendError('INVALID_INPUT', 400)
    return result


def validate_meal_changes(value):
    if isinstance(value, dict) and set(value) == {'consumedFraction'}:
        return {'consumedFraction': validate_number(value['consumedFraction'], 0, 1)}
    request = validate_object(value, {'baseline'})
    return {'baseline': validate_baseline(request['baseline'])}


def validate_meal_input(value):
    meal = validate_object(value, {'period', 'time', 'items'})
    if meal['period'] not in ('breakfast', 'lunch', 'dinner', 'snack') or not isinstance(meal['time'], str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', meal['time']):
        raise BackendError('INVALID_INPUT', 400)
    if not isinstance(meal['items'], list) or not 1 <= len(meal['items']) <= 30:
        raise BackendError('INVALID_INPUT', 400)
    items = []
    for value in meal['items']:
        item = validate_object(value, {'name', 'portion', 'originalPortion', 'base', 'nutrientUnits', 'consumedFraction', 'estimated'})
        baseline = validate_baseline({key: item[key] for key in ('portion', 'originalPortion', 'base', 'nutrientUnits')})
        if type(item['estimated']) is not bool:
            raise BackendError('INVALID_INPUT', 400)
        items.append({**baseline, 'name': validate_localized(item['name']), 'consumedFraction': validate_number(item['consumedFraction'], 0, 1), 'estimated': item['estimated']})
    return {**meal, 'items': items}


def _validate_mutation(value):
    if not isinstance(value, dict):
        raise BackendError('INVALID_INPUT', 400)
    action = value.get('action')
    fields = {'kind', 'action', 'requestId', 'resetEpoch', 'runId', 'authorizationId', 'expectedMealRevision'}
    if action == 'add':
        request = validate_object(value, fields | {'meal'})
        request['meal'] = validate_meal_input(request['meal'])
    elif action == 'update':
        request = validate_object(value, fields | {'mealId', 'mealItemId', 'expectedMealVersion', 'changes'})
        request['changes'] = validate_meal_changes(request['changes'])
    elif action == 'delete':
        request = validate_object(value, fields | {'mealId', 'expectedMealVersion'}, {'mealItemId'})
    else:
        raise BackendError('INVALID_INPUT', 400)
    if request['kind'] != 'mutate_meal_log':
        raise BackendError('INVALID_INPUT', 400)
    for key in ('requestId', 'runId', 'authorizationId', 'mealId', 'mealItemId'):
        if key in request:
            validate_id(request[key])
    for key in ('resetEpoch', 'expectedMealRevision', 'expectedMealVersion'):
        if key in request:
            request[key] = validate_version(request[key])
    return request


def _business(meal):
    return {key: value for key, value in meal.items() if key not in ('version', 'operationId')} if meal else None


def _changed(snapshot):
    snapshot['mealRevision'] += 1
    snapshot['revision'] += 1
    invalidate_workout_advice(snapshot)


def _nutrition(snapshot, meal_id):
    from .read_services import calculate_daily_totals, calculate_meal_totals
    meal = next((meal for meal in snapshot['meals'] if meal['id'] == meal_id), None)
    return {'meal': calculate_meal_totals(meal) if meal else None, 'day': calculate_daily_totals(snapshot)}


def mutate_meal_log(database, session_id, value):
    request = _validate_mutation(value)

    def transition(snapshot):
        action = request['action']
        constraint = {'scope': 'meal_' + action}
        if action != 'add':
            constraint['mealId'] = request['mealId']
            if 'mealItemId' in request:
                constraint['mealItemId'] = request['mealItemId']
            if action == 'update':
                constraint['changes'] = request['changes']
        grant = database.assert_authorization(session_id, request, constraint)
        if snapshot['mealRevision'] != request['expectedMealRevision']:
            raise BackendError('VERSION_CONFLICT', 409)
        index = len(snapshot['meals']) if action == 'add' else next((i for i, meal in enumerate(snapshot['meals']) if meal['id'] == request['mealId']), -1)
        if index < 0:
            raise BackendError('NOT_FOUND', 404)
        before = None if action == 'add' else deepcopy(snapshot['meals'][index])
        if before and before['version'] != request['expectedMealVersion']:
            raise BackendError('VERSION_CONFLICT', 409)
        meal_id = before['id'] if before else str(uuid4())
        entity = database.get_meal_entity(session_id, snapshot['resetEpoch'], meal_id) or {'mealId': meal_id, 'version': before['version'] if before else 0, 'headOperationId': before.get('operationId') if before else None}
        if before and (before['version'] != entity['version'] or before.get('operationId') != entity['headOperationId']):
            raise BackendError('VERSION_CONFLICT', 409)
        if action == 'add':
            after = {**deepcopy(request['meal']), 'id': meal_id, 'version': 1}
            for item in after['items']:
                item['id'] = str(uuid4())
        elif action == 'update':
            after = deepcopy(before)
            item = next((item for item in after['items'] if item['id'] == request['mealItemId']), None)
            if item is None:
                raise BackendError('NOT_FOUND', 404)
            if 'consumedFraction' in request['changes']:
                item['consumedFraction'] = request['changes']['consumedFraction']
            else:
                item.update(deepcopy(request['changes']['baseline']))
        elif 'mealItemId' in request:
            if not any(item['id'] == request['mealItemId'] for item in before['items']):
                raise BackendError('NOT_FOUND', 404)
            after = deepcopy(before)
            after['items'] = [item for item in after['items'] if item['id'] != request['mealItemId']]
            if not after['items']:
                after = None
        else:
            after = None
        database.consume_authorization(session_id, request['authorizationId'], request['requestId'])
        if _business(before) == _business(after):
            return {'httpStatus': 200, 'result': {'requestId': request['requestId'], 'status': 'succeeded', 'snapshot': snapshot, 'nutrition': _nutrition(snapshot, meal_id)}}
        operation_id = str(uuid4())
        next_entity = {'mealId': meal_id, 'version': entity['version'] + 1, 'headOperationId': operation_id}
        if after:
            after.update(version=next_entity['version'], operationId=operation_id)
        operation = {'id': operation_id, 'sessionId': session_id, 'resetEpoch': snapshot['resetEpoch'], 'mealId': meal_id, 'action': action,
                     'sourceMessageId': grant['sourceMessageId'], 'requestId': request['requestId'], 'before': before, 'after': after,
                     'beforeIndex': index, 'afterVersion': next_entity['version'], 'parentOperationId': entity['headOperationId'], 'status': 'applied'}
        database.store_meal_operation(operation)
        database.save_meal_entity(session_id, snapshot['resetEpoch'], next_entity)
        snapshot['meals'][index:index + (1 if before else 0)] = [after] if after else []
        _changed(snapshot)
        return {'httpStatus': 200, 'result': {'requestId': request['requestId'], 'status': 'succeeded', 'operationId': operation_id, 'nutrition': _nutrition(snapshot, meal_id)}, 'snapshot': snapshot}

    return database.mutate(session_id, request, transition)


def undo_meal(database, session_id, value):
    request = validate_object(value, {'kind', 'requestId', 'resetEpoch', 'source', 'operationId'})
    if request['kind'] != 'undo_meal':
        raise BackendError('INVALID_INPUT', 400)
    validate_id(request['requestId'])
    validate_id(request['operationId'])
    request['resetEpoch'] = validate_version(request['resetEpoch'])
    if request['source'] not in ('today', 'agent', 'workout', 'profile', 'app_open'):
        raise BackendError('INVALID_INPUT', 400)
    if request['source'] not in ('today', 'agent'):
        raise BackendError('MEAL_REQUIRES_USER_ACTION', 403)

    def transition(snapshot):
        operation = database.get_meal_operation(session_id, snapshot['resetEpoch'], request['operationId'])
        if not operation:
            raise BackendError('NOT_FOUND', 404)
        if operation['status'] == 'undone':
            return {'httpStatus': 200, 'result': {'requestId': request['requestId'], 'status': 'succeeded', 'operationId': operation['id'], 'snapshot': snapshot, 'nutrition': _nutrition(snapshot, operation['mealId'])}}
        entity = database.get_meal_entity(session_id, snapshot['resetEpoch'], operation['mealId'])
        index = next((i for i, meal in enumerate(snapshot['meals']) if meal['id'] == operation['mealId']), -1)
        current = snapshot['meals'][index] if index >= 0 else None
        if not entity or entity['headOperationId'] != operation['id'] or (current and (current['version'] != entity['version'] or current.get('operationId') != entity['headOperationId'])) or _business(current) != _business(operation['after']):
            raise BackendError('UNDO_CONFLICT', 409)
        restored = deepcopy(operation['before'])
        next_entity = {'mealId': operation['mealId'], 'version': entity['version'] + 1, 'headOperationId': operation['parentOperationId']}
        if restored:
            restored['version'] = next_entity['version']
            if next_entity['headOperationId']:
                restored['operationId'] = next_entity['headOperationId']
            else:
                restored.pop('operationId', None)
        if index >= 0:
            snapshot['meals'][index:index + 1] = [restored] if restored else []
        elif restored:
            snapshot['meals'].insert(min(operation['beforeIndex'], len(snapshot['meals'])), restored)
        database.save_meal_entity(session_id, snapshot['resetEpoch'], next_entity)
        database.mark_meal_operation_undone(operation, request['requestId'])
        _changed(snapshot)
        return {'httpStatus': 200, 'result': {'requestId': request['requestId'], 'status': 'succeeded', 'operationId': operation['id'], 'nutrition': _nutrition(snapshot, operation['mealId'])}, 'snapshot': snapshot}

    return database.mutate(session_id, request, transition)
