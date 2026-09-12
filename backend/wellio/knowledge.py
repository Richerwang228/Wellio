"""Small, versioned PostgreSQL/pgvector knowledge service; no model advice here."""
import asyncio
import hashlib
import json
import math
import os
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from starlette.concurrency import run_in_threadpool

from .errors import BackendError
from .migrate import validate_database_url

MODEL = 'qwen/qwen3-embedding-4b'
DIMENSIONS = 2560
RECIPE = 'wellio-demo-v1-title-path-body-qwen-query-instruction'
QUERY_INSTRUCTION = 'Instruct: Given a question about adult fitness, sleep or nutrition, retrieve relevant evidence passages that answer the question.\nQuery: '


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def valid_vector(value, dimensions):
    if not isinstance(value, list) or len(value) != dimensions or any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise BackendError('EMBEDDING_INVALID_RESPONSE', 502)
    norm = math.sqrt(sum(x * x for x in value))
    if not math.isfinite(norm) or norm <= 0:
        raise BackendError('EMBEDDING_INVALID_RESPONSE', 502)
    return [float(x / norm) for x in value]


@dataclass(frozen=True)
class EmbeddingConfig:
    api_key: str = field(default='', repr=False)
    model: str = MODEL
    dimensions: int = DIMENSIONS
    timeout: float = 10

    @classmethod
    def from_env(cls):
        return cls(api_key=os.environ.get('OPENROUTER_API_KEY', '').strip())


class OpenRouterEmbeddings:
    def __init__(self, config=None, transport=None):
        self.config = config or EmbeddingConfig.from_env()
        self.client = httpx.AsyncClient(base_url='https://openrouter.ai/api/v1/', timeout=self.config.timeout,
                                       follow_redirects=False, transport=transport)
        self._slots = asyncio.Semaphore(4)

    async def close(self):
        await self.client.aclose()

    async def embed(self, texts):
        # One deadline includes semaphore queueing, every HTTP attempt and retry
        # backoff; a per-request transport timeout alone can multiply latency.
        try:
            async with asyncio.timeout(self.config.timeout):
                return await self._embed(texts)
        except TimeoutError:
            raise BackendError('EMBEDDING_TIMEOUT', 503) from None

    async def _embed(self, texts):
        if not self.config.api_key or any(x in self.config.api_key for x in '\r\n'):
            raise BackendError('OPENROUTER_API_KEY_REQUIRED', 503)
        if not texts or len(texts) > 16 or any(not isinstance(t, str) or not t.strip() or len(t) > 12000 for t in texts):
            raise BackendError('EMBEDDING_INVALID_INPUT', 400)
        async with self._slots:
            for attempt in range(3):
                try:
                    response = await self.client.post('embeddings', headers={'Authorization': 'Bearer ' + self.config.api_key},
                        json={'model': self.config.model, 'input': texts, 'encoding_format': 'float',
                              'provider': {'sort': 'latency', 'allow_fallbacks': True}})
                except httpx.HTTPError:
                    if attempt < 2:
                        await asyncio.sleep(0.3 * 2 ** attempt)
                        continue
                    raise BackendError('EMBEDDING_UNAVAILABLE', 503) from None
                if response.status_code in (429, 500, 502, 503, 504, 529) and attempt < 2:
                    await asyncio.sleep(0.3 * 2 ** attempt)
                    continue
                if response.status_code != 200:
                    code = {401: 'OPENROUTER_AUTH_FAILED', 402: 'OPENROUTER_CREDITS_REQUIRED', 429: 'EMBEDDING_RATE_LIMITED'}.get(response.status_code, 'EMBEDDING_UNAVAILABLE')
                    raise BackendError(code, 503)
                try:
                    payload = response.json()
                    # OpenRouter can return the upstream Qwen/Qwen3-Embedding-4B
                    # capitalization for the canonical lowercase request slug.
                    reported = payload.get('model')
                    if reported is not None and (not isinstance(reported, str) or reported.casefold() != self.config.model.casefold()):
                        raise ValueError('model mismatch')
                    items = payload['data']
                    if not isinstance(items, list) or len(items) != len(texts):
                        raise ValueError('count mismatch')
                    if any(type(x.get('index')) is not int for x in items) or {x['index'] for x in items} != set(range(len(texts))):
                        raise ValueError('index mismatch')
                    return [valid_vector(x['embedding'], self.config.dimensions) for x in sorted(items, key=lambda x: x['index'])]
                except (ValueError, KeyError, TypeError, AttributeError):
                    raise BackendError('EMBEDDING_INVALID_RESPONSE', 502) from None


