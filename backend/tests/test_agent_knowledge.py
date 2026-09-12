from copy import deepcopy
from uuid import uuid4
import pytest
from wellio.errors import BackendError
from test_agent_service import TOKEN, ANSWER, opened, tool, rpc


from offline_knowledge_server import FixtureKnowledge


@pytest.fixture
def knowledge_client(client_factory):
    knowledge=FixtureKnowledge()
    return client_factory(agent_token=TOKEN,agent_enabled=True,knowledge_service=knowledge),knowledge


def read(client,run):
    tool(client,run,'get_day_context')
    return tool(client,run,'search_expert_knowledge',{'query':'睡眠不足对训练有什么影响？'}).json()['result']


def answer(receipt):
    return {**ANSWER,'evidenceReadId':receipt['evidenceReadId'],'evidenceChunkIds':['sleep-chunk-1']}


def test_knowledge_is_real_ninth_tool_and_required_before_publish_or_proposal(knowledge_client):
    client,knowledge=knowledge_client
    _,_,run=opened(client)
    assert len(run['tools'])==9 and run['knowledgeRequired'] and run['knowledgeEnabled']
    assert set(run['tools']['search_expert_knowledge']['properties']) == {'query'}
    result=tool(client,run,'search_expert_knowledge',{'query':'睡眠'}).json()
    assert result['result']['errorCode']=='CONTEXT_READ_REQUIRED' and knowledge.calls==0
    context=tool(client,run,'get_day_context').json()['result']
    assert rpc(client,'finish',{'runId':run['runId'],'output':ANSWER}).json()['errorCode']=='KNOWLEDGE_READ_REQUIRED'
    proposal=tool(client,run,'propose_workout',{'scope':'schedule','reason':{'en':'Rest','zh-CN':'休息'},'contextReadId':context['id'],'evidenceReadId':'fake','evidenceChunkIds':['fake']}).json()
    assert proposal['result']['errorCode']=='KNOWLEDGE_READ_REQUIRED'
    result=tool(client,run,'search_expert_knowledge',{'query':'睡眠'}).json()
    assert result['knowledgeRequired'] is False
    receipt=result['result'];assert receipt['contextReadId']==context['id']
    assert any(e.get('step',{}).get('operation')=='knowledge_search' for e in result['events'])
    invalid={**answer(receipt),'evidenceChunkIds':['invented']}
    assert rpc(client,'finish',{'runId':run['runId'],'output':invalid}).json()['errorCode']=='KNOWLEDGE_CITATION_INVALID'
    output=answer(receipt);finished=rpc(client,'finish',{'runId':run['runId'],'output':output})
    assert finished.status_code==200,finished.text
    assert 'https://example.org/sleep' in finished.json()['output']['markdown']
    snapshot=client.get('/api/state').json();message=next(m for m in snapshot['messages'] if m['id']==run['messageId'])
    assert message['sources'][0]['chunkId']=='sleep-chunk-1'
    assert message['content']==finished.json()['output']['markdown']
    assert snapshot['advice']['evidenceReadId']==message['evidenceReadId']
    assert rpc(client,'finish',{'runId':run['runId'],'output':output}).status_code==200
    assert client.get('/api/state').json()['messages']==snapshot['messages']


def test_receipt_cannot_survive_context_reread_or_release_change(knowledge_client):
    client,knowledge=knowledge_client;_,_,run=opened(client);receipt=read(client,run)
    tool(client,run,'get_day_context')
    assert rpc(client,'status',{'runId':run['runId']}).json()['knowledgeRequired']
    assert rpc(client,'finish',{'runId':run['runId'],'output':answer(receipt)}).json()['errorCode']=='KNOWLEDGE_READ_REQUIRED'
    receipt=tool(client,run,'search_expert_knowledge',{'query':'睡眠'}).json()['result']
    knowledge.version='test-release-2'
    assert rpc(client,'finish',{'runId':run['runId'],'output':answer(receipt)}).json()['errorCode']=='KNOWLEDGE_VERSION_STALE'
    assert client.get('/api/state').json()['messages'][-1]['content']==''


