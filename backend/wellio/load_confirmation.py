"""Pure verification of private, original-user load evidence for one training session."""
from copy import deepcopy
import re


def is_read_only_user_text(text):
    without_contractions = re.sub(r"\b(?:I'm|I've|I'll|that's|today's)\b", '', text, flags=re.I)
    return bool(re.search(r'''[?？"'“”‘’`\n\r]''', without_contractions)
                or re.search(r"\b(if|would|could|should|not|never|nothing|no|maybe|might|recommend|hypothetically|don't|didn't|can't|won't)\b", text, re.I)
                or re.search(r'如果|假如|假设|不要|没有|没吃|不吃|别记录|可能|引用|推荐|建议|是否|吗[。.!！]?$', text))


def has_current_user_source(snapshot, source):
    return bool(source.get('id') and source.get('sessionId') == snapshot.get('sessionId')
                and source.get('resetEpoch') == snapshot.get('resetEpoch') and source.get('conversationId') == snapshot.get('conversationId'))


DEFINITIONS = {
    'seated-cable-row': ('cable', ('seated cable row', '坐姿绳索划船')),
    'lat-pulldown': ('cable', ('lat pulldown', '高位下拉')),
    'dumbbell-curl': ('dumbbells', ('dumbbell curl', '哑铃弯举')),
    'one-arm-dumbbell-row': ('dumbbells', ('one-arm dumbbell row', 'single-arm dumbbell row', '单臂哑铃划船')),
    'goblet-squat': ('dumbbells', ('goblet squat', '高脚杯深蹲')),
    'dumbbell-romanian-deadlift': ('dumbbells', ('dumbbell romanian deadlift', '哑铃罗马尼亚硬拉')),
    'reverse-lunge': ('dumbbells', ('reverse lunge', '反向弓步', '后撤弓步')),
    'dumbbell-bench-press': ('dumbbells', ('dumbbell bench press', '哑铃卧推')),
    'dumbbell-shoulder-press': ('dumbbells', ('dumbbell shoulder press', '哑铃肩推', '哑铃推举')),
    'triceps-pushdown': ('cable', ('triceps pushdown', '绳索下压', '绳索肱三头肌下压')),
    'lateral-raise': ('dumbbells', ('lateral raise', '侧平举', '哑铃侧平举')),
}


def _resolve_catalog(snapshot, source, label):
    target_id = source.get('targetExerciseId')
    target = next((item for item in (snapshot.get('workout') or {}).get('exercises', []) if item['id'] == target_id), None)
    if target_id and not target:
        return None
    name = label.strip().lower()
    if not name or name in ('this exercise', 'this movement', '这个动作', '该动作'):
        # Later progress must not resolve a previously ambiguous bare weight.
        return target['catalogId'] if target else None
    matches = [key for key, (_, names) in DEFINITIONS.items() if (not target or key == target['catalogId']) and name in names]
    return matches[0] if len(matches) == 1 else None


