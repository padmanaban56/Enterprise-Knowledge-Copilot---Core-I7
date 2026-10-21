import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from api.analytics_service import ChatHistoryService


class HistoryQueryTests(unittest.TestCase):
    def test_counts_exclude_events_and_sessions_require_query_and_answer_before_limit(self):
        service = ChatHistoryService.__new__(ChatHistoryService)
        service.engine = MagicMock()
        conn = service.engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = []
        service.list_sessions('owner')
        sql, params = conn.execute.call_args.args
        sql = str(sql)
        self.assertIn("cm.role IN ('user','assistant')",sql)
        self.assertIn("cm.role='user'",sql)
        self.assertIn("cm.role='assistant'",sql)
        self.assertLess(sql.index("cm.role='assistant'"),sql.index('LIMIT'))
        self.assertEqual(params['uid'],'owner')

    def test_rename_is_owner_scoped_and_only_for_completed_conversations(self):
        service = ChatHistoryService.__new__(ChatHistoryService)
        service.engine = MagicMock()
        conn = service.engine.begin.return_value.__enter__.return_value
        conn.execute.return_value.fetchone.return_value = None
        self.assertIsNone(service.rename_session('session','owner','New name'))
        sql, params = conn.execute.call_args.args
        self.assertIn('user_id=:uid',str(sql))
        self.assertIn("cm.role='assistant'",str(sql))
        self.assertEqual(params,{'sid':'session','uid':'owner','title':'New name'})


class RenameRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_rename_validation_and_foreign_session_denial(self):
        from api import main
        user = SimpleNamespace(user_id='owner',role='employee',access_roles=['HR'])
        main.app.dependency_overrides[main.get_current_user] = lambda: user
        service = MagicMock()
        sid = '12345678-1234-4234-8234-123456789abc'
        try:
            with patch.object(main,'chat_history_service',service):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
                    for title,status in [('',422),('   ',400),('x'*256,422)]:
                        self.assertEqual((await client.patch('/chat/sessions/'+sid,json={'title':title})).status_code,status)
                    service.rename_session.assert_not_called()
                    service.rename_session.return_value = {'session_id':sid,'title':'Named chat'}
                    response = await client.patch('/chat/sessions/'+sid,json={'title':'  Named chat  '})
                    self.assertEqual(response.status_code,200)
                    service.rename_session.assert_called_once_with(sid,'owner','Named chat')
                    service.rename_session.return_value = None
                    self.assertEqual((await client.patch('/chat/sessions/'+sid,json={'title':'Foreign'})).status_code,404)
        finally:
            main.app.dependency_overrides.clear()