def test_receipt_from_another_run_is_rejected(knowledge_client):
    client,knowledge=knowledge_client;_,_,first=opened(client);prior=read(client,first)
    rpc(client,'cancel',{'runId':first['runId']})
    _,_,second=opened(client);new=read(client,second)
    assert prior['evidenceReadId']!=new['evidenceReadId']
    assert rpc(client,'finish',{'runId':second['runId'],'output':answer(prior)}).json()['errorCode']=='KNOWLEDGE_RECEIPT_MISMATCH'


def test_workout_proposal_needs_evidence_then_fresh_evidence_for_final_answer(knowledge_client):
    client,_=knowledge_client
    snapshot=client.get('/api/state').json()
    action={'kind':'request_proposal','requestId':str(uuid4()),'resetEpoch':snapshot['resetEpoch'],'source':'today'}
    run=rpc(client,'open',{'action':action}).json()
    context=tool(client,run,'get_day_context').json()['result']
    receipt=tool(client,run,'search_expert_knowledge',{'query':'Sleep and training'}).json()['result']
    proposed=tool(client,run,'propose_workout',{'scope':'workout','contextReadId':context['id'],
        'reason':{'en':'Keep plan','zh-CN':'保持计划'},'workout':snapshot['workout'],
        'evidenceReadId':receipt['evidenceReadId'],'evidenceChunkIds':['sleep-chunk-1']}).json()
    assert proposed['result']['result']['status']=='succeeded'
    assert rpc(client,'finish',{'runId':run['runId'],'output':answer(receipt)}).json()['errorCode']=='CONTEXT_READ_REQUIRED'
    tool(client,run,'get_day_context')
    assert rpc(client,'finish',{'runId':run['runId'],'output':answer(receipt)}).json()['errorCode']=='KNOWLEDGE_READ_REQUIRED'
    fresh=tool(client,run,'search_expert_knowledge',{'query':'Sleep and training'}).json()['result']
    finished=rpc(client,'finish',{'runId':run['runId'],'output':answer(fresh)}).json()
    assert finished['reply']['result']['status']=='succeeded'
    assert 'https://example.org/sleep' in finished['output']['markdown']


@pytest.mark.parametrize('mode',['empty','failure'])
def test_failed_search_finishes_with_server_notice_and_cannot_authorize_advice(knowledge_client,mode):
    client,knowledge=knowledge_client;setattr(knowledge,mode,True)
    _,_,run=opened(client);tool(client,run,'get_day_context')
    response=tool(client,run,'search_expert_knowledge',{'query':'睡眠'}).json()
    assert response['result']['status']=='failed' and response['knowledgeRequired'] is False
    assert response['knowledgeUnavailable']['markdown']
    state = rpc(client, 'status', {'runId':run['runId']}).json()
    assert state['knowledgeUnavailable'] == response['knowledgeUnavailable'] and state['knowledgeRequired'] is False
    assert tool(client,run,'search_expert_knowledge',{'query':'睡眠'}).json()['result']['errorCode']=='KNOWLEDGE_UNAVAILABLE_THIS_RUN'
    assert knowledge.calls==1
    finished = rpc(client,'finish',{'runId':run['runId'],'output':{**ANSWER, 'evidenceReadId':'invented', 'evidenceChunkIds':['invented']}})
    assert finished.status_code == 200, finished.text
    assert finished.json()['output'] == {'markdown':response['knowledgeUnavailable']['markdown'], 'trainingSummary':None, 'nutritionSummary':None}
    snapshot = client.get('/api/state').json()
    assert snapshot['messages'][-1]['content'] == response['knowledgeUnavailable']['markdown']
    assert 'sources' not in snapshot['messages'][-1]
    assert rpc(client,'finish',{'runId':run['runId'],'output':ANSWER}).status_code == 200


