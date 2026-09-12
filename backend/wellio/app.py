"""FastAPI transport preserving the existing same-origin /api contract."""
import json
import logging
import asyncio
import anyio
import hmac
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse, Response

from .actions import execute_action
from .attachments import AttachmentStore, MAX_ATTACHMENT_BYTES
from .database import Database, now_ms
from .errors import BackendError
from .session import SessionCookies, SESSION_MAX_AGE_SECONDS
from .validation import parse_action, action_adapter
from .agent_service import AgentService


def json_response(value, status=200, headers=None):
    return JSONResponse(value, status_code=status, headers={
        'Cache-Control': 'no-store', 'Vary': 'Cookie', 'X-Content-Type-Options': 'nosniff',
        'X-Wellio-Schema-Version': '4', **(headers or {}),
    })


def assert_same_origin(request, public_origins):
    allowed = {str(request.base_url).rstrip('/'), *public_origins}
    origin, referer = request.headers.get('origin'), request.headers.get('referer')
    if request.headers.get('sec-fetch-site') == 'cross-site' or (origin is not None and origin not in allowed):
        raise BackendError('ORIGIN_NOT_ALLOWED', 403)
    if origin is None and referer is not None:
        parsed = urlparse(referer)
        if f'{parsed.scheme}://{parsed.netloc}' not in allowed:
            raise BackendError('ORIGIN_NOT_ALLOWED', 403)


async def read_body(request, maximum):
    declared = request.headers.get('content-length')
    if declared is not None and (not declared.isascii() or not declared.isdigit() or int(declared) > maximum):
        raise BackendError('PAYLOAD_TOO_LARGE', 413)
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > maximum:
            raise BackendError('PAYLOAD_TOO_LARGE', 413)
        data.extend(chunk)
    return bytes(data)


async def read_json(request, maximum=16 * 1024):
    if request.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
        raise BackendError('UNSUPPORTED_MEDIA_TYPE', 415)
    data = await read_body(request, maximum)
    def reject_constant(_value):
        raise ValueError()
    try:
        return json.loads(data.decode('utf-8'), parse_constant=reject_constant)
    except (ValueError, UnicodeError):
        raise BackendError('INVALID_INPUT', 400) from None


async def read_attachment_form(request):
    if not request.headers.get('content-type', '').startswith('multipart/form-data;'):
        raise BackendError('UNSUPPORTED_MEDIA_TYPE', 415)
    raw = await read_body(request, MAX_ATTACHMENT_BYTES + 64 * 1024)
    async def receive():
        return {'type': 'http.request', 'body': raw, 'more_body': False}
    buffered = Request(request.scope, receive)
    try:
        async with buffered.form(max_files=1, max_fields=1, max_part_size=MAX_ATTACHMENT_BYTES + 64 * 1024) as form:
            file, purpose = form.get('file'), form.get('purpose')
            if len(form.multi_items()) != 2 or not isinstance(file, UploadFile) or purpose not in ('food', 'menu'):
                raise BackendError('INVALID_INPUT', 400)
            return await file.read(MAX_ATTACHMENT_BYTES + 1), file.filename, purpose
    except (ValueError, HTTPException):
        raise BackendError('INVALID_INPUT', 400) from None


