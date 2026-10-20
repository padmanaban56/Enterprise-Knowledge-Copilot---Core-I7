import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from api.access_control import ticket_visible, TICKET_VISIBLE
from api.ticket_intelligence import TicketIntelligence


class TicketAccessTests(unittest.IsolatedAsyncioTestCase):
    async def test_anonymous_cannot_read_ticket_routes(self):
        from api import main
        main.app.dependency_overrides[main.get_current_user] = lambda: SimpleNamespace(
            user_id='anonymous', role='employee', access_roles=['EMPLOYEE'])
        try:
            with patch.object(main, 'ticket_intelligence', MagicMock()) as service:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    for path in ['/tickets/search?q=payroll', '/tickets/recent', '/tickets/categories']:
                        self.assertEqual((await client.get(path)).status_code, 401)
                self.assertEqual(service.mock_calls, [])
        finally:
            main.app.dependency_overrides.clear()

    async def test_routes_use_identity_roles_and_ignore_requested_roles(self):
        from api import main
        main.app.dependency_overrides[main.get_current_user] = lambda: SimpleNamespace(
            user_id='hr-user', role='employee', access_roles=['HR'])
        service = MagicMock()
        service.search_tickets.return_value = {'tickets': [], 'known_issue': None}
        service.get_recent_tickets.return_value = []
        service.get_category_breakdown.return_value = []
        service.get_total_count.return_value = 0
        try:
            with patch.object(main, 'ticket_intelligence', service):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
                    for path in ['/tickets/search?q=network&category=IT&roles=ADMIN',
                                 '/tickets/recent?category=IT&roles=ADMIN', '/tickets/categories?roles=ADMIN']:
                        self.assertEqual((await client.get(path)).status_code, 200)
                for method in [service.search_tickets, service.get_recent_tickets,
                               service.get_category_breakdown, service.get_total_count]:
                    method.assert_called_once()
                    self.assertEqual(method.call_args.kwargs['user_roles'], ['EMPLOYEE', 'HR'])
        finally:
            main.app.dependency_overrides.clear()


class TicketQueryTests(unittest.TestCase):
    def test_all_ticket_queries_filter_before_limits_and_aggregation(self):
        service = TicketIntelligence.__new__(TicketIntelligence)
        service.engine = MagicMock()
        conn = service.engine.connect.return_value.__enter__.return_value
        conn.execute.return_value = []
        service.search_tickets('network outage', limit=2, user_roles=['HR'])
        service.get_recent_tickets(2, user_roles=['HR'])
        service.get_recent_tickets(2, 'IT', user_roles=['HR'])
        service.get_category_breakdown(user_roles=['HR'])
        service.get_resolution_stats('IT', user_roles=['HR'])
        self.assertEqual(len(conn.execute.call_args_list), 6)
        for call in conn.execute.call_args_list:
            sql, params = call.args
            self.assertIn(TICKET_VISIBLE, str(sql))
            self.assertEqual(params['repositories'], ['HR'])
            self.assertFalse(params['is_admin'])
            if 'LIMIT' in str(sql):
                self.assertLess(str(sql).index(TICKET_VISIBLE), str(sql).index('LIMIT'))
        conn.execute.reset_mock()
        conn.execute.return_value = MagicMock()
        conn.execute.return_value.scalar.return_value = 0
        service.get_total_count()
        self.assertEqual(conn.execute.call_args.args[1]['repositories'], [])

    def test_ticket_department_mapping(self):
        for category, roles in [('HR', ['HR']), ('Finance', ['FINANCE']),
                                (' Engineering ', ['ENGINEERING']), ('Projects', ['PROJECTS']),
                                ('IT', ['IT']), ('Operations', ['IT']), ('Security', ['IT'])]:
            self.assertTrue(ticket_visible({'category': category}, roles))
            self.assertTrue(ticket_visible({'category': category}, ['ADMIN']))
            self.assertFalse(ticket_visible({'category': category}, ['EMPLOYEE']))
        self.assertFalse(ticket_visible({'category': 'Finance'}, ['HR']))
        self.assertFalse(ticket_visible({'category': 'Security'}, ['ENGINEERING', 'PROJECTS']))


if __name__ == '__main__':
    unittest.main()