@pytest.mark.parametrize('message,changes,reason', [
    ('你好', {}, 'greeting'), ('What is my readiness score?', {}, 'facts'), ('你好，今天准备度是多少', {}, 'facts'),
    ('今天的准备度是多少？', {}, 'facts'), ('How much protein have I consumed today?', {}, 'facts'),
    ('I only ate half of this item', {'targetMealId':'meal-lunch', 'targetMealItemId':'item-lunch'}, 'meal_receipt'),
    ('Delete this meal', {}, 'clarification'), ('识别这份菜单', {'purpose':'menu'}, 'menu_recognition'),
    ("Briefly summarize my saved lunch and today's scheduled workout. Do not change anything or give health advice.", {}, 'facts'),
    ('概括我今天记录的午餐和训练', {}, 'facts'),
    ('概括我今天记录的午餐和训练，不要修改任何记录或提供健康建议', {}, 'facts'),
    ('Search the public menu of Pret A Manger in Hong Kong. Give me two items supported by the retrieved sources and their source links. Do not record food or make nutrition recommendations.', {}, 'facts'),
    ('Find the public menu of Another Cafe in London. Show me three items with source links. Do not make nutrition recommendations.', {}, 'facts'),
    ('我今天计划咋样', {}, 'facts'), ('今天的训练计划怎么样', {}, 'facts'),
])
def test_nonprofessional_intents_do_not_require_retrieval(knowledge_client, message, changes, reason):
    client, knowledge = knowledge_client
    _, _, run = opened(client, message, **changes)
    assert run['knowledgePolicy'] == {'required':False, 'reason':reason}
    assert run['knowledgeRequired'] is False
    assert tool(client,run,'get_day_context').json()['knowledgeRequired'] is False
    assert rpc(client,'status',{'runId':run['runId']}).json()['knowledgeRequired'] is False
    if reason not in ('meal_receipt',):
        assert rpc(client,'finish',{'runId':run['runId'],'output':{'markdown':'Recorded facts.', 'trainingSummary':None, 'nutritionSummary':None}}).status_code == 200
    assert knowledge.calls == 0


@pytest.mark.parametrize('message', [
    'Review my day.', 'What should I eat?', '今天应该怎么训练？', '你好，请推荐晚餐',
    '总结午餐并推荐如何减脂',
    'Summarize my saved lunch and recommend a workout. Do not give health advice.',
    'Summarize my saved lunch. Do not give health advice, but recommend a low-carb plan.',
    'Search the public menu of Example Cafe in Hong Kong. Recommend the best meal for fat loss. Do not record food.',
    'Search the public menu of Example Cafe in Hong Kong and give nutrition recommendations.',
    'Search the public menu of Example Cafe in Hong Kong and build me a workout.',
    '我今天计划咋样，帮我调整一下', '我今天计划咋样，推荐如何减脂',
])
def test_professional_requests_remain_required(knowledge_client, message):
    client, _ = knowledge_client
    _, _, run = opened(client, message)
    assert run['knowledgePolicy']['required'] and run['knowledgeRequired']
    tool(client,run,'get_day_context')
    assert rpc(client,'finish',{'runId':run['runId'],'output':{**ANSWER,'trainingSummary':None,'nutritionSummary':None}}).json()['errorCode'] == 'KNOWLEDGE_READ_REQUIRED'


def test_greeting_cannot_publish_new_summary_without_evidence(knowledge_client):
    client,_ = knowledge_client
    _,_,run = opened(client,'Hi')
    tool(client,run,'get_day_context')
    assert rpc(client,'finish',{'runId':run['runId'],'output':ANSWER}).json()['errorCode']=='KNOWLEDGE_READ_REQUIRED'


def test_unavailable_knowledge_still_cannot_create_proposal(knowledge_client):
    client,knowledge = knowledge_client
    knowledge.failure = True
    snapshot = client.get('/api/state').json()
    run = rpc(client,'open',{'action':{'kind':'request_proposal','requestId':str(uuid4()),'resetEpoch':snapshot['resetEpoch'],'source':'today'}}).json()
    context = tool(client,run,'get_day_context').json()['result']
    tool(client,run,'search_expert_knowledge',{'query':'Sleep'})
    response = tool(client,run,'propose_workout',{'scope':'schedule','reason':{'en':'Rest','zh-CN':'休息'},'contextReadId':context['id'],'evidenceReadId':'fake','evidenceChunkIds':['fake']}).json()
    assert response['result']['errorCode'] == 'KNOWLEDGE_READ_REQUIRED'