def load_confirmations(snapshot, source):
    from .workouts import lookup_equipment

    if not isinstance(source, dict) or not isinstance(source.get('content'), str):
        return {'kind': 'not_confirmation'}
    text = re.sub(r'[.。!！]$', '', source['content'].strip()).strip()
    if source.get('purpose') == 'menu' or is_read_only_user_text(text):
        return {'kind': 'not_confirmation'}
    if not re.search(r'(?:kg|kilograms?|lbs?|pounds?)\b|公斤|千克|磅', text, re.I):
        return {'kind': 'not_confirmation'}
    def need(code):
        return {'kind': 'needs_input', 'errorCode': code}
    if not has_current_user_source(snapshot, source):
        return need('SOURCE_CONTEXT_MISMATCH')
    if any(source.get(key) for key in ('targetMealId', 'targetMealItemId', 'targetOperationId')):
        return need('LOAD_TARGET_REQUIRED')
    workout = snapshot.get('workout')
    if not workout or workout['status'] == 'completed' or (source.get('targetWorkoutId') and source['targetWorkoutId'] != workout['id']):
        return need('WORKOUT_TARGET_REQUIRED')
    context = source.get('workoutContext')
    if not context:
        return need('LOAD_CONTEXT_REQUIRED')
    if not isinstance(context, dict) or context.get('workoutId') != workout['id'] or context.get('trainingSessionId') != workout['trainingSessionId'] or context.get('gymId') != snapshot['conditions']['gymId']:
        return need('LOAD_CONTEXT_MISMATCH')
    if re.search(r'(?:lbs?|pounds?)\b|磅', text, re.I):
        return need('LOAD_UNIT_REQUIRED')
    confirmations = []
    for clause in re.split(r'[;；]', text):
        clause = clause.strip()
        if match := re.fullmatch(r'(?:(?:I confirm|Use|I use) )?(?:(.+?) (?:at |is |use |uses )?)?([0-9]+(?:\.[0-9]+)?)\s*(?:kg|kilograms?) (per hand|on the machine stack|on the stack|machine stack)', clause, re.I):
            label, kg = match[1] or '', float(match[2])
            basis = 'per_hand' if match[3].lower() == 'per hand' else 'machine_stack'
        elif match := re.fullmatch(r'(?:Use|I confirm|I use) ([0-9]+(?:\.[0-9]+)?)\s*(?:kg|kilograms?) (per hand|on the machine stack|on the stack|machine stack) for (.+)', clause, re.I):
            label, kg = match[3], float(match[1])
            basis = 'per_hand' if match[2].lower() == 'per hand' else 'machine_stack'
        elif match := re.fullmatch(r'(?:我确认|确认)?(.*?)(?:用|使用)?(?:每只|每手)([0-9]+(?:\.[0-9]+)?)\s*(?:kg|公斤|千克)', clause, re.I):
            label, kg, basis = match[1], float(match[2]), 'per_hand'
        elif match := re.fullmatch(r'(?:我确认|确认)?(.*?)(?:用|使用)?(?:配重片|配重栈|器械配重)([0-9]+(?:\.[0-9]+)?)\s*(?:kg|公斤|千克)', clause, re.I):
            label, kg, basis = match[1], float(match[2]), 'machine_stack'
        else:
            return need('LOAD_CONFIRMATION_REQUIRED')
        catalog_id = _resolve_catalog(snapshot, source, label)
        if catalog_id not in DEFINITIONS:
            return need('EXERCISE_TARGET_REQUIRED')
        equipment_id = snapshot['conditions']['gymId'] + '-' + DEFINITIONS[catalog_id][0]
        equipment = lookup_equipment(equipment_id)
        if not equipment or equipment['kind'] not in ('cable', 'dumbbells') or equipment['load']['basis'] != basis:
            return need('LOAD_BASIS_REQUIRED')
        if kg not in equipment['load']['allowedKg']:
            return need('INVALID_LOAD')
        if any(item['catalogId'] == catalog_id and item['equipmentId'] == equipment_id for item in confirmations):
            return need('LOAD_CONFIRMATION_AMBIGUOUS')
        confirmations.append({'sourceMessageId': source['id'], 'sessionId': source['sessionId'], 'resetEpoch': source['resetEpoch'],
                              'trainingSessionId': workout['trainingSessionId'], 'catalogId': catalog_id, 'equipmentId': equipment_id,
                              'kg': kg, 'unit': 'kg', 'basis': basis, 'source': deepcopy(source)})
    return {'kind': 'confirmed', 'confirmations': confirmations}


derive_load_confirmations = load_confirmations


def validate_user_load(snapshot, exercise, evidence):
    """Evidence must be obtained from private user_inputs, never accepted from model JSON."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get('source'), dict):
        return False
    source, load = evidence['source'], exercise.get('suggestedLoad', {})
    if not isinstance(source.get('content'), str) or type(evidence.get('kg')) not in (int, float) or type(load.get('value')) not in (int, float):
        return False
    required = {'sessionId': snapshot['sessionId'], 'resetEpoch': snapshot['resetEpoch'],
                'trainingSessionId': (snapshot.get('workout') or {}).get('trainingSessionId'), 'catalogId': exercise['catalogId'],
                'equipmentId': exercise['equipmentId'], 'unit': 'kg', 'kg': load.get('value'), 'basis': load.get('basis')}
    if any(evidence.get(key) != value for key, value in required.items()) or load.get('unit') != 'kg' or load.get('source') != 'user':
        return False
    if not evidence.get('sourceMessageId') or load.get('sourceMessageId') != evidence['sourceMessageId'] or source.get('id') != evidence['sourceMessageId']:
        return False
    parsed = load_confirmations(snapshot, source)
    keys = ('sourceMessageId', 'trainingSessionId', 'catalogId', 'equipmentId', 'kg', 'basis')
    return parsed['kind'] == 'confirmed' and any(all(item[key] == evidence[key] for key in keys) for item in parsed['confirmations'])
