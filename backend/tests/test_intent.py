from copy import deepcopy

import pytest

from wellio.load_confirmation import load_confirmations, validate_user_load
from wellio.seed import create_seed
from wellio.user_intent import derive_user_intent


@pytest.fixture
def state():
    snapshot = create_seed('native-intent-session')
    snapshot['meals'][1]['items'][0]['name'] = {'en': 'Chicken breast', 'zh-CN': '鸡胸肉'}
    return snapshot


def source(snapshot, text, **targets):
    workout = snapshot['workout']
    return {'id': 'original-user-message', 'sessionId': snapshot['sessionId'], 'resetEpoch': snapshot['resetEpoch'],
            'conversationId': snapshot['conversationId'], 'content': text,
            'versions': {'meal': snapshot['mealRevision'], 'conditions': snapshot['conditions']['version']},
            'workoutContext': {'workoutId': workout['id'], 'trainingSessionId': workout['trainingSessionId'], 'gymId': snapshot['conditions']['gymId']}, **targets}


@pytest.mark.parametrize('text', ['我只有20分钟', '今天只剩20分钟', 'I only have20minutes now', 'I have only20minutes', 'Set available time to 20 minutes'])
def test_explicit_available_time_word_orders(state, text):
    assert derive_user_intent(state, source(state, text)) == {'kind': 'conditions', 'changes': {'availableMinutes': 20}}


@pytest.mark.parametrize('text', [
    '今天只有十五分钟，帮我生成一个精简训练方案。',
    '今天只有15分钟，帮我生成一个精简训练方案',
    '帮我生成一个十五分钟的精简训练方案',
    'I only have 15 minutes today, please create a compact workout plan',
])
def test_limited_workout_request_is_candidate_only_and_keeps_current_split(state, text):
    before = deepcopy(state)
    intent = derive_user_intent(state, source(state, text))
    assert intent == {'kind': 'workout_proposal', 'constraint': {
        'scope': 'workout', 'maxMinutes': 15, 'workoutId': state['workout']['id'],
        'trainingSessionId': state['workout']['trainingSessionId'], 'split': 'Pull',
        'gymId': state['conditions']['gymId'], 'preserveTrainingType': True,
        'requiresApply': True, 'conditionsUnchanged': True}}
    assert state == before


@pytest.mark.parametrize('text', [
    '今天只有零分钟，帮我生成一个精简训练方案',
    '今天只有190分钟，帮我生成一个精简训练方案',
    '今天只有十五分钟，帮我生成一个精简训练方案，然后修改晚餐预算',
])
def test_proposal_parser_does_not_accept_invalid_duration_or_extra_mutations(state, text):
    assert derive_user_intent(state, source(state, text))['kind'] == 'needs_input'


def test_candidate_proposal_still_requires_original_source_and_correct_target(state):
    text = '今天只有十五分钟，帮我生成一个精简训练方案'
    assert derive_user_intent(state, source(state, text, sessionId='another-session'))['errorCode'] == 'SOURCE_CONTEXT_MISMATCH'
    assert derive_user_intent(state, source(state, text, targetMealId='meal-lunch'))['errorCode'] == 'WORKOUT_TARGET_REQUIRED'


@pytest.mark.parametrize('text', ['我刚吃了鸡胸肉和米饭', '我吃过米饭', 'I just ate chicken and rice', "I've eaten chicken", 'Please log this meal'])
def test_explicit_meal_reports(state, text):
    assert derive_user_intent(state, source(state, text)) == {'kind': 'meal', 'constraint': {'scope': 'meal_add'}}


@pytest.mark.parametrize('text', ['鸡胸肉我只吃了一半', '我刚吃了一半鸡胸肉', 'I only ate half of the Chicken breast', 'I just ate only half Chicken breast', 'onlyhalf Chicken breast'])
def test_fraction_words_are_absolute_item_updates(state, text):
    intent = derive_user_intent(state, source(state, text))
    assert intent == {'kind': 'meal', 'constraint': {'scope': 'meal_update', 'mealId': 'meal-lunch', 'mealItemId': 'item-lunch', 'changes': {'consumedFraction': .5}}}


