"""Repair ticket index metadata from canonical SQL rows, without re-embedding."""
import json
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text

from configs.settings import get_settings
from api.access_control import ticket_repository, repository_roles
from retrieval.bm25_store import BM25Store


def main():
    settings = get_settings()
    with create_engine(settings.postgres_url).connect() as conn:
        rows = [dict(r._mapping) for r in conn.execute(text('SELECT ticket_id,category FROM tickets'))]
    canonical = {}
    for row in rows:
        repository = ticket_repository(row['category'])
        canonical[str(uuid.uuid5(uuid.NAMESPACE_DNS, row['ticket_id']))] = {
            **row, 'repository':repository, 'department':repository,
            'access_roles':repository_roles(repository)}
    points = []
    offset = None
    with httpx.Client(base_url=f'http://{settings.qdrant_host}:{settings.qdrant_port}', timeout=60, trust_env=False) as client:
        url = f'/collections/{settings.qdrant_collection}/points'
        while True:
            body = {'limit':500,'with_payload':True,'with_vector':False,
                    'filter':{'must':[{'key':'doc_type','match':{'value':'Ticket'}}]}}
            if offset is not None:
                body['offset'] = offset
            response = client.post(url+'/scroll', json=body)
            response.raise_for_status()
            batch = response.json()['result']
            points.extend(batch['points'])
            offset = batch.get('next_page_offset')
            if offset is None:
                break
        if any(p['payload'].get('chunk_id') not in canonical for p in points):
            raise RuntimeError('Unmatched indexed ticket; repair stopped before updates')
        backup = Path(__file__).resolve().parents[3] / '.local' / f'ticket-permissions-{time.time_ns()}'
        backup.mkdir(parents=True)
        fields = ('ticket_id','category','repository','department','access_roles')
        (backup/'vector-metadata.json').write_text(json.dumps([
            {'id':p['id'], 'payload':{key:p['payload'].get(key) for key in fields}} for p in points]), encoding='utf-8')
        store = BM25Store()
        if not store._redis_enabled:
            raise RuntimeError('BM25 persistence unavailable; repair stopped')
        saved = store._redis.get(store.REDIS_KEY)
        if saved:
            (backup/'bm25.bin').write_bytes(saved)
        for start in range(0, len(points), 100):
            operations = [{'set_payload':{'payload':canonical[p['payload']['chunk_id']], 'points':[p['id']]}}
                          for p in points[start:start+100]]
            response = client.post(url+'/batch?wait=true', json={'operations':operations})
            response.raise_for_status()
        repaired_bm25 = 0
        for payload in store._corpus_meta:
            if payload.get('doc_type') == 'Ticket' and payload.get('chunk_id') in canonical:
                payload.update(canonical[payload['chunk_id']])
                repaired_bm25 += 1
        store._persist_to_redis()
    print(f'Repaired {len(points)} vector ticket payloads and {repaired_bm25} BM25 ticket payloads; local backups saved.')


if __name__ == '__main__':
    main()
