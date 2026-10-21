import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from fastapi import HTTPException

from api.access_control import payload_visible, allowed_repositories, access_parameters
from api.auth import AuthService

DOC_ID = '12345678-1234-4234-8234-123456789abc'


class FileGrantTests(unittest.TestCase):
    def test_file_grant_only_unlocks_one_document_without_department_or_ticket_access(self):
        roles = ['HR', 'FILE:' + DOC_ID]
        self.assertTrue(payload_visible({'doc_id':DOC_ID, 'repository':'Finance', 'access_roles':['FINANCE']}, roles))
        self.assertFalse(payload_visible({'doc_id':'other', 'repository':'Finance', 'access_roles':['FINANCE']}, roles))
        self.assertEqual(allowed_repositories(roles), ['HR'])
        self.assertEqual(access_parameters(roles)['granted_ids'], [DOC_ID])

    def test_invalid_or_missing_file_is_rejected_before_creating_request(self):
        service = AuthService.__new__(AuthService)
        service.engine = MagicMock()
        conn = service.engine.begin.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = None
        for name, expected in [('FILE:Finance/name.pdf',400), ('FILE:' + DOC_ID,404)]:
            with self.assertRaises(HTTPException) as error:
                service.create_access_request('user',name,'Need file',resource_type='file')
            self.assertEqual(error.exception.status_code, expected)
        self.assertFalse(any('INSERT' in str(call.args[0]) for call in conn.execute.call_args_list))


class FileRequestRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_file_grant_is_honored_by_retrieval_without_unlocking_other_files(self):
        from retrieval.hybrid_engine import HybridRetrievalEngine
        from retrieval.query_understanding import QueryContext
        engine = HybridRetrievalEngine.__new__(HybridRetrievalEngine)
        engine.vector_store = MagicMock()
        engine.vector_store.get_chunks_by_doc.side_effect = lambda did, **kwargs: [
            {'doc_id':did,'chunk_id':did+'-1','content':'Invoice approval policy',
             'source_file':'invoice.pdf','repository':'Finance','access_roles':['FINANCE']}]
        ctx = QueryContext(intent='SUMMARIZE',active_document_ids=[DOC_ID,'other'])
        result = await engine.retrieve(ctx,rbac_roles=['HR','FILE:'+DOC_ID])
        self.assertTrue(result.chunks)
        self.assertEqual({c.doc_id for c in result.chunks},{DOC_ID})

    async def test_file_request_is_not_validated_as_a_role(self):
        from api import main
        main.app.dependency_overrides[main.get_current_user] = lambda: SimpleNamespace(user_id='hr',role='employee',access_roles=['HR'])
        service = MagicMock()
        service.create_access_request.return_value = {'resource_type':'file','resource_name':'FILE:' + DOC_ID,'status':'pending'}
        try:
            with patch.object(main,'get_auth_service',return_value=service):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
                    response = await client.post('/access-requests',json={'resource_type':'file','resource_name':'FILE:' + DOC_ID,'justification':'Need this invoice policy'})
                    self.assertEqual(response.status_code,200)
                    service.create_access_request.assert_called_once_with(user_id='hr', resource_name='FILE:' + DOC_ID,
                        justification='Need this invoice policy',resource_type='file')
                    self.assertEqual((await client.post('/access-requests',json={'resource_name':'INVALID','justification':'Need access'})).status_code,400)
        finally:
            main.app.dependency_overrides.clear()

    async def test_catalog_requires_authentication(self):
        from api import main
        main.app.dependency_overrides[main.get_current_user] = lambda: SimpleNamespace(user_id='anonymous')
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
                self.assertEqual((await client.get('/access-requests/catalog')).status_code,401)
        finally:
            main.app.dependency_overrides.clear()