class KnowledgeStore:
    def __init__(self, database_url, schema='knowledge'):
        self.url = validate_database_url(database_url)
        self.schema = sql.Identifier(schema)

    def connect(self):
        return psycopg.connect(self.url, row_factory=dict_row, connect_timeout=5)

    def initialize(self):
        with self.connect() as conn:
            conn.execute('CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public')
            namespace = conn.execute("SELECT n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='vector'").fetchone()
            if namespace['nspname'] != 'public':
                raise BackendError('PGVECTOR_PUBLIC_SCHEMA_REQUIRED', 503)
            conn.execute(sql.SQL(Path(__file__).with_name('data').joinpath('knowledge_v1.sql').read_text()).format(schema=self.schema), prepare=False)

    def active(self):
        try:
            with self.connect() as conn:
                conn.execute("SET LOCAL statement_timeout='5s'")
                conn.execute("SET LOCAL lock_timeout='1500ms'")
                result = conn.execute(sql.SQL("SELECT * FROM {}.releases WHERE status='active'").format(self.schema)).fetchone()
        except (psycopg.OperationalError, psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
            raise BackendError('KNOWLEDGE_NOT_READY', 503) from None
        except psycopg.Error:
            raise BackendError('KNOWLEDGE_UNAVAILABLE', 503) from None
        if not result:
            raise BackendError('KNOWLEDGE_NOT_READY', 503)
        return result

    def import_release(self, release_id, config, documents, chunks, vectors):
        if not chunks or len(chunks) != len(vectors):
            raise ValueError('EMPTY_OR_INCOMPLETE_RELEASE')
        vectors = [valid_vector(v, config.dimensions) for v in vectors]
        with self.connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(87623001)')
            previous = conn.execute(sql.SQL('SELECT id FROM {}.releases WHERE id=%s').format(self.schema), (release_id,)).fetchone()
            if previous:
                return False  # Content-addressed release is immutable; retry is harmless.
            conn.execute(sql.SQL('INSERT INTO {}.releases(id,model,dimensions,recipe) VALUES (%s,%s,%s,%s)').format(self.schema),
                         (release_id, config.model, config.dimensions, RECIPE))
            for d in documents:
                conn.execute(sql.SQL('INSERT INTO {}.documents VALUES (%s,%s,%s,%s,%s,%s,%s)').format(self.schema),
                    (release_id, d['id'], d['document_version'], d['title'], d['source_url'], d['text'], Jsonb({k:v for k,v in d.items() if k != 'text'})))
            with conn.cursor() as cursor:
                cursor.executemany(sql.SQL('INSERT INTO {}.chunks VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::public.vector)').format(self.schema),
                    [(release_id,c['id'],c['document_id'],config.dimensions,c['text'],c['char_start'],c['char_end'],Jsonb({k:v for k,v in c.items() if k not in ('text','embedding')}),json.dumps(v)) for c,v in zip(chunks,vectors)])
        return True

    def activate(self, release_id):
        with self.connect() as conn:
            conn.execute('SELECT pg_advisory_xact_lock(87623001)')
            exists = conn.execute(sql.SQL('SELECT 1 FROM {}.chunks WHERE release_id=%s LIMIT 1').format(self.schema), (release_id,)).fetchone()
            if not exists:
                raise ValueError('EMPTY_OR_UNKNOWN_RELEASE')
            conn.execute(sql.SQL("UPDATE {}.releases SET status='retired' WHERE status='active'").format(self.schema))
            conn.execute(sql.SQL("UPDATE {}.releases SET status='active' WHERE id=%s").format(self.schema), (release_id,))

    def search(self, release, vector, top_k, topic=None, require_active=True):
        vector = valid_vector(vector, release['dimensions'])
        with self.connect() as conn:
            conn.execute("SET LOCAL statement_timeout='5s'")
            rows = conn.execute(sql.SQL('''SELECT c.id, c.document_id, c.text, c.char_start, c.char_end,
                c.metadata, d.title, d.source_url, d.metadata AS document_metadata,
                1 - (c.embedding OPERATOR(public.<=>) %s::public.vector) AS similarity
                FROM {s}.chunks c JOIN {s}.documents d ON d.release_id=c.release_id AND d.id=c.document_id
                JOIN {s}.releases r ON r.id=c.release_id
                WHERE c.release_id=%s AND (%s=false OR r.status='active')
                AND (%s::text IS NULL OR c.metadata->'topics' ? %s)
                ORDER BY c.embedding OPERATOR(public.<=>) %s::public.vector, c.id LIMIT %s''').format(s=self.schema),
                (json.dumps(vector), release['id'], require_active, topic, topic, json.dumps(vector), top_k * 4)).fetchall()
        results, per_document = [], {}
        for row in rows:
            if per_document.get(row['document_id'], 0) >= 2:
                continue
            per_document[row['document_id']] = per_document.get(row['document_id'], 0) + 1
            meta = row['document_metadata']
            results.append({'chunkId':row['id'], 'documentId':row['document_id'], 'title':row['title'],
                'sourceUrl':row['source_url'], 'publisher':meta['publisher'], 'text':row['text'],
                'headingPath':row['metadata']['heading_path'], 'charStart':row['char_start'], 'charEnd':row['char_end'],
                'documentVersion':meta['document_version'], 'language':meta['language'],
                'population':meta['population'], 'limitations':meta.get('limitations', []),
                'sourceType':meta['source_type'], 'similarity':float(row['similarity']),
                'parentContextRequired':True})
            if len(results) == top_k:
                break
        return results


class KnowledgeService:
    def __init__(self, store, embedder, *, timeout_seconds=12):
        if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 120 or not math.isfinite(timeout_seconds):
            raise ValueError('INVALID_KNOWLEDGE_TIMEOUT')
        self.store, self.embedder = store, embedder
        self.cache = OrderedDict()
        self.timeout_seconds = timeout_seconds
        self._embeddings = {}

    async def close(self):
        tasks = tuple(self._embeddings.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._embeddings.clear()
        await self.embedder.close()

    def current_version(self):
        return self.store.active()['id']

    async def search(self, query, top_k=4, topic=None):
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await self._search(query, top_k, topic)
        except TimeoutError:
            raise BackendError('KNOWLEDGE_TIMEOUT', 503) from None
        except psycopg.Error:
            raise BackendError('KNOWLEDGE_UNAVAILABLE', 503) from None

    async def _embedding(self, key, query):
        # A waiter cancellation cannot cancel the shared provider request. The
        # provider and this task have their own deadlines and close() drains it.
        async with asyncio.timeout(self.timeout_seconds):
            vector = (await self.embedder.embed([QUERY_INSTRUCTION + query]))[0]
        self.cache[key] = vector
        if len(self.cache) > 128:
            self.cache.popitem(last=False)
        return vector

    def _embedding_finished(self, key, task):
        if self._embeddings.get(key) is task:
            self._embeddings.pop(key, None)
        if not task.cancelled():
            task.exception()  # Consume failures even when every caller cancelled.

    async def _search(self, query, top_k, topic):
        release = await run_in_threadpool(self.store.active)
        if (release['model'], release['dimensions'], release['recipe']) != (self.embedder.config.model, self.embedder.config.dimensions, RECIPE):
            raise BackendError('KNOWLEDGE_MODEL_MISMATCH', 503)
        key = fingerprint([release['id'], query])
        if key in self.cache:
            vector = self.cache[key]
            self.cache.move_to_end(key)
        else:
            task = self._embeddings.get(key)
            if task is None:
                task = asyncio.create_task(self._embedding(key, query))
                self._embeddings[key] = task
                task.add_done_callback(lambda completed: self._embedding_finished(key, completed))
            vector = await asyncio.shield(task)
        results = await run_in_threadpool(self.store.search, release, vector, top_k, topic)
        return {'status':'ok' if results else 'no_results', 'knowledgeVersion':release['id'],
                'model':release['model'], 'results':results,
                'usage':'Evidence candidates only. Check applicability and claim support before giving advice.'}
