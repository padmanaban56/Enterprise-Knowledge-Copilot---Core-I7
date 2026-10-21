import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from sqlalchemy import text

from api.access_control import payload_visible
from api.ticket_intelligence import TicketIntelligence
from retrieval.hybrid_engine import TicketRetriever
from retrieval.query_understanding import QueryContext


class TicketPagingTests(unittest.TestCase):
    def test_exact_subject_or_id_does_not_mix_in_keyword_matches(self):
        service = TicketIntelligence.__new__(TicketIntelligence)
        service.engine = MagicMock()
        conn = service.engine.connect.return_value.__enter__.return_value
        count = MagicMock()
        count.scalar.return_value = 1
        conn.execute.side_effect = [count,[SimpleNamespace(_mapping={'ticket_id':'TCK000529'})]]
        result = service.get_page('Database migration reported by event processing service',user_roles=['ENGINEERING'])
        self.assertEqual(result['match_type'],'exact')
        self.assertEqual(result['total'],1)
        self.assertEqual(result['tickets'],[{'ticket_id':'TCK000529'}])
        self.assertEqual(conn.execute.call_count,2)
        sql = str(conn.execute.call_args.args[0])
        self.assertIn('LOWER(ticket_id)=LOWER(:q)',sql)
        self.assertIn('LOWER(TRIM(subject))=LOWER(:q)',sql)
        self.assertIn(':repositories',sql)

    def test_count_and_rows_share_permission_filters_and_stable_order(self):
        service = TicketIntelligence.__new__(TicketIntelligence)
        service.engine = MagicMock()
        conn = service.engine.connect.return_value.__enter__.return_value
        count = MagicMock()
        count.scalar.return_value = 43
        exact = MagicMock()
        exact.scalar.return_value = 0
        conn.execute.side_effect = [exact, count, []]
        page = service.get_page('network', 'IT', 'High', 9, 20, user_roles=['IT'])
        self.assertEqual((page['page'], page['total_pages'], page['total']), (3,3,43))
        calls = conn.execute.call_args_list
        self.assertEqual(calls[2].args[1]['offset'],40)
        for call in calls:
            self.assertIn(':repositories',str(call.args[0]))
            self.assertEqual(call.args[1]['repositories'],['IT'])
            self.assertEqual(call.args[1]['category'],'IT')
        self.assertIn('ticket_id LIMIT :limit OFFSET :offset',str(calls[2].args[0]))

    def test_missing_or_conflicting_ticket_metadata_cannot_masquerade_as_it(self):
        self.assertFalse(payload_visible({'doc_type':'Ticket','repository':'IT','access_roles':['IT']},['IT']))
        self.assertFalse(payload_visible({'doc_type':'Ticket','repository':'IT','category':'Engineering','access_roles':['IT']},['IT']))
        self.assertTrue(payload_visible({'doc_type':'Ticket','category':'Engineering'},['ENGINEERING']))
        self.assertTrue(payload_visible({'doc_type':'Ticket','category':'Security'},['IT']))

    def test_denied_exact_id_does_not_fall_back_to_another_row(self):
        retriever = TicketRetriever.__new__(TicketRetriever)
        retriever.search = MagicMock(return_value=[])
        retriever._search_with_filters = MagicMock()
        vector = MagicMock()
        result = retriever.dual_path_search('ticket ENG-1234',ticket_ids=['ENG-1234'],vector_store=vector,rbac_roles=['IT'])
        self.assertEqual(result['candidates'],[])
        retriever._search_with_filters.assert_not_called()
        vector.search.assert_not_called()

    def test_sql_ticket_retrieval_filters_exact_and_fts_before_limit(self):
        retriever = TicketRetriever.__new__(TicketRetriever)
        retriever.engine = MagicMock()
        retriever._text = text
        conn = retriever.engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = []
        retriever.search('network',rbac_roles=['IT'])
        retriever.search('ENG-1234',ticket_ids=['ENG-1234'],rbac_roles=['IT'])
        retriever._search_with_filters('network',{'category':'Engineering'},rbac_roles=['IT'])
        for call in conn.execute.call_args_list:
            sql, params = call.args
            self.assertEqual(params['repositories'],['IT'])
            self.assertLess(str(sql).index(':repositories'),str(sql).index('LIMIT'))


class TicketChatAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_denied_ticket_chat_returns_no_other_ticket_and_never_calls_generation(self):
        from api import main
        user = SimpleNamespace(user_id='it-user',role='employee',access_roles=['IT'])
        ctx = QueryContext(intent='TICKET_LOOKUP', ticket_ids=['ENG-1234'])
        retriever = MagicMock()
        retriever.search.return_value = []
        llm = MagicMock()
        with patch.object(main,'query_understanding') as understanding, patch.object(main,'repo_service',None), \
             patch.object(main,'model_service') as models, patch.object(main,'ticket_retriever',retriever), \
             patch.object(main,'chat_history_service',None), patch.object(main,'llm_service',llm):
            understanding.process.return_value = ctx
            models.validate = AsyncMock()
            result = await main._run_chat(main.ChatRequest(query='status of ENG-1234'),user)
        self.assertEqual(result.citations,[])
        self.assertEqual(result.ticket_candidates,[])
        self.assertIn('accessible ticket', result.answer)
        llm.generate_from_context.assert_not_called()

    async def test_page_endpoint_denies_anonymous_and_invalid_page_sizes(self):
        from api import main
        main.app.dependency_overrides[main.get_current_user] = lambda: SimpleNamespace(user_id='anonymous')
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
                self.assertEqual((await client.get('/tickets')).status_code,401)
                for path in ['/tickets?page=0','/tickets?page_size=0','/tickets?page_size=101']:
                    self.assertEqual((await client.get(path)).status_code,422)
        finally:
            main.app.dependency_overrides.clear()
