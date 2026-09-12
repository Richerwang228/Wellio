"""Bounded whole-message grammar over original user text and persisted UI targets."""
import math
import re

from .conditions import validate_conditions_changes
from .errors import BackendError
from .load_confirmation import has_current_user_source, is_read_only_user_text, load_confirmations
from .meals import validate_baseline


def _need(code):
    return {'kind': 'needs_input', 'errorCode': code}


def _match(pattern, text):
    return re.fullmatch(pattern, text, re.I)


def _proposal_minutes(value):
    if re.fullmatch(r'[0-9]{1,3}', value):
        minutes = int(value)
    else:
        digits = dict(zip('零一二三四五六七八九', range(10)))
        digits['两'] = 2
        hundreds = 0
        if value.startswith('一百'):
            hundreds, value = 100, value[2:]
            if value.startswith('零'):
                value = value[1:]
        if not value:
            minutes = hundreds
        elif value in digits:
            minutes = hundreds + digits[value]
        elif match := re.fullmatch(r'([一二三四五六七八九]?)十([一二三四五六七八九]?)', value):
            minutes = hundreds + digits.get(match[1], 1) * 10 + digits.get(match[2], 0)
        else:
            return None
    return minutes if 1 <= minutes <= 180 else None


def _workout_proposal(snapshot, source, text):
    """An explicit short-plan request constrains a candidate, not saved facts."""
    amount = r'([0-9零一二两三四五六七八九十百]+)'
    match = (_match(r'(?:我(?:今天|现在)?|今天|现在)?(?:只)?(?:有|剩|剩下|剩余)\s*' + amount +
                    r'\s*分钟(?:时间)?[，,;；]\s*(?:请帮我|帮我|请)?(?:生成|制定|做)(?:一个|一份)?(?:精简|简短|短时|紧凑|简化)?(?:的)?训练(?:方案|计划)', text)
             or _match(r'(?:请帮我|帮我|请)?(?:生成|制定|做)(?:一个|一份)?\s*' + amount +
                       r'\s*分钟(?:的)?(?:精简|简短|短时|紧凑|简化)?(?:的)?训练(?:方案|计划)', text)
             or _match(r'I (?:only have|have only) ([0-9]+) minutes?(?: today)?[,;]\s*(?:please )?(?:create|generate|make) (?:a )?(?:short |compact |condensed )?(?:workout|training) (?:plan|proposal)', text))
    if not match:
        return None
    minutes = _proposal_minutes(match[1])
    if minutes is None:
        return _need('INVALID_AVAILABLE_MINUTES')
    workout = snapshot.get('workout')
    if not workout or workout['status'] == 'completed' or (source.get('targetWorkoutId') and source['targetWorkoutId'] != workout['id']):
        return _need('WORKOUT_TARGET_REQUIRED')
    if any(source.get(key) for key in ('targetMealId', 'targetMealItemId', 'targetOperationId', 'targetExerciseId')):
        return _need('WORKOUT_TARGET_REQUIRED')
    session = next((row for row in snapshot['plan']['sessions'] if row['id'] == workout['trainingSessionId']), None)
    if not session or session['date'] != snapshot['dayKey']:
        return _need('WORKOUT_TARGET_REQUIRED')
    return {'kind': 'workout_proposal', 'constraint': {
        'scope': 'workout', 'maxMinutes': minutes, 'workoutId': workout['id'],
        'trainingSessionId': workout['trainingSessionId'], 'split': session['split'],
        'gymId': snapshot['conditions']['gymId'], 'preserveTrainingType': True,
        'requiresApply': True, 'conditionsUnchanged': True}}


def _resolve_item(snapshot, source, label):
    name = label.strip().lower()
    pronoun = name in ('this item', 'this food', 'this', 'it', '这份食物', '这个', '这份', '它')
    matches = [(meal, item) for meal in snapshot['meals'] if not source.get('targetMealId') or meal['id'] == source['targetMealId']
               for item in meal['items'] if (not source.get('targetMealItemId') or item['id'] == source['targetMealItemId'])
               and (pronoun or any(alias.strip().lower() == name for alias in item['name'].values()))]
    return matches[0] if len(matches) == 1 else None


