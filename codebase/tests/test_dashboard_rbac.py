import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

from api.access_control import signed_in_roles
from api.analytics_service import AnalyticsService


class DashboardAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_uses_authenticated_identity_even_if_another_user_is_requested(self):
        from api import main
        user = SimpleNamespace(user_id='current-user', role='admin', access_roles=['IT_ADMIN'], email='own@example.test')
        service = MagicMock()
        service.get_dashboard_metrics.return_value = {'scope': 'user', 'total_queries': 2}
        main.app.dependency_overrides[main.get_current_user] = lambda: user
        try:
            with patch.object(main, 'analytics_service', service):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    response = await client.get('/analytics/dashboard?user_id=someone-else&scope=all')
                    self.assertEqual(response.status_code, 200)
            service.get_dashboard_metrics.assert_called_once_with(7, user_id='current-user',
                user_roles=['ADMIN', 'IT_ADMIN'], user_email='own@example.test')
        finally:
            main.app.dependency_overrides.clear()

    async def test_anonymous_dashboard_and_repository_access_is_denied(self):
        from api import main
        anonymous = SimpleNamespace(user_id='anonymous', role='employee', access_roles=['EMPLOYEE'])
        main.app.dependency_overrides[main.get_current_user] = lambda: anonymous
        try:
            with patch.object(main, 'analytics_service', MagicMock()) as service:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    for path in ['/analytics/dashboard', '/repositories', '/repositories/Finance/documents', '/documents']:
                        self.assertEqual((await client.get(path)).status_code, 401, path)
                    for days in [0, 366]:
                        self.assertEqual((await client.get(f'/analytics/dashboard?days={days}')).status_code, 422)
                service.get_dashboard_metrics.assert_not_called()
        finally:
            main.app.dependency_overrides.clear()

    async def test_global_evaluation_is_not_available_to_regular_users(self):
        from api import main
        user = SimpleNamespace(user_id='current-user', role='employee', access_roles=['EMPLOYEE'])
        main.app.dependency_overrides[main.get_current_user] = lambda: user
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                for path in ['/analytics/evaluation', '/analytics/evaluation/summary']:
                    self.assertEqual((await client.get(path)).status_code, 403)
        finally:
            main.app.dependency_overrides.clear()

    async def test_unsupported_query_logs_final_confidence_once_under_its_owner(self):
        from api import main
        response = main.ChatResponse(answer='The provided context does not contain this information. [Source 1]',
            citations=[{'doc_id': 'irrelevant'}], confidence=.9, confidence_label='HIGH',
            confidence_breakdown={}, chunks_used=3, low_confidence=False, latency_ms=1,
            session_id='s', repositories_searched=[], expanded_queries=[], sub_queries=[],
            intent='SEARCH', hyde_used=False, retrieval_transparency=[], pipeline_trace=[], pipeline_stats={})
        analytics = MagicMock()
        with patch.object(main, '_run_chat', AsyncMock(return_value=response)), \
             patch.object(main, 'analytics_service', analytics):
            await main.chat(main.ChatRequest(query='Synthetic policy question'), SimpleNamespace(user_id='owner'))
        analytics.log_query.assert_called_once()
        recorded = analytics.log_query.call_args.kwargs
        self.assertEqual(recorded['user_id'], 'owner')
        self.assertEqual(recorded['confidence'], 0)
        self.assertEqual(recorded['chunks_retrieved'], 0)

    async def test_short_circuit_queries_are_included_in_personal_metrics(self):
        from api import main
        analytics = MagicMock()
        with patch.object(main, 'chat_history_service', None), patch.object(main, 'analytics_service', analytics):
            await main.chat(main.ChatRequest(query='what is the time in chennai?'), SimpleNamespace(user_id='owner'))
        analytics.log_query.assert_called_once()
        self.assertEqual(analytics.log_query.call_args.kwargs['intent'], 'OUT_OF_SCOPE')
        self.assertEqual(analytics.log_query.call_args.kwargs['user_id'], 'owner')


class DashboardServiceTests(unittest.TestCase):
    def test_missing_identity_cannot_fall_back_to_global_metrics(self):
        service = AnalyticsService.__new__(AnalyticsService)
        service.engine = MagicMock()
        for user_id in [None, '', 'anonymous']:
            with self.assertRaises(ValueError):
                service.get_dashboard_metrics(user_id=user_id)
        service.engine.connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
