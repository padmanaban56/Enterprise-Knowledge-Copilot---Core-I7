import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from retrieval.query_understanding import QueryUnderstanding


class TicketAnalyticsRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def classify_as_ticket_analytics(self, query):
        pipeline = QueryUnderstanding()
        ctx = pipeline.process(query)
        response = MagicMock()
        response.json.return_value = {"response": "TICKET_ANALYTICS"}
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        manager = MagicMock()
        manager.__aenter__ = AsyncMock(return_value=client)
        manager.__aexit__ = AsyncMock(return_value=False)
        with patch('retrieval.query_understanding.httpx.AsyncClient', return_value=manager):
            return await pipeline.classify_intent_llm(ctx)

    async def test_leave_entitlement_cannot_become_ticket_count(self):
        ctx = await self.classify_as_ticket_analytics(
            'How many casual leave days are employees eligible for annually?'
        )
        self.assertEqual(ctx.intent, 'SEARCH')
        self.assertEqual(ctx.pipeline_trace[2]['llm_classifier']['reason'], 'missing_ticket_context')

    async def test_llm_can_classify_an_explicit_ticket_question(self):
        ctx = await self.classify_as_ticket_analytics('Tally tickets by category')
        self.assertEqual(ctx.intent, 'TICKET_ANALYTICS')

    def test_explicit_ticket_count_still_uses_rule(self):
        ctx = QueryUnderstanding().process('How many open tickets are there?')
        self.assertEqual(ctx.intent, 'TICKET_ANALYTICS')


if __name__ == '__main__':
    unittest.main()
