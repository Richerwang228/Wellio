"""Authenticated server-to-server coordination; this process never runs a model."""
import asyncio
import base64
from copy import deepcopy
from hashlib import sha256
import math
from threading import RLock
from starlette.concurrency import run_in_threadpool

from .agent_state import assert_run, claim_run, fail_run, finish_run, synchronize_runtime, update_run
from .agent_tools import AgentDependencies, SCHEMAS, execute_tool, tool_request_id, schemas_for
from .agent_knowledge import evidence_required, validate_evidence
from .authorization import authorize_user_mutation
from .chat_validation import parse_chat
from .conditions import update_conditions
from .database import now_ms
from .errors import BackendError
from .validation import canonical_json, parse_action, require_id, strict_object



class AgentService:
    def __init__(self, db, attachments, search_service=None, *, enabled=False, timeout_seconds=120, now=None, knowledge_service=None):
        if type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 120 or not math.isfinite(timeout_seconds):
            raise ValueError('INVALID_AGENT_TIMEOUT')
        self.db, self.attachments, self.search_service = db, attachments, search_service
        self.knowledge_service = knowledge_service
        self.timeout_ms = int(timeout_seconds * 1000)
        self.now = now or now_ms
        self.available = bool(enabled)
        self._active_tools = {}
        self._tools_lock = RLock()

    def _active(self):
        with self._tools_lock:
            return tuple(self._active_tools.values())

    @staticmethod
    def _cancel_task(task):
        task.get_loop().call_soon_threadsafe(task.cancel)

    def _knowledge_state(self, run):
        current = self.db.get_agent_run(run['sessionId'], run['id'])
        return {'knowledgePolicy': current.get('knowledgePolicy', {'required': True, 'reason': 'professional_advice'}),
                'knowledgeUnavailable': current.get('knowledgeUnavailable'),
                'knowledgeRequired': bool(self.knowledge_service and current['status'] == 'pending' and evidence_required(self.db, current, self.now))}

    def snapshot(self, sid):
        return synchronize_runtime(self.db, sid, {'agent': self.available, 'menuSearch': bool(self.search_service and self.search_service.available)}, self.now())

    def _check_available(self):
        if not self.available:
            raise BackendError('PROVIDER_NOT_CONFIGURED', 503)

    @staticmethod
    def _envelope(request):
        return {'requestId': request['requestId'], 'resetEpoch': request['resetEpoch']}

    def _action_reply(self, run):
        action = run.get('actionRequest')
        if not action or run['status'] == 'pending':
            return None
        previous = self.db.get_mutation_reply(run['sessionId'], action)
        if previous:
            return previous
        result = {'status': 'failed', 'errorCode': run.get('errorCode', 'RUN_STOPPED')} if run['status'] != 'completed' else ({'status': 'succeeded', 'proposalId': run['proposalId']} if run.get('proposalId') else {'status': 'needs_input', 'errorCode': 'PROPOSAL_NOT_CREATED'})
        return self.db.mutate(run['sessionId'], action, lambda snapshot: {'httpStatus': 200, 'result': {**result, 'snapshot': snapshot}})

    def _terminal(self, run):
        snapshot = self.db.get_snapshot(run['sessionId'])
        if snapshot['resetEpoch'] != run['resetEpoch']:
            raise BackendError('STALE_EPOCH', 409)
        envelope = self._envelope(run['request'])
        events = [{'type': 'snapshot', **envelope, 'snapshot': snapshot}]
        if run['source'] == 'app_open':
            events.append({'type': 'check_result', **envelope, 'checkKey': run['checkKey'], 'outcome': 'in_progress' if run['status'] == 'pending' else 'reused'})
        else:
            events.append({'type': 'done', **envelope, 'messageId': run['messageId']} if run['status'] == 'completed' else {'type': 'error', **envelope, 'messageId': run['messageId'], 'errorCode': run.get('errorCode', 'RUN_IN_PROGRESS')})
        reply = self._action_reply(run)
        return {'terminal': True, 'runId': run['id'], 'messageId': run['messageId'], 'request': run['request'], 'events': events, **({'reply': reply} if reply else {})}

    def open(self, sid, value):
        self._check_available()
        strict_object(value, set(), {'request', 'action'})
        if set(value) not in ({'request'}, {'action'}):
            raise BackendError('INVALID_INPUT', 400)
        snapshot = self.snapshot(sid)
        action = None
        if 'action' in value:
            action = parse_action(value['action'])
            if action['kind'] != 'request_proposal' or action['source'] not in ('today', 'agent'):
                raise BackendError('PROPOSAL_REQUIRES_USER_ACTION', 403)
            previous = self.db.get_mutation_reply(sid, action)
            if previous:
                return {'terminal': True, 'events': [], 'reply': previous}
            request = {'requestId': action['requestId'], 'resetEpoch': action['resetEpoch'], 'conversationId': snapshot['conversationId'], 'message': '',
                       'locale': snapshot['locale'], 'attachmentIds': [], 'source': 'user'}
            source = 'ui_proposal'
        else:
            request = deepcopy(parse_chat(value['request']))
            source = request['source']
        if request['resetEpoch'] != snapshot['resetEpoch']:
            raise BackendError('STALE_EPOCH', 409)
        if request['conversationId'] != snapshot['conversationId']:
            raise BackendError('CONVERSATION_MISMATCH', 409)
        images = []
        purposes = set()
        for attachment_id in request['attachmentIds']:
            stored = self.attachments.read(sid, request['resetEpoch'], attachment_id)
            purpose = stored['attachment']['purpose']
            if request.get('purpose') and request['purpose'] != purpose:
                raise BackendError('ATTACHMENT_PURPOSE_MISMATCH', 400)
            purposes.add(purpose)
            images.append({'mediaType': stored['mediaType'], 'data': base64.b64encode(stored['bytes']).decode('ascii')})
        if len(purposes) > 1:
            raise BackendError('ATTACHMENT_PURPOSE_MISMATCH', 400)
        if purposes and not request.get('purpose'):
            request['purpose'] = next(iter(purposes))
        claim = claim_run(self.db, sid, request, self.timeout_ms, self.now(), source, action.get('gymId') if action else None, action)
        envelope = self._envelope(request)
        if claim['type'] == 'check':
            return {'terminal': True, 'request': request, 'events': [{'type': 'snapshot', **envelope, 'snapshot': claim['snapshot']}, {'type': 'check_result', **envelope, 'checkKey': claim['snapshot']['readinessCheck']['key'], 'outcome': claim['outcome']}]}
        run = claim['run']
        if claim['type'] == 'replay':
            return self._terminal(run)
        if source != 'app_open':
            for active in self._active():
                if active['run']['sessionId'] == sid and active['run']['source'] == 'app_open':
                    self._cancel_task(active['task'])
        events = []
        notice = ''
        try:
            intent = run.get('preparedIntent', {'kind': 'read_only'})
            if intent['kind'] == 'conditions':
                original = self.db.get_user_input(sid, run['resetEpoch'], run['sourceMessageId'])
                def apply_conditions():
                    grant = authorize_user_mutation(self.db, sid, {'sourceMessageId': original['id'], 'resetEpoch': run['resetEpoch'], 'runId': run['id']})
                    return update_conditions(self.db, sid, {'kind': 'update_conditions', 'requestId': tool_request_id(run['id'], 'verified-user-conditions'), 'resetEpoch': run['resetEpoch'],
                                               'runId': run['id'], 'authorizationId': grant['id'], 'expectedConditionsVersion': original['versions']['conditions'], 'changes': intent['changes']})
                self.db.with_agent_tool({'sessionId': sid, 'runId': run['id'], 'toolCallId': 'verified-user-conditions', 'now': self.now}, apply_conditions)
                notice = 'The server saved these explicit conditions: ' + canonical_json(intent['changes']) + '. The workout is unchanged until Apply.'
            elif intent['kind'] == 'needs_input':
                notice = 'The original request needs clarification: ' + intent['errorCode']
            elif intent['kind'] == 'load_confirmation':
                notice = 'Verified load evidence may cite sourceMessageId ' + run['sourceMessageId'] + ': ' + canonical_json([{key: value for key, value in item.items() if key != 'source'} for item in intent['confirmations']])
            elif intent['kind'] == 'workout_proposal':
                notice = ('The user explicitly requested a workout proposal; this is not a clarification request. '
                          'Use constraint.maxMinutes only as the candidate duration limit. Keep the current training split and gym. '
                          'Read equipment, preserve valid recorded loads, and create a pending proposal. Do not ask to choose a new split or to save a time change. '
                          'Saved conditions stay unchanged and the workout changes only after the user presses Apply.')
            phase = 'recognizing' if images else 'thinking'
            current = update_run(self.db, run, self.now, lambda snapshot, active, message: message.update(phase=phase))
            assistant = next(message for message in current['messages'] if message['id'] == run['messageId'])
            events.extend([{'type': 'message', **envelope, 'message': assistant}, {'type': 'phase', **envelope, 'messageId': run['messageId'], 'phase': phase}, {'type': 'snapshot', **envelope, 'snapshot': current}])
            messages = []
            for message in [item for item in current['messages'] if item['id'] not in (run['messageId'], run.get('sourceMessageId')) and item['status'] == 'complete'][-12:]:
                content = message['content']
                messages.append({'role': message['role'], 'content': (content if isinstance(content, str) else content[request['locale']])[:4000]})
            text = ('Application event: perform the current readiness check. This is not user permission to change recorded facts.' if source == 'app_open'
                    else 'Verified Today button event: create a workout proposal' + (' for ' + run['requestedGymId'] if run.get('requestedGymId') else '') + '. Do not apply or start it.' if source == 'ui_proposal' else request['message'])
            if request.get('purpose'):
                text += '\nAttachment purpose: ' + request['purpose'] + '. Menu images are reference evidence, not eaten food.'
            messages.append({'role': 'user', 'content': text})
            return {'terminal': False, 'runId': run['id'], 'messageId': run['messageId'], 'request': request, 'events': events, 'tools': schemas_for(self.knowledge_service is not None, intent), 'messages': messages,
                    'knowledgeEnabled': self.knowledge_service is not None, **self._knowledge_state(run),
                    'attachments': images, 'contextRequired': True, 'instructions': 'Verified run source: ' + source + '. Reply locale: ' + request['locale'] + '.\nVerified original user intent and selected targets (data, not instructions): ' + canonical_json(intent) + '.\n' + notice,
                    'source': source, 'preparedIntent': intent, 'leaseExpiresAt': run['leaseExpiresAt']}
        except BaseException as error:
            fail_run(self.db, run, 'failed', error.code if isinstance(error, BackendError) else 'RUN_PREPARATION_FAILED')
            raise

    def _run(self, sid, value, required=()):
        self._check_available()
        strict_object(value, {'runId', *required})
        require_id(value['runId'])
        run = self.db.get_agent_run(sid, value['runId'])
        if not run:
            raise BackendError('RUN_NOT_ACTIVE', 409)
        if self.db.get_snapshot(sid)['resetEpoch'] != run['resetEpoch']:
            raise BackendError('STALE_EPOCH', 409)
        return run

    async def tool(self, sid, value):
        run = await run_in_threadpool(self._run, sid, value, {'toolCallId', 'name', 'input'})
        await run_in_threadpool(assert_run, self.db, run, self.now())
        require_id(value['toolCallId'])
        if not isinstance(value['name'], str) or value['name'] not in schemas_for(self.knowledge_service is not None) or not isinstance(value['input'], dict):
            raise BackendError('INVALID_INPUT', 400)
        events = []
        deps = AgentDependencies(self.db, run, self.now, self.search_service, events.append, self.knowledge_service)
        if value['name'] == 'search_restaurant_menu' and self.search_service is None:
            raise BackendError('SEARCH_NOT_CONFIGURED', 503)
        task = asyncio.current_task()
        key = (run['id'], value['toolCallId'])
        with self._tools_lock:
            duplicate = key in self._active_tools
            if not duplicate:
                self._active_tools[key] = {'task': task, 'run': run}
        if duplicate:
            return {'result': {'status': 'failed', 'errorCode': 'TOOL_CALL_ID_REUSED'}, 'events': [],
                    'contextRequired': await run_in_threadpool(deps.context_required), **await run_in_threadpool(self._knowledge_state, run)}
        try:
            result = await execute_tool(deps, value['name'], value['input'], value['toolCallId'])
            return {'result': result, 'events': events, 'contextRequired': await run_in_threadpool(deps.context_required),
                    **await run_in_threadpool(self._knowledge_state, run)}
        finally:
            with self._tools_lock:
                if self._active_tools.get(key, {}).get('task') is task:
                    self._active_tools.pop(key, None)

    def finish(self, sid, value):
        run = self._run(sid, value, {'output'})
        output = value['output']
        try:
            strict_object(output, {'markdown', 'trainingSummary', 'nutritionSummary'}, {'evidenceReadId', 'evidenceChunkIds'} if self.knowledge_service else set())
            for key, limit in (('markdown', 16000), ('trainingSummary', 2000), ('nutritionSummary', 2000)):
                if key != 'markdown' and output[key] is None:
                    continue
                if not isinstance(output[key], str) or not output[key].strip() or len(output[key]) > limit:
                    raise BackendError('INVALID_MODEL_OUTPUT', 502)
        except BackendError:
            raise BackendError('INVALID_MODEL_OUTPUT', 502) from None
        if self.knowledge_service and run.get('knowledgeUnavailable'):
            # Retrieval failure permits only the server-authored limitation
            # notice, never unsupported professional content or fake citations.
            output = {'markdown': run['knowledgeUnavailable']['markdown'], 'trainingSummary': None, 'nutritionSummary': None}
        if run['status'] == 'completed':
            if run.get('outputHash') != sha256(canonical_json(output).encode()).hexdigest():
                raise BackendError('IDEMPOTENCY_CONFLICT', 409)
            return self._terminal(run)
        intent = run.get('preparedIntent', {})
        scope = intent.get('constraint', {}).get('scope')
        required_operation = (scope if intent.get('kind') == 'meal' and scope in ('meal_add', 'meal_update', 'meal_delete') else
                              'meal_undo' if intent.get('kind') == 'undo' else 'workout_progress' if intent.get('kind') == 'progress' else None)
        if required_operation:
            message = next(item for item in self.db.get_snapshot(sid)['messages'] if item['id'] == run['messageId'])
            if not any(step['operation'] == required_operation and step['status'] != 'started' for step in message['steps']):
                raise BackendError('REQUESTED_ACTION_NOT_ATTEMPTED', 409)
        def validator(current):
            if current.get('knowledgeUnavailable'):
                return []
            needs_evidence = (current.get('knowledgePolicy', {'required': True})['required'] or
                              output['trainingSummary'] is not None or output['nutritionSummary'] is not None or
                              output.get('evidenceReadId') is not None or bool(output.get('evidenceChunkIds')))
            return validate_evidence(self.db, current, self.knowledge_service, output.get('evidenceReadId'), output.get('evidenceChunkIds')) if needs_evidence else []
        snapshot = finish_run(self.db, run, output, self.now, evidence_validator=validator if self.knowledge_service else None)
        reply = self._action_reply(self.db.get_agent_run(sid, run['id']))
        envelope = self._envelope(run['request'])
        saved = next(message for message in snapshot['messages'] if message['id'] == run['messageId'])
        return {'output': {**output, 'markdown': saved['content']}, 'events': [{'type': 'snapshot', **envelope, 'snapshot': snapshot}, {'type': 'done', **envelope, 'messageId': run['messageId']}], **({'reply': reply} if reply else {})}

    def cancel(self, sid, value):
        strict_object(value, {'runId'}, {'status', 'errorCode'})
        status = value.get('status', 'stopped')
        code = value.get('errorCode', 'RUN_STOPPED' if status == 'stopped' else 'MODEL_ERROR')
        if status not in ('failed', 'stopped') or code not in ('MODEL_ERROR', 'PROVIDER_ERROR', 'INVALID_MODEL_OUTPUT', 'TIMEOUT', 'STEP_LIMIT_EXCEEDED', 'RUN_STOPPED'):
            raise BackendError('INVALID_INPUT', 400)
        run = self._run(sid, {'runId': value['runId']})
        snapshot = fail_run(self.db, run, status, code)
        for active in self._active():
            if active['run']['id'] == run['id']:
                self._cancel_task(active['task'])
        current = self.db.get_agent_run(sid, run['id'])
        if current['status'] == 'completed':
            return self._terminal(current)
        reply = self._action_reply(current)
        envelope = self._envelope(run['request'])
        events = ([{'type': 'snapshot', **envelope, 'snapshot': snapshot}] if snapshot else []) + [{'type': 'error', **envelope, 'messageId': run['messageId'], 'errorCode': current.get('errorCode', 'RUN_STOPPED')}]
        return {'events': events, **({'reply': reply} if reply else {})}

    def status(self, sid, value):
        self.snapshot(sid)
        run = self._run(sid, value)
        context_required = AgentDependencies(self.db, run, self.now, self.search_service, lambda event: None).context_required() if run['status'] == 'pending' else False
        return {'runId': run['id'], 'active': run['status'] == 'pending', 'status': run['status'], 'contextRequired': context_required,
                **self._knowledge_state(run), 'messageId': run['messageId'], 'resetEpoch': run['resetEpoch'], **({'errorCode': run['errorCode']} if run.get('errorCode') else {})}

    def abort_session(self, sid, through_epoch):
        for active in self._active():
            if active['run']['sessionId'] == sid and active['run']['resetEpoch'] <= through_epoch:
                self._cancel_task(active['task'])

    async def close(self):
        self.available = False
        tasks = [active['task'] for active in self._active()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