def _recorded_meal_portion(snapshot, source, text):
    """Resolve an explicit portion correction from today's recorded meal facts.

    This runs after the original-message read-only/source guards. A meal period
    is sufficient only when it identifies one item; model-selected IDs never
    resolve ambiguity or grant permission to edit every item in a meal.
    """
    match = _match(
        r'(?:请帮我|麻烦帮我|帮我|麻烦|请)?(?:把|将)?(?:我)?(?:今天的?|今日的?)?'
        r'(?:(?:已经|已)?记录的?|已录入的|已保存的)?'
        r'(早餐|早饭|午餐|午饭|中餐|晚餐|晚饭|加餐)(?:的)?(?:份量|分量)?'
        r'(?:修改成|修改为|改成|改为|改到|调整为|调整成|设为|设置为)'
        r'(一半|半份|四分之一|四分之三|全部|[0-9]+(?:\.[0-9]+)?%)', text)
    if not match:
        return None
    if any(source.get(key) for key in ('targetWorkoutId', 'targetExerciseId', 'targetOperationId')):
        return _need('MEAL_TARGET_REQUIRED')
    period = {'早餐': 'breakfast', '早饭': 'breakfast', '午餐': 'lunch', '午饭': 'lunch',
              '中餐': 'lunch', '晚餐': 'dinner', '晚饭': 'dinner', '加餐': 'snack'}[match[1]]
    matches = [(meal, item) for meal in snapshot['meals']
               if meal['period'] == period and (not source.get('targetMealId') or meal['id'] == source['targetMealId'])
               for item in meal['items']
               if not source.get('targetMealItemId') or item['id'] == source['targetMealItemId']]
    if len(matches) != 1:
        return _need('MEAL_TARGET_REQUIRED')
    amount = match[2]
    fraction = {'一半': .5, '半份': .5, '四分之一': .25, '四分之三': .75, '全部': 1}.get(amount)
    if fraction is None:
        fraction = float(amount[:-1]) / 100
    if not math.isfinite(fraction) or not 0 <= fraction <= 1:
        return _need('INVALID_MEAL_CHANGES')
    meal, item = matches[0]
    return {'kind': 'meal', 'constraint': {'scope': 'meal_update', 'mealId': meal['id'],
            'mealItemId': item['id'], 'changes': {'consumedFraction': fraction}}}


def _resolve_exercise(snapshot, source, label, completed):
    name = label.strip().lower()
    pronoun = name in ('this exercise', 'this movement', 'the exercise', '这个动作', '该动作', '整个动作')
    matches = []
    for exercise in (snapshot.get('workout') or {}).get('exercises', []):
        if source.get('targetExerciseId') and source['targetExerciseId'] != exercise['id']:
            continue
        matches_label = (bool(source.get('targetExerciseId')) or exercise['completed'] == completed) if pronoun else any(alias.strip().lower() == name for alias in exercise['name'].values())
        if matches_label:
            matches.append(exercise['id'])
    return matches[0] if len(matches) == 1 else None


def _progress(snapshot, source, text):
    start = _match(r'(?:(?:Please )?start (?:the |this |my )?workout|开始训练|开始今天的训练|我现在开始训练)', text)
    finish = _match(r'(?:Finish|End) (?:the |this |my )?workout( with unfinished exercises| even if incomplete)?(?:[,，:]? (?:after |in |actual time )?([0-9]+) minutes?)?', text) or _match(r'(未完成也)?(?:结束训练|训练结束了)(?:[，,:]?\s*(?:实际(?:用时|用了)?|用时|用了)?\s*([0-9]+)\s*分钟)?', text)
    complete = _match(r'(?:I (?:have )?(?:finished|completed)|Complete|Mark complete) (.+)', text) or _match(r'(.+?)(?:全部)?(?:做完了|完成了)', text) or _match(r'完成(.+)', text)
    undo = _match(r'Undo completion of (.+)', text) or _match(r'撤回(.+?)(?:的)?完成(?:状态)?', text)
    if not any((start, finish, complete, undo)):
        return None
    if any(source.get(key) for key in ('targetMealId', 'targetMealItemId', 'targetOperationId')):
        return _need('WORKOUT_TARGET_REQUIRED')
    workout = snapshot.get('workout')
    if not workout or (source.get('targetWorkoutId') and source['targetWorkoutId'] != workout['id']):
        return _need('WORKOUT_TARGET_REQUIRED')
    if start:
        return _need('WORKOUT_TARGET_REQUIRED') if source.get('targetExerciseId') else {'kind': 'progress', 'action': {'kind': 'start_workout', 'workoutId': workout['id']}}
    if finish:
        if source.get('targetExerciseId'):
            return _need('WORKOUT_TARGET_REQUIRED')
        if not finish[2]:
            return _need('ACTUAL_MINUTES_REQUIRED')
        minutes = int(finish[2])
        if not 1 <= minutes <= 1440:
            return _need('INVALID_ACTUAL_MINUTES')
        confirmed = bool(finish[1])
        if any(not item['completed'] for item in workout['exercises']) and not confirmed:
            return _need('INCOMPLETE_CONFIRMATION_REQUIRED')
        return {'kind': 'progress', 'action': {'kind': 'finish_workout', 'workoutId': workout['id'], 'actualMinutes': minutes, 'confirmIncomplete': confirmed}}
    exercise_id = _resolve_exercise(snapshot, source, (undo or complete)[1], bool(undo))
    return {'kind': 'progress', 'action': {'kind': 'undo_exercise' if undo else 'complete_exercise', 'workoutId': workout['id'], 'exerciseId': exercise_id}} if exercise_id else _need('EXERCISE_TARGET_REQUIRED')


