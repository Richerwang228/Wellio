from uuid import uuid4
import asyncio
import time
import httpx
import psycopg
from psycopg import sql
import pytest
from wellio.knowledge import EmbeddingConfig, OpenRouterEmbeddings, KnowledgeStore, KnowledgeService, valid_vector
from wellio.errors import BackendError

@pytest.mark.asyncio
async def test_embedding_validates_indexes_dimensions_and_sanitizes_errors():
    def response(request):
        assert request.url.path == '/api/v1/embeddings'
        assert request.headers['Authorization'] == 'Bearer test-only'
        return httpx.Response(200,json={'model':'Qwen/Qwen3-Embedding-4B', 'data':[{'index':1,'embedding':[0,2,0]},{'index':0,'embedding':[1,0,0]}]})
    provider=OpenRouterEmbeddings(EmbeddingConfig(api_key='test-only',dimensions=3),httpx.MockTransport(response))
    try: assert await provider.embed(['first','second']) == [[1,0,0],[0,1,0]]
    finally: await provider.close()
    for payload in [{'data':[{'index':0,'embedding':[1,0]}]}, {'data':[{'index':1,'embedding':[1,0,0]}]}, {'data':[{'index':0,'embedding':[0,0,0]}]}]:
        provider=OpenRouterEmbeddings(EmbeddingConfig(api_key='test-only',dimensions=3),httpx.MockTransport(lambda _:httpx.Response(200,json=payload)))
        try:
            with pytest.raises(BackendError,match='EMBEDDING_INVALID_RESPONSE'): await provider.embed(['first'])
        finally: await provider.close()
    provider=OpenRouterEmbeddings(EmbeddingConfig(api_key='test-only',dimensions=3),httpx.MockTransport(lambda _:httpx.Response(401,text='secret upstream text')))
    try:
        with pytest.raises(BackendError,match='OPENROUTER_AUTH_FAILED') as error: await provider.embed(['first'])
        assert 'secret' not in str(error.value)
    finally: await provider.close()

def test_vectors_reject_non_finite_boolean_and_zero():
    for value in [[float('nan'),0],[float('inf'),1],[True,0],[0,0]]:
        with pytest.raises(BackendError):valid_vector(value,2)

@pytest.fixture
def knowledge_store(postgres_url):
    name='knowledge_test_'+uuid4().hex
    store=KnowledgeStore(postgres_url,name);store.initialize()
    try: yield store
    finally:
        with psycopg.connect(postgres_url) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))

def corpus():
    docs=[{'id':id,'document_version':'a'*64,'title':id,'source_url':'https://example.org/'+id,
           'text':text,'publisher':'Test source','population':'adults','language':'en','source_type':'test_fixture'}
          for id,text in [('sleep','Sleep evidence'),('food','Food evidence')]]
    chunks=[{'id':d['id']+'-1','document_id':d['id'],'text':d['text'],'char_start':0,'char_end':len(d['text']),
             'heading_path':[d['title']],'topics':[d['id']]} for d in docs]
    return docs,chunks

def test_real_pgvector_staging_activation_ranking_and_topic_filter(knowledge_store):
    store=knowledge_store;docs,chunks=corpus();config=EmbeddingConfig(dimensions=3)
    assert store.import_release('r1',config,docs,chunks,[[1,0,0],[0,1,0]])
    assert store.import_release('r1',config,docs,chunks,[[1,0,0],[0,1,0]]) is False
    with pytest.raises(BackendError,match='KNOWLEDGE_NOT_READY'):store.active()
    store.activate('r1');release=store.active();hits=store.search(release,[1,0,0],4)
    assert hits[0]['documentId']=='sleep'
    assert hits[0]['sourceUrl']=='https://example.org/sleep'
    assert hits[0]['text']==docs[0]['text']
    assert hits[0]['similarity']==pytest.approx(1)
    assert store.search(release,[1,0,0],4,'food')[0]['documentId']=='food'
    assert store.search(release,[1,0,0],4,'unknown')==[]
    with pytest.raises(ValueError):store.activate('does-not-exist')
    assert store.active()['id']=='r1'
    store.import_release('r2',config,docs,chunks,[[0,1,0],[1,0,0]]);store.activate('r2')
    assert store.search(release,[1,0,0],4)==[]
    assert store.search(store.active(),[1,0,0],4)[0]['documentId']=='food'