async def until_disconnect(request, operation):
    """Keep cancellation active during preflight, before streaming headers exist."""
    async def disconnected():
        while (await request.receive())['type'] != 'http.disconnect':
            pass
    task = asyncio.ensure_future(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait((task, watcher), return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            task.cancel()
            raise asyncio.CancelledError()
        return await task
    finally:
        watcher.cancel()
        if not task.done():
            task.cancel()
        with anyio.move_on_after(3, shield=True):
            await asyncio.gather(task, watcher, return_exceptions=True)


def create_app(database_url, attachments_path=None, public_origins=(), cookie_secure=None, search_service=None, agent_token=None, agent_enabled=False, agent_timeout_seconds=120, now=None, knowledge_service=None):
    database = Database(database_url)
    cookies = SessionCookies(database.signing_key)
    attachments = AttachmentStore(attachments_path or Path('.data/attachments').absolute())
    token_valid = isinstance(agent_token, str) and 1 <= len(agent_token) <= 4096 and agent_token.strip() == agent_token and not any(ord(char) < 33 or ord(char) == 127 for char in agent_token)
    agent = AgentService(database, attachments, search_service, enabled=bool(agent_enabled and token_valid), timeout_seconds=agent_timeout_seconds, now=now, knowledge_service=knowledge_service)

    @asynccontextmanager
    async def lifespan(_app):
        try:
            yield
        finally:
            try:
                await agent.close()
                if search_service is not None:
                    await search_service.close()
                if knowledge_service is not None:
                    await knowledge_service.close()
            finally:
                await run_in_threadpool(database.close)

    app = FastAPI(title='Wellio API', version='1.0.0', lifespan=lifespan, redirect_slashes=False)
    app.state.database = database
    app.state.attachments = attachments
    app.state.search = search_service
    app.state.agent = agent
    app.state.knowledge = knowledge_service

    @app.exception_handler(HTTPException)
    async def http_error(_request, error):
        return json_response({'errorCode': 'METHOD_NOT_ALLOWED' if error.status_code == 405 else 'NOT_FOUND'}, error.status_code, error.headers)

    @app.get('/healthz')
    async def health():
        try:
            await asyncio.wait_for(run_in_threadpool(database.health), timeout=10)
        except (BackendError, TimeoutError) as error:
            code = error.code if isinstance(error, BackendError) else 'DATABASE_TIMEOUT'
            return json_response({'status': 'unavailable', 'backend': 'fastapi', 'schemaVersion': 4, 'errorCode': code}, 503)
        except Exception as error:
            logging.getLogger('wellio').error('Database health check failed (%s)', type(error).__name__)
            return json_response({'status': 'unavailable', 'backend': 'fastapi', 'schemaVersion': 4, 'errorCode': 'DATABASE_UNAVAILABLE'}, 503)
        return json_response({'status': 'ok', 'backend': 'fastapi', 'schemaVersion': 4})

    @app.get('/readyz')
    async def readiness():
        # Full demo prerequisites are separate from business database health:
        # missing model/search/knowledge setup must not disable fact APIs.
        database_response = await health()
        checks = {
            'database': {'status': 'ok' if database_response.status_code == 200 else 'unavailable'},
            'agent': {'status': 'configured' if agent.available else 'unconfigured'},
            'search': {'status': 'configured' if search_service is not None and search_service.available else 'unconfigured'},
        }
        if knowledge_service is None:
            checks['knowledge'] = {'status': 'unavailable', 'errorCode': 'KNOWLEDGE_NOT_READY'}
        else:
            try:
                version = await asyncio.wait_for(run_in_threadpool(knowledge_service.current_version), timeout=6)
                checks['knowledge'] = {'status': 'ready', 'activeVersion': version}
            except (BackendError, TimeoutError) as error:
                checks['knowledge'] = {'status': 'unavailable', 'errorCode': error.code if isinstance(error, BackendError) else 'KNOWLEDGE_TIMEOUT'}
            except Exception as error:
                logging.getLogger('wellio').error('Knowledge readiness check failed (%s)', type(error).__name__)
                checks['knowledge'] = {'status': 'unavailable', 'errorCode': 'KNOWLEDGE_NOT_READY'}
        ready = all(value['status'] in ('ok', 'configured', 'ready') for value in checks.values())
        return json_response({'status': 'ready' if ready else 'unavailable', 'checks': checks}, 200 if ready else 503)

    async def internal_agent(request: Request):
        try:
            supplied = request.headers.getlist('authorization')
            expected = ('Bearer ' + agent_token).encode() if token_valid else b''
            if not token_valid or len(supplied) != 1 or not hmac.compare_digest(supplied[0].encode(), expected):
                raise BackendError('AGENT_AUTH_REQUIRED', 401)
            assert_same_origin(request, public_origins)
            sid = cookies.read(request)
            if not sid:
                raise BackendError('SESSION_REQUIRED', 401)
            await run_in_threadpool(database.get_snapshot, sid)
            value = await read_json(request, 128 * 1024)
            operation = request.path_params['operation']
            if operation == 'tool':
                try:
                    result = await until_disconnect(request, agent.tool(sid, value))
                except asyncio.CancelledError:
                    if isinstance(value, dict) and isinstance(value.get('runId'), str):
                        try:
                            await run_in_threadpool(agent.cancel, sid, {'runId': value['runId']})
                        except BackendError:
                            pass
                    raise
            elif operation == 'open':
                result = await run_in_threadpool(agent.open, sid, value)
            elif operation in ('finish', 'cancel', 'status'):
                result = await run_in_threadpool(getattr(agent, operation), sid, value)
            else:
                raise BackendError('NOT_FOUND', 404)
            return json_response(result)
        except BackendError as error:
            return json_response({'status': 'conflict' if error.http_status == 409 else 'failed', 'errorCode': error.code}, error.http_status)
        except Exception as error:
            logging.getLogger('wellio').error('Internal agent request failed (%s)', type(error).__name__)
            return json_response({'status': 'failed', 'errorCode': 'INTERNAL_ERROR'}, 500)

    async def handle(request: Request):
        action = None
        secure = cookie_secure if cookie_secure is not None else request.url.scheme == 'https'
        try:
            assert_same_origin(request, public_origins)
            sid = cookies.read(request)
            path = request.url.path
            if path == '/api/state':
                headers = {}
                if not sid:
                    expires = now_ms() + SESSION_MAX_AGE_SECONDS * 1000
                    snapshot = await run_in_threadpool(database.create_session, expires)
                    sid = snapshot['sessionId']
                    headers['Set-Cookie'] = cookies.issue(sid, expires, secure)
                snapshot = await run_in_threadpool(agent.snapshot, sid)
                return json_response(snapshot, headers=headers)
            if not sid:
                raise BackendError('SESSION_REQUIRED', 401)
            current = await run_in_threadpool(database.get_snapshot, sid)
            if path == '/api/knowledge/search':
                value = await read_json(request, 8192)
                if (not isinstance(value, dict) or set(value) - {'query', 'topK', 'topic'}
                    or not isinstance(value.get('query'), str) or not 1 <= len(value['query'].strip()) <= 1000
                    or type(value.get('topK', 4)) is not int or not 1 <= value.get('topK', 4) <= 6
                    or ('topic' in value and (not isinstance(value['topic'], str) or not 1 <= len(value['topic']) <= 64))):
                    raise BackendError('INVALID_INPUT', 400)
                if knowledge_service is None:
                    raise BackendError('KNOWLEDGE_NOT_READY', 503)
                try:
                    result = await asyncio.wait_for(knowledge_service.search(value['query'].strip(), value.get('topK', 4), value.get('topic')), timeout=20)
                except TimeoutError:
                    raise BackendError('KNOWLEDGE_TIMEOUT', 503) from None
                return json_response(result)
            if path.startswith('/api/attachments/'):
                stored = await run_in_threadpool(attachments.read, sid, current['resetEpoch'], request.path_params['attachment_id'])
                return Response(stored['bytes'], headers={'Content-Type': stored['mediaType'], 'Cache-Control': 'private, no-store', 'Vary': 'Cookie', 'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'"})
            if path == '/api/attachments':
                data, name, purpose = await read_attachment_form(request)
                saved = await run_in_threadpool(attachments.upload, sid, current['resetEpoch'], data, name, purpose)
                if (await run_in_threadpool(database.get_snapshot, sid))['resetEpoch'] != current['resetEpoch']:
                    await run_in_threadpool(attachments.reset, sid, current['resetEpoch'])
                    raise BackendError('STALE_EPOCH', 409)
                return json_response(saved)
            if path == '/api/chat':
                raise BackendError('USE_COPILOTKIT_RUNTIME' if agent.available else 'PROVIDER_NOT_CONFIGURED', 410 if agent.available else 503)
            action = parse_action(await read_json(request))
            if action['kind'] == 'request_proposal' and agent.available:
                raise BackendError('USE_COPILOTKIT_RUNTIME', 410)
            elif action['kind'] == 'check_readiness' and agent.available:
                raise BackendError('USE_CHAT_READINESS_CHECK', 410)
            else:
                reply = await run_in_threadpool(execute_action, database, sid, action)
            if action['kind'] == 'reset_demo' and reply['result']['status'] == 'succeeded':
                await run_in_threadpool(agent.abort_session, sid, action['resetEpoch'])
                await run_in_threadpool(attachments.reset, sid, action['resetEpoch'])
            return json_response(reply['result'], reply['httpStatus'])
        except Exception as error:
            known = isinstance(error, BackendError)
            code, status = (error.code, error.http_status) if known else ('INTERNAL_ERROR', 500)
            if not known:
                logging.getLogger('wellio').error('Backend request failed (%s)', type(error).__name__)
            headers = {'Set-Cookie': cookies.clear(secure)} if code == 'INVALID_SESSION' and request.method == 'GET' and request.url.path == '/api/state' else {}
            return json_response({**({'requestId': action['requestId'], 'resetEpoch': action['resetEpoch']} if action else {}), 'status': 'conflict' if status == 409 else 'failed', 'errorCode': code}, status, headers)

    app.add_api_route('/api/state', handle, methods=['GET'], name='get_state')
    action_schema = action_adapter.json_schema(ref_template='#/components/schemas/{model}')
    definitions = action_schema.pop('$defs', {})
    app.add_api_route('/api/actions', handle, methods=['POST'], name='execute_action', openapi_extra={'requestBody': {'required': True, 'content': {'application/json': {'schema': action_schema}}}})
    original_openapi = app.openapi
    def openapi():
        schema = original_openapi()
        schema.setdefault('components', {}).setdefault('schemas', {}).update(definitions)
        return schema
    app.openapi = openapi
    app.add_api_route('/api/chat', handle, methods=['POST'], name='chat')
    app.add_api_route('/api/knowledge/search', handle, methods=['POST'], name='search_knowledge', openapi_extra={
        'requestBody': {'required': True, 'content': {'application/json': {'schema': {
            'type': 'object', 'additionalProperties': False, 'required': ['query'], 'properties': {
                'query': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
                'topK': {'type': 'integer', 'minimum': 1, 'maximum': 6, 'default': 4},
                'topic': {'type': 'string', 'minLength': 1, 'maxLength': 64}}}}}}})
    app.add_api_route('/internal/agent/{operation}', internal_agent, methods=['POST'], name='internal_agent', include_in_schema=False)
    app.add_api_route('/api/attachments', handle, methods=['POST'], name='upload_attachment')
    app.add_api_route('/api/attachments/{attachment_id}', handle, methods=['GET'], name='read_attachment')
    return app