def _conditions(snapshot, text):
    from .workouts import lookup_equipment

    changes = {}
    invalid = False
    def assign(key, value):
        nonlocal invalid
        invalid |= key in changes
        changes[key] = value
    for raw in re.split(r'[;；]', text):
        clause = re.sub(r'[.。]$', '', raw.strip()).strip()
        if match := (_match(r'Set available time to ([0-9]+) minutes?', clause)
                     or _match(r'I (?:only\s+have|have\s*(?:only)?)\s*([0-9]+)\s*minutes?(?: (?:left|now|today))?', clause)
                     or _match(r'(?:今天|我今天|我现在|现在)?(?:只)?(?:剩|剩下|剩余)(?:时间)?(?:改为|改成)?\s*([0-9]+)\s*分钟', clause)
                     or _match(r'(?:我(?:今天|现在)?|今天|现在)?只有\s*([0-9]+)\s*分钟', clause)):
            assign('availableMinutes', int(match[1]))
        elif match := (_match(r'(?:Set|Change) (?:my )?dinner budget to (?:HK\$)?([0-9]+(?:\.[0-9]{1,2})?)', clause)
                       or _match(r'(?:今天的?|我的?)?(?:晚餐)?预算改(?:为|成)\s*([0-9]+(?:\.[0-9]{1,2})?)(?:港币|港元|元)?', clause)):
            assign('dinnerBudget', float(match[1]))
        elif match := (_match(r"(?:Set gym to|Switch (?:my gym )?to|I'm (?:now )?at) Gym\s*([AB])", clause)
                       or _match(r'(?:场地改为|场地改成|换到|换成|换|我现在在)\s*Gym\s*([AB])', clause)):
            assign('gymId', 'gym-' + match[1].lower())
        else:
            equipment_id = status = None
            if match := _match(r'Set (gym-[ab]-[a-z-]+) to (available|temporarily_occupied|unavailable)', clause):
                equipment_id, status = match[1].lower(), match[2].lower()
            elif match := (_match(r"(?:The )?(?:(?:current gym(?:'s)?|my gym(?:'s)?) )?(cable(?: machine)?|dumbbells|bench|pull-up bar) (?:is|are) (occupied|busy|available|unavailable|broken)", clause)
                           or _match(r'(?:当前场地的?|这里的?)?(cable|拉力器|绳索器械|哑铃|训练凳|单杠)(?:现在)?(?:被)?(占用|占了|占|空闲|可用|不可用|坏了)', clause)):
                names = {'cable': 'cable', 'cable machine': 'cable', '拉力器': 'cable', '绳索器械': 'cable', 'dumbbells': 'dumbbells', '哑铃': 'dumbbells', 'bench': 'bench', '训练凳': 'bench', 'pull-up bar': 'pullup-bar', '单杠': 'pullup-bar'}
                equipment_id = snapshot['conditions']['gymId'] + '-' + names[match[1].lower()]
                word = match[2].lower()
                status = 'temporarily_occupied' if word in ('occupied', 'busy', '占用', '占了', '占') else 'available' if word in ('available', '空闲', '可用') else 'unavailable'
            if not equipment_id or not status:
                recognizable = re.match(r'(?:set |change .*budget|switch |I (?:only\s+have|have\s*only)|(?:我(?:今天|现在)?|今天|现在)?只有|剩余|今天只剩|晚餐预算|预算改|当前场地)', text, re.I)
                return _need('CONDITIONS_CONFIRMATION_REQUIRED') if changes or recognizable else None
            if not lookup_equipment(equipment_id) or equipment_id in changes.get('equipmentStatus', {}):
                invalid = True
            changes.setdefault('equipmentStatus', {})[equipment_id] = status
    if changes.get('gymId') and changes.get('equipmentStatus') and re.search(r'occupied|busy|被占|占用|空闲|坏了', text, re.I):
        invalid = True
    try:
        validated = validate_conditions_changes(changes)
    except BackendError:
        return _need('CONDITIONS_CONFIRMATION_REQUIRED')
    return _need('CONDITIONS_CONFIRMATION_REQUIRED') if invalid else {'kind': 'conditions', 'changes': validated}