def test_menu_recommendation_is_not_recognition_only(knowledge_client):
    client,_ = knowledge_client
    _,_,run = opened(client, 'Pick the best high protein meal for me', purpose='menu')
    assert run['knowledgePolicy']['required'] and run['knowledgeRequired']


@pytest.mark.parametrize('compact', [False, True])
def test_fifteen_minute_request_creates_pending_proposal_without_changing_conditions(knowledge_client, compact):
    from wellio.authorization import authorize_user_mutation
    client, _ = knowledge_client
    initial, _, run = opened(client, '今天只有十五分钟，帮我生成一个精简训练方案。', locale='zh-CN')
    assert run['preparedIntent']['kind'] == 'workout_proposal'
    assert run['knowledgeRequired'] is True
    assert 'workout' not in run['tools']['propose_workout']['properties']
    assert 'keepExerciseIds' in run['tools']['propose_workout']['required']
    assert 'not a clarification request' in run['instructions']
    current = client.get('/api/state').json()
    assert current['conditions'] == initial['conditions'] and current['workout'] == initial['workout']
    database = client.app.state.database
    saved_run = database.get_agent_run(initial['sessionId'], run['runId'])
    with pytest.raises(BackendError, match='EXPLICIT_USER_INTENT_REQUIRED'):
        authorize_user_mutation(database, initial['sessionId'], {'sourceMessageId':saved_run['sourceMessageId'], 'resetEpoch':initial['resetEpoch'], 'runId':run['runId']})
    context = tool(client, run, 'get_day_context').json()['result']
    receipt = tool(client, run, 'search_expert_knowledge', {'query':'Shorten the current Pull workout to fifteen minutes'}).json()['result']
    candidate = deepcopy(initial['workout'])
    candidate['exercises'] = candidate['exercises'][:2]
    candidate['estimatedMinutes'] = 16
    payload = {'scope':'workout', 'workout':candidate, 'reason':{'en':'Shorter Pull session','zh-CN':'精简拉类训练'},
               'contextReadId':context['id'], 'evidenceReadId':receipt['evidenceReadId'], 'evidenceChunkIds':['sleep-chunk-1']}
    assert tool(client, run, 'propose_workout', payload).json()['result']['errorCode'] == 'WORKOUT_TIME_EXCEEDED'
    candidate['estimatedMinutes'] = 15
    if compact:
        payload.pop('workout')
        payload['keepExerciseIds'] = [exercise['id'] for exercise in candidate['exercises']]
        assert tool(client, run, 'propose_workout', {**payload, 'keepExerciseIds':['invented']}).json()['result']['errorCode'] == 'INVALID_EXERCISE_ID'
        assert tool(client, run, 'propose_workout', {**payload, 'keepExerciseIds':payload['keepExerciseIds'] * 2}).json()['result']['errorCode'] == 'INVALID_EXERCISE_ID'
    reply = tool(client, run, 'propose_workout', payload).json()
    assert reply['result']['result']['status'] == 'succeeded', reply
    pending = client.get('/api/state').json()
    assert pending['conditions'] == initial['conditions'] and pending['workout'] == initial['workout']
    proposal = pending['proposals'][-1]
    assert proposal['status'] == 'pending' and proposal['workout']['estimatedMinutes'] == 15
    assert proposal['workout']['trainingSessionId'] == initial['workout']['trainingSessionId']
    assert [item['suggestedLoad'] for item in proposal['workout']['exercises']] == [item['suggestedLoad'] for item in candidate['exercises']]
    applied = client.post('/api/actions', json={'kind':'apply_proposal', 'requestId':str(uuid4()),
        'resetEpoch':initial['resetEpoch'], 'source':'today', 'proposalId':proposal['id'], 'startAfterApply':False})
    assert applied.status_code == 200, applied.text
    final = client.get('/api/state').json()
    assert final['workout']['estimatedMinutes'] == 15 and final['workout']['status'] == 'planned'
    assert final['conditions'] == initial['conditions']