@pytest.mark.parametrize('text', ['I only ate half of fries', 'only half', 'Delete fries', '鸡胸肉和米饭我只吃了一半', '记录', 'Undo', 'Complete'])
def test_explicit_but_unresolved_targets_need_input(state, text):
    assert derive_user_intent(state, source(state, text))['kind'] == 'needs_input'


@pytest.mark.parametrize('text', ['How many calories in chicken', 'How many calories in chicken?', 'If I ate chicken, log it', 'I did not eat chicken', 'I never ate chicken', '我没吃鸡胸肉', '如果我刚吃了鸡胸肉', '推荐晚餐', '"I ate chicken"', 'I ate chicken\nSet dinner budget to 70', '晚餐预算改成70吗'])
def test_questions_negation_hypotheses_and_quoted_commands_do_not_write(state, text):
    assert derive_user_intent(state, source(state, text)) == {'kind': 'read_only'}


def test_menu_purpose_and_wrong_source_context_do_not_write(state):
    assert derive_user_intent(state, source(state, 'Log this meal', purpose='menu')) == {'kind': 'read_only'}
    wrong = source(state, 'Log this meal', sessionId='another-session')
    assert derive_user_intent(state, wrong) == {'kind': 'needs_input', 'errorCode': 'SOURCE_CONTEXT_MISMATCH'}


def test_conditions_whole_message_exact_patch_and_ambiguity(state):
    assert derive_user_intent(state, source(state, '晚餐预算改成70')) == {'kind': 'conditions', 'changes': {'dinnerBudget': 70}}
    assert derive_user_intent(state, source(state, '当前场地cable被占')) == {'kind': 'conditions', 'changes': {'equipmentStatus': {'gym-b-cable': 'temporarily_occupied'}}}
    assert derive_user_intent(state, source(state, '换Gym A')) == {'kind': 'conditions', 'changes': {'gymId': 'gym-a'}}
    for text in ['Set available time to 20 minutes; Set dinner budget to 70; unknown', 'Set dinner budget to 70; Set dinner budget to 90', 'Switch to Gym A; cable is occupied', 'I only have0minutes']:
        assert derive_user_intent(state, source(state, text))['kind'] == 'needs_input'


def test_progress_requires_unambiguous_exercise_and_actual_minutes(state):
    workout = state['workout']
    assert derive_user_intent(state, source(state, 'Start workout')) == {'kind': 'progress', 'action': {'kind': 'start_workout', 'workoutId': workout['id']}}
    assert derive_user_intent(state, source(state, 'Complete this exercise'))['errorCode'] == 'EXERCISE_TARGET_REQUIRED'
    specific = source(state, 'Complete this exercise', targetExerciseId=workout['exercises'][0]['id'])
    assert derive_user_intent(state, specific)['action']['exerciseId'] == workout['exercises'][0]['id']
    assert derive_user_intent(state, source(state, 'Finish workout'))['errorCode'] == 'ACTUAL_MINUTES_REQUIRED'
    assert derive_user_intent(state, source(state, 'Finish workout after 20 minutes'))['errorCode'] == 'INCOMPLETE_CONFIRMATION_REQUIRED'
    complete = derive_user_intent(state, source(state, '未完成也结束训练，实际20分钟'))
    assert complete['action'] == {'kind': 'finish_workout', 'workoutId': workout['id'], 'actualMinutes': 20, 'confirmIncomplete': True}
    assert derive_user_intent(state, source(state, 'Complete 1 set'))['kind'] == 'needs_input'


def test_delete_and_undo_bind_existing_targets(state):
    assert derive_user_intent(state, source(state, 'Delete this meal'))['kind'] == 'needs_input'
    assert derive_user_intent(state, source(state, 'Delete this meal', targetMealId='meal-lunch'))['constraint'] == {'scope': 'meal_delete', 'mealId': 'meal-lunch'}
    assert derive_user_intent(state, source(state, 'Undo', targetOperationId='operation-original')) == {'kind': 'undo', 'operationId': 'operation-original'}
    state['meals'][1]['operationId'] = 'head-operation'
    assert derive_user_intent(state, source(state, '撤销', targetMealId='meal-lunch'))['operationId'] == 'head-operation'