def derive_user_intent(snapshot, source):
    if not isinstance(source, dict) or not isinstance(source.get('content'), str):
        return {'kind': 'read_only'}
    text = re.sub(r'[.。!！]$', '', source['content'].strip()).strip()
    if source.get('purpose') == 'menu' or is_read_only_user_text(text) or re.search(r'\b(?:how many|how much|what is|what are|is this|is that|did I)\b|多少|够不够|能不能|是不是|有没有', text, re.I):
        return {'kind': 'read_only'}
    if not has_current_user_source(snapshot, source):
        return _need('SOURCE_CONTEXT_MISMATCH')
    if meal_portion := _recorded_meal_portion(snapshot, source, text):
        return meal_portion
    if proposal := _workout_proposal(snapshot, source, text):
        return proposal
    if progress := _progress(snapshot, source, text):
        return progress
    if _match(r'(?:Undo (?:this|that|the last) (?:meal )?change|Undo|撤销|撤销(?:这次|刚才的|上次的)(?:餐食)?修改)', text):
        if any(source.get(key) for key in ('targetWorkoutId', 'targetExerciseId', 'targetMealItemId')):
            return _need('OPERATION_TARGET_REQUIRED')
        if source.get('targetOperationId'):
            return {'kind': 'undo', 'operationId': source['targetOperationId']}
        if source.get('targetMealId'):
            meal = next((meal for meal in snapshot['meals'] if meal['id'] == source['targetMealId']), None)
            if meal and meal.get('operationId'):
                return {'kind': 'undo', 'operationId': meal['operationId']}
        return _need('OPERATION_TARGET_REQUIRED')
    load = load_confirmations(snapshot, source)
    if load['kind'] == 'confirmed':
        return {'kind': 'load_confirmation', 'confirmations': load['confirmations']}
    if load['kind'] == 'needs_input':
        return load
    other_target = any(source.get(key) for key in ('targetWorkoutId', 'targetExerciseId', 'targetOperationId'))
    if match := _match(r'Correct (.+): ([0-9]+(?:\.[0-9]+)?) (g|ml|piece|serving); ([0-9]+(?:\.[0-9]+)?) kcal; protein ([0-9]+(?:\.[0-9]+)?) g; carbs ([0-9]+(?:\.[0-9]+)?) g; fat ([0-9]+(?:\.[0-9]+)?) g', text):
        target = _resolve_item(snapshot, source, match[1])
        if not target or other_target:
            return _need('MEAL_TARGET_REQUIRED')
        quantity, unit = float(match[2]), match[3].lower()
        portion = f'{quantity:g} {unit}'
        try:
            baseline = validate_baseline({'portion': {'en': portion, 'zh-CN': portion}, 'originalPortion': {'quantity': quantity, 'unit': unit},
                                          'base': dict(zip(('kcal', 'protein', 'carbs', 'fat'), (float(match[i]) for i in range(4, 8)))), 'nutrientUnits': {'energy': 'kcal', 'mass': 'g'}})
        except BackendError:
            return _need('INVALID_MEAL_CHANGES')
        return {'kind': 'meal', 'constraint': {'scope': 'meal_update', 'mealId': target[0]['id'], 'mealItemId': target[1]['id'], 'changes': {'baseline': baseline}}}
    fraction, label = None, None
    if match := (_match(r'I (?:(?:only|just) )?ate\s+(?:only\s*)?(half|a quarter|three quarters|all|[0-9]+(?:\.[0-9]+)?%)(?:\s+(?:of )?(?:the )?(.+))?', text)
                 or _match(r'only\s*(half|a quarter|three quarters|all|[0-9]+(?:\.[0-9]+)?%)(?:\s+(?:of )?(?:the )?(.+))?', text)):
        amount = match[1].lower()
        fraction = {'half': .5, 'a quarter': .25, 'three quarters': .75, 'all': 1}.get(amount)
        if fraction is None:
            fraction = float(amount[:-1]) / 100
        label = match[2] or 'this item'
    elif match := _match(r'(?:我)?(?:刚刚|刚才|刚|已经)?(?:只)?吃(?:了)?(一半|四分之一|四分之三|全部)(.*)', text):
        fraction = {'一半': .5, '四分之一': .25, '四分之三': .75, '全部': 1}[match[1]]
        label = match[2].strip() or '这份食物'
    elif match := _match(r'(.+?)(?:我)?(?:刚刚|刚才|刚|已经)?(?:只)?吃(?:了)?(一半|四分之一|四分之三|全部)', text):
        fraction = {'一半': .5, '四分之一': .25, '四分之三': .75, '全部': 1}[match[2]]
        label = re.sub(r'^我(?:的)?', '', match[1]).strip()
    if fraction is not None and label:
        target = _resolve_item(snapshot, source, label)
        if not target or other_target:
            return _need('MEAL_TARGET_REQUIRED')
        if not math.isfinite(fraction) or not 0 <= fraction <= 1:
            return _need('INVALID_MEAL_CHANGES')
        return {'kind': 'meal', 'constraint': {'scope': 'meal_update', 'mealId': target[0]['id'], 'mealItemId': target[1]['id'], 'changes': {'consumedFraction': fraction}}}
    if _match(r'(?:Delete this meal|删除这餐)', text):
        meal = next((meal for meal in snapshot['meals'] if meal['id'] == source.get('targetMealId')), None)
        return {'kind': 'meal', 'constraint': {'scope': 'meal_delete', 'mealId': meal['id']}} if meal and not source.get('targetMealItemId') and not other_target else _need('MEAL_TARGET_REQUIRED')
    if match := (_match(r'Delete (?:the )?(.+)', text) or _match(r'删除(.+)', text)):
        target = _resolve_item(snapshot, source, match[1])
        return {'kind': 'meal', 'constraint': {'scope': 'meal_delete', 'mealId': target[0]['id'], 'mealItemId': target[1]['id']}} if target and not other_target else _need('MEAL_TARGET_REQUIRED')
    if _match(r'(?:(?:Please )?(?:Log|Record) this meal|帮我记录这餐|记录这餐)', text) or _match(r"(?:I (?:just )?(?:ate|have eaten)|I've (?:just )?eaten) [^;；:：]+", text) or _match(r'(?:我)?(?:刚刚|刚才|刚|已经)?(?:吃|喝)(?:了|过了?)[^;；:：]+', text):
        return _need('MEAL_TARGET_REQUIRED') if source.get('targetMealId') or source.get('targetMealItemId') or other_target else {'kind': 'meal', 'constraint': {'scope': 'meal_add'}}
    if conditions := _conditions(snapshot, text):
        return _need('CONDITIONS_TARGET_REQUIRED') if source.get('targetMealId') or source.get('targetMealItemId') or other_target else conditions
    if re.match(r'(?:(?:Please )?(?:Log|Record|Correct|Delete)\b|删除|记录|帮我记录|I (?:(?:just|only) )?ate\b|(?:我)?(?:刚刚|刚才|刚|已经)?(?:只)?(?:吃|喝)(?:了|过))', text, re.I):
        return _need('MEAL_TARGET_REQUIRED')
    if re.match(r'(?:Undo\b|撤销)', text, re.I):
        return _need('OPERATION_TARGET_REQUIRED')
    if re.match(r'(?:(?:Please )?(?:Start|Finish|End|Complete)\b|开始|结束|完成)', text, re.I):
        return _need('WORKOUT_TARGET_REQUIRED')
    return {'kind': 'read_only'}