@pytest.mark.asyncio
async def test_service_cache_and_model_mismatch(knowledge_store):
    class FakeEmbedding:
        config=EmbeddingConfig(dimensions=3)
        calls=0
        async def embed(self,texts):
            self.calls+=1;assert texts[0].startswith('Instruct:');return [[1,0,0]]
        async def close(self):pass
    docs,chunks=corpus();knowledge_store.import_release('r1',EmbeddingConfig(dimensions=3),docs,chunks,[[1,0,0],[0,1,0]])
    knowledge_store.activate('r1');fake=FakeEmbedding();service=KnowledgeService(knowledge_store,fake)
    assert (await service.search('睡眠'))['results'][0]['documentId']=='sleep'
    await service.search('睡眠');assert fake.calls==1
    fake.config=EmbeddingConfig(dimensions=4)
    with pytest.raises(BackendError,match='KNOWLEDGE_MODEL_MISMATCH'):await service.search('睡眠')

def test_knowledge_endpoint_session_origin_validation_and_failure(client_factory):
    class FakeService:
        async def search(self,query,top_k,topic):return {'status':'ok','results':[], 'query':query}
        async def close(self):pass
    client=client_factory(knowledge_service=FakeService())
    assert client.post('/api/knowledge/search',json={'query':'sleep'}).status_code==401
    client.get('/api/state')
    assert client.post('/api/knowledge/search',json={'query':'sleep'},headers={'Origin':'https://evil.example'}).status_code==403
    for value in [{'query':''},{'query':'sleep','topK':True},{'query':'sleep','topK':7},{'query':'sleep','model':'another'},{'query':'sleep','topic':None}]:
        assert client.post('/api/knowledge/search',json=value).status_code==400
    result=client.post('/api/knowledge/search',json={'query':'睡眠','topK':4})
    assert result.status_code==200 and result.headers['cache-control']=='no-store'
    assert result.json()['query']=='睡眠'
    unavailable=client_factory();unavailable.get('/api/state')
    assert unavailable.post('/api/knowledge/search',json={'query':'sleep'}).json()['errorCode']=='KNOWLEDGE_NOT_READY'


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['queue', 'retry'])
async def test_embedding_budget_includes_queue_and_retry_backoff(phase):
    calls = []
    provider = OpenRouterEmbeddings(EmbeddingConfig(api_key='test-only', dimensions=3, timeout=.06),
        httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(503))[1]))
    if phase == 'queue':
        provider._slots = asyncio.Semaphore(0)
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(BackendError, match='EMBEDDING_TIMEOUT'):
            await provider.embed(['sleep'])
        assert asyncio.get_running_loop().time() - started < .3
        assert len(calls) == (0 if phase == 'queue' else 1)
    finally:
        await provider.close()


class MemoryKnowledgeStore:
    def active(self):
        from wellio.knowledge import RECIPE
        return {'id':'release-1', 'model':EmbeddingConfig().model, 'dimensions':3, 'recipe':RECIPE}

    def search(self, release, vector, top_k, topic):
        return [{'documentId':'sleep', 'chunkId':'sleep-1'}]


@pytest.mark.asyncio
async def test_concurrent_queries_share_embedding_and_caller_cancellation_is_isolated():
    class SlowEmbedding:
        config = EmbeddingConfig(dimensions=3)
        calls = 0
        def __init__(self):
            self.started, self.release = asyncio.Event(), asyncio.Event()
        async def embed(self, texts):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return [[1,0,0]]
        async def close(self): pass
    fake = SlowEmbedding()
    service = KnowledgeService(MemoryKnowledgeStore(), fake)
    first = asyncio.create_task(service.search('sleep'))
    second = asyncio.create_task(service.search('sleep'))
    try:
        await asyncio.wait_for(fake.started.wait(), 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError): await first
        fake.release.set()
        result = await asyncio.wait_for(second, 1)
        assert result['results'][0]['documentId'] == 'sleep'
        assert fake.calls == 1
        await service.search('sleep')
        assert fake.calls == 1
    finally:
        await service.close()
    assert not service._embeddings


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['release', 'embedding', 'search'])
async def test_service_total_budget_covers_each_stage_and_close_drains_shared_work(phase):
    class SlowStore(MemoryKnowledgeStore):
        def active(self):
            if phase == 'release': time.sleep(.2)
            return super().active()
        def search(self, *args):
            if phase == 'search': time.sleep(.2)
            return super().search(*args)
    class SlowEmbedding:
        config = EmbeddingConfig(dimensions=3)
        async def embed(self, texts):
            if phase == 'embedding': await asyncio.sleep(.2)
            return [[1,0,0]]
        async def close(self): pass
    service = KnowledgeService(SlowStore(), SlowEmbedding(), timeout_seconds=.06)
    started = asyncio.get_running_loop().time()
    try:
        with pytest.raises(BackendError, match='KNOWLEDGE_TIMEOUT'):
            await service.search('sleep')
        assert asyncio.get_running_loop().time() - started < .18
    finally:
        await service.close()
    assert not service._embeddings