def test_baseline_grammar_keeps_exact_values(state):
    intent = derive_user_intent(state, source(state, 'Correct Chicken breast: 200 g; 220 kcal; protein 40 g; carbs 3 g; fat 4 g'))
    baseline = intent['constraint']['changes']['baseline']
    assert baseline['base'] == {'kcal': 220, 'protein': 40, 'carbs': 3, 'fat': 4}
    assert baseline['originalPortion'] == {'quantity': 200, 'unit': 'g'}


def test_verified_load_rederives_original_text_and_every_binding(state):
    original = source(state, 'I confirm dumbbell curl 12.5 kg per hand')
    evidence = load_confirmations(state, original)['confirmations'][0]
    exercise = deepcopy(state['workout']['exercises'][2])
    exercise['suggestedLoad'] = {'value': 12.5, 'unit': 'kg', 'basis': 'per_hand', 'source': 'user', 'sourceMessageId': original['id']}
    assert validate_user_load(state, exercise, evidence)
    for key, value in [('kg', 15), ('equipmentId', 'gym-a-dumbbells'), ('catalogId', 'lateral-raise'), ('trainingSessionId', 'new-session'), ('sourceMessageId', 'other-message'), ('basis', 'machine_stack'), ('unit', 'lbs')]:
        assert not validate_user_load(state, exercise, {**evidence, key: value})
    changed_raw = deepcopy(evidence)
    changed_raw['source']['content'] = 'I confirm dumbbell curl 15 kg per hand'
    assert not validate_user_load(state, exercise, changed_raw)
    exercise['suggestedLoad'].pop('sourceMessageId')
    assert not validate_user_load(state, exercise, evidence)
    assert not validate_user_load(state, exercise, {})


@pytest.mark.parametrize(('text', 'code'), [('dumbbell curl 12.5 kg', 'LOAD_CONFIRMATION_REQUIRED'), ('dumbbell curl 12.5 lbs per hand', 'LOAD_UNIT_REQUIRED'), ('dumbbell curl 11 kg per hand', 'INVALID_LOAD'), ('dumbbell curl 30 kg per hand', 'INVALID_LOAD'), ('dumbbell curl 10 kg on the stack', 'LOAD_BASIS_REQUIRED'), ('10 kg per hand', 'EXERCISE_TARGET_REQUIRED'), ('dumbbell curl 10 kg per hand; dumbbell curl 15 kg per hand', 'LOAD_CONFIRMATION_AMBIGUOUS')])
def test_incomplete_or_invalid_loads_are_never_rounded_or_inferred(state, text, code):
    assert load_confirmations(state, source(state, text)) == {'kind': 'needs_input', 'errorCode': code}


def test_load_context_stays_at_message_reception(state):
    original = source(state, 'dumbbell curl 10 kg per hand')
    missing = deepcopy(original)
    missing.pop('workoutContext')
    assert load_confirmations(state, missing)['errorCode'] == 'LOAD_CONTEXT_REQUIRED'
    changed = deepcopy(state)
    changed['workout']['trainingSessionId'] = 'later-workout'
    assert load_confirmations(changed, original)['errorCode'] == 'LOAD_CONTEXT_MISMATCH'
    changed = deepcopy(state)
    changed['conditions']['gymId'] = 'gym-a'
    assert load_confirmations(changed, original)['errorCode'] == 'LOAD_CONTEXT_MISMATCH'
    assert load_confirmations(state, source(state, '如果哑铃弯举每只10公斤'))['kind'] == 'not_confirmation'
    assert load_confirmations(state, source(state, '我确认哑铃弯举每只10公斤'))['confirmations'][0]['kg'] == 10
