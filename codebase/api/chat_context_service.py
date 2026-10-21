"""Transactional context changes recorded in the conversation timeline."""
import json
from typing import List, Literal, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, text
from api.auth import CurrentUser, get_current_user
from configs.settings import get_settings

router = APIRouter()


class ContextUpdate(BaseModel):
    request_id: UUID
    provider: Optional[Literal['ollama', 'openrouter']] = None
    model: Optional[str] = Field(default=None, min_length=1, max_length=200)
    document_ids: List[UUID] = Field(default_factory=list, max_length=1000)
    bundle_ids: List[UUID] = Field(default_factory=list, max_length=1000)


class ChatContextService:
    def __init__(self, db_url=None):
        self.engine = create_engine(db_url or get_settings().postgres_url)

    def update(self, session_id, user_id, change, roles=None, is_admin=False):
        if bool(change.provider) != bool(change.model):
            raise HTTPException(400, 'Provide both model and provider, or clear both')
        sid, rid = str(session_id), str(change.request_id)
        with self.engine.begin() as conn:
            conn.execute(text('INSERT INTO chat_sessions (session_id,user_id,title) VALUES (:sid,:uid,\'New Chat\') ON CONFLICT (session_id) DO NOTHING'), {'sid':sid,'uid':user_id})
            session = conn.execute(text('SELECT user_id,context_state FROM chat_sessions WHERE session_id=:sid FOR UPDATE'), {'sid':sid}).fetchone()
            if not session or str(session.user_id) != str(user_id):
                raise HTTPException(404, 'Session not found')
            replay = conn.execute(text('SELECT session_id,result FROM chat_context_updates WHERE request_id=:rid'), {'rid':rid}).fetchone()
            if replay:
                if str(replay.session_id) != sid:
                    raise HTTPException(409, 'Context update ID belongs to another session')
                return replay.result
            manual_ids = list(dict.fromkeys(map(str, change.document_ids)))
            bundle_ids = list(dict.fromkeys(map(str, change.bundle_ids)))
            bundles = []
            if bundle_ids:
                rows = conn.execute(text('SELECT bundle_id::text,name,document_ids FROM bundles WHERE bundle_id::text=ANY(:ids) AND user_id=:uid AND deleted_at IS NULL'), {'ids':bundle_ids,'uid':user_id}).fetchall()
                by_id = {r.bundle_id:dict(r._mapping) for r in rows}
                if set(by_id) != set(bundle_ids):
                    raise HTTPException(404, 'A selected bundle is unavailable')
                bundles = [by_id[bid] for bid in bundle_ids]
            effective_ids = list(dict.fromkeys(manual_ids + [doc for bundle in bundles for doc in bundle['document_ids']]))
            documents = []
            if effective_ids:
                from api.access_control import DOCUMENT_VISIBLE, access_parameters
                visibility = access_parameters(list(roles or []) + (['ADMIN'] if is_admin else []))
                rows = conn.execute(text(f'SELECT d.doc_id::text,d.source_file,d.title,d.access_roles,COALESCE(r.name,d.department) AS repository FROM documents d LEFT JOIN repositories r ON r.repository_id=d.repository_id WHERE d.doc_id::text=ANY(:ids) AND {DOCUMENT_VISIBLE}'), {'ids':effective_ids, **visibility}).fetchall()
                user_roles = {role.upper() for role in (roles or ['EMPLOYEE'])}
                by_id = {r.doc_id:{'doc_id':r.doc_id,'filename':r.source_file or r.title,'repository':r.repository}
                         for r in rows}
                if set(by_id) != set(effective_ids):
                    raise HTTPException(400, 'A selected document is unavailable')
                documents = [by_id[did] for did in effective_ids]
            state = {'provider':change.provider, 'model':change.model,
                     'document_ids':manual_ids, 'bundle_ids':bundle_ids,
                     'documents':[d for d in documents if d['doc_id'] in manual_ids],
                     'effective_documents':documents,
                     'bundles':[{'bundle_id':b['bundle_id'],'name':b['name']} for b in bundles]}
            previous = session.context_state or {}
            events = []
            def event(kind, content, **details):
                events.append((kind,content,details))
            if session.context_state is None and state['model'] is None:
                event('model_removed', 'Model selection cleared; using the local default.')
            if (previous.get('provider'),previous.get('model')) != (state['provider'],state['model']):
                if previous.get('model'):
                    event('model_removed', f"Model removed: {previous['model']}", provider=previous.get('provider'),model=previous['model'])
                if state['model']:
                    event('model_selected', f"Model selected: {state['model']}", provider=state['provider'],model=state['model'])
            for field, key, noun in (('bundles','bundle_id','Bundle'), ('effective_documents','doc_id','Document')):
                old = {item[key]:item for item in previous.get(field,[])}
                new = {item[key]:item for item in state[field]}
                for action, items in (('removed',[item for id,item in old.items() if id not in new]),
                                      ('added',[item for id,item in new.items() if id not in old])):
                    for item in items:
                        label = item.get('filename') or item.get('name')
                        event(f'{noun.lower()}_{action}', f'{noun} {action}: {label}', **item)
            # Manual selections can change while a bundle still includes the
            # same document. Record that action without claiming scope changed.
            old_manual = set(previous.get('document_ids', []))
            new_manual = set(manual_ids)
            old_effective = {d['doc_id'] for d in previous.get('effective_documents', [])}
            new_effective = {d['doc_id'] for d in documents}
            by_id = {d['doc_id']:d for d in documents}
            for did in old_manual - new_manual:
                if did in new_effective:
                    event('document_removed', f"Document selection removed: {by_id[did]['filename']} (still included by a bundle)", **by_id[did])
            for did in new_manual - old_manual:
                if did in old_effective:
                    event('document_added', f"Document selected: {by_id[did]['filename']} (already included by a bundle)", **by_id[did])
            messages = []
            for kind, content, details in events:
                metadata = {'kind':'context_event','event_type':kind,**details}
                row = conn.execute(text("INSERT INTO chat_messages (session_id,role,content,retrieval_meta) VALUES (:sid,'event',:content,CAST(:meta AS jsonb)) RETURNING message_id,role,content,retrieval_meta,created_at,timeline_seq"),
                                   {'sid':sid,'content':content,'meta':json.dumps(metadata)}).fetchone()
                messages.append(dict(row._mapping))
            conn.execute(text('UPDATE chat_sessions SET context_state=CAST(:state AS jsonb), active_documents=:docs, updated_at=CASE WHEN :changed THEN NOW() ELSE updated_at END WHERE session_id=:sid'),
                         {'sid':sid,'state':json.dumps(state),'docs':effective_ids,'changed':bool(events)})
            result = json.loads(json.dumps({'messages':messages,'context_state':state},default=str))
            conn.execute(text('INSERT INTO chat_context_updates (request_id,session_id,result) VALUES (:rid,:sid,CAST(:result AS jsonb))'),
                         {'rid':rid,'sid':sid,'result':json.dumps(result)})
            return result


context_service = ChatContextService()


@router.post('/chat/sessions/{session_id}/context')
async def update_chat_context(session_id: UUID, change: ContextUpdate, user: CurrentUser = Depends(get_current_user)):
    if user.user_id == 'anonymous':
        raise HTTPException(401, 'Sign in to save chat context')
    return context_service.update(session_id,user.user_id,change,roles=user.access_roles,
                                  is_admin=user.role.upper()=='ADMIN')
