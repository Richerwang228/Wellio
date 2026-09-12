"""Explicit offline preparation/embedding/import/release commands. Never reads test DBs implicitly."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from dotenv import load_dotenv
from .knowledge import (EmbeddingConfig, KnowledgeStore, OpenRouterEmbeddings, RECIPE,
                        QUERY_INSTRUCTION, fingerprint, valid_vector)
from .errors import BackendError

BACKEND = Path(__file__).resolve().parents[1]


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
    temporary.replace(path)


def prepare(root, selection_path):
    import hashlib
    selection = json.loads(selection_path.read_text())
    docs = {d['id']:d for d in map(json.loads, (root/'documents.jsonl').read_text().splitlines())}
    chunks, texts, skipped = [], [], []
    for c in map(json.loads, (root/'chunks.jsonl').read_text().splitlines()):
        headings = selection['documents'].get(c['document_id'])
        if not headings or not c['heading_path'] or c['heading_path'][-1] not in headings:
            continue
        d = docs[c['document_id']]
        if c['quality_flags'] or len(c['text']) > 8000:
            skipped.append(c['id'])
            continue
        if (c['text'] != d['text'][c['char_start']:c['char_end']]
            or c['document_version'] != d['document_version']
            or hashlib.sha256(c['text'].encode()).hexdigest() != c['text_sha256']):
            raise ValueError('CORPUS_INTEGRITY_ERROR')
        text = d['title'] + '\n' + ' > '.join(c['heading_path'][1:]) + '\n\n' + c['text']
        if len(text) > 12000:
            raise ValueError('EMBEDDING_INPUT_TOO_LONG')
        chunks.append(c); texts.append(text)
    included = {c['document_id'] for c in chunks}
    if included != set(selection['documents']):
        raise ValueError('SELECTION_HAS_MISSING_DOCUMENTS')
    documents = [docs[i] for i in sorted(included)]
    for d in documents:
        if hashlib.sha256(d['text'].encode()).hexdigest() != d['document_version']:
            raise ValueError('CORPUS_DOCUMENT_HASH_MISMATCH')
    config = EmbeddingConfig()
    identity = {'model':config.model, 'dimensions':config.dimensions, 'recipe':RECIPE,
                'selection':selection, 'documents':documents, 'chunks':chunks, 'inputs':texts}
    return {**identity, 'releaseId':'demo-' + fingerprint(identity)[:24], 'skippedChunks':skipped}


async def embed_release(bundle, destination):
    embedder = OpenRouterEmbeddings()
    if not embedder.config.api_key:
        await embedder.close()
        raise BackendError('OPENROUTER_API_KEY_REQUIRED', 503)
    cache_path = destination/'vectors.json'
    cached = json.loads(cache_path.read_text()) if cache_path.exists() else {'releaseId':bundle['releaseId'], 'vectors':{}}
    if cached['releaseId'] != bundle['releaseId']:
        raise ValueError('CACHE_RELEASE_MISMATCH')
    try:
        pending = []
        for c, text in zip(bundle['chunks'],bundle['inputs']):
            if c['id'] in cached['vectors']:
                valid_vector(cached['vectors'][c['id']], bundle['dimensions'])
            else:
                pending.append((c['id'],text))
        for start in range(0,len(pending),8):
            batch = pending[start:start+8]
            vectors = await embedder.embed([text for _,text in batch])
            cached['vectors'].update({id:v for (id,_),v in zip(batch,vectors)})
            dump(cache_path,cached)
            print(json.dumps({'embedded':len(cached['vectors']),'total':len(bundle['chunks'])}),flush=True)
    finally:
        await embedder.close()
    return cached


DEMO_QUERIES = [
    ('昨晚只睡了四小时，今天训练会受什么影响？', {'sleep-loss-performance'}),
    ('今天只剩十五分钟，怎么精简力量训练？', {'time-efficient-training'}),
    ('增肌训练后需要注意蛋白质吗？', {'bda-sport-exercise-nutrition'}),
    ('晚上喝咖啡和吃太晚会影响睡眠吗？', {'nhlbi-sleep-habits'}),
]


async def evaluate(store, bundle):
    import time
    embedder=OpenRouterEmbeddings()
    release={'id':bundle['releaseId'],'dimensions':bundle['dimensions']}
    results=[]
    try:
        for query,expected in DEMO_QUERIES:
            started=time.perf_counter()
            vector=(await embedder.embed([QUERY_INSTRUCTION+query]))[0]
            hits=await asyncio.to_thread(store.search,release,vector,4,None,False)
            results.append({'query':query,'expectedDocumentIds':sorted(expected),
                'retrievedDocumentIds':[h['documentId'] for h in hits],
                'pass':bool(expected & {h['documentId'] for h in hits}),
                'elapsedMs':round((time.perf_counter()-started)*1000)})
    finally:
        await embedder.close()
    return {'releaseId':bundle['releaseId'],'model':bundle['model'],
            'passed':all(r['pass'] for r in results),'queries':results,
            'scope':'Four retrieval smoke cases, not a medical correctness or Agent integration evaluation.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['prepare','embed','import','evaluate','activate'])
    parser.add_argument('--root',type=Path,default=BACKEND/'.data/knowledge/nutrition-v1')
    parser.add_argument('--selection',type=Path,default=BACKEND/'knowledge/demo_selection.json')
    args=parser.parse_args()
    # Only the explicit offline CLI loads this project env. Tests never call it.
    load_dotenv(BACKEND/'.env',override=False)
    bundle=prepare(args.root,args.selection)
    destination=args.root/'releases'/bundle['releaseId']
    dump(destination/'bundle.json',bundle)
    if args.command=='embed':
        asyncio.run(embed_release(bundle,destination))
    elif args.command in ('import','evaluate','activate'):
        store=KnowledgeStore(os.environ.get('DATABASE_URL'))
        if args.command=='import':
            cached=json.loads((destination/'vectors.json').read_text())
            if cached['releaseId']!=bundle['releaseId']:
                raise ValueError('CACHE_RELEASE_MISMATCH')
            vectors=[cached['vectors'][c['id']] for c in bundle['chunks']]
            store.initialize()
            store.import_release(bundle['releaseId'],EmbeddingConfig(),bundle['documents'],bundle['chunks'],vectors)
        elif args.command=='evaluate':
            result=asyncio.run(evaluate(store,bundle));dump(destination/'evaluation.json',result)
            print(json.dumps(result,ensure_ascii=False))
            if not result['passed']:
                raise SystemExit(1)
        else:
            report=json.loads((destination/'evaluation.json').read_text())
            if report['releaseId']!=bundle['releaseId'] or report.get('model')!=bundle['model'] or not report['passed']:
                raise ValueError('RETRIEVAL_EVALUATION_REQUIRED')
            store.activate(bundle['releaseId'])
    print(json.dumps({'command':args.command,'releaseId':bundle['releaseId'],'documents':len(bundle['documents']),
                      'chunks':len(bundle['chunks']),'directory':str(destination)},ensure_ascii=False))


if __name__=='__main__':
    try:
        main()
    except BackendError as error:
        raise SystemExit(error.code) from None
