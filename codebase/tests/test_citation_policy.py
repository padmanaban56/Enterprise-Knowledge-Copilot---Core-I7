import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from api.citation_policy import apply_citation_policy, non_document_reply
from api.analytics_service import ChatHistoryService


class CitationPolicyTests(unittest.TestCase):
    def test_live_questions_get_no_document_sources(self):
        for query in ['what is the time in chennai?', 'What time is it in London?',
                      'Tell me the current time in Chennai', 'What is the weather in Delhi?']:
            with self.subTest(query=query):
                reply = non_document_reply(query)
                self.assertIsNotNone(reply)
                result = apply_citation_policy({'answer': 'Incorrect answer [Source 1]',
                                                'citations': [{'doc_id': 'unrelated'}]}, query)
                self.assertEqual(result['answer'], reply)
                self.assertEqual(result['citations'], [])
                self.assertEqual(result['chunks_used'], 0)
                self.assertEqual(result['confidence'], 0)

    def test_real_document_questions_are_not_short_circuited(self):
        for query in ['What is the time off policy?', 'What is our severe weather policy?',
                      'What time zone is the Chennai office in?',
                      'What is the time complexity of this algorithm?',
                      'What is the time required for invoice approval?',
                      'How do I request annual leave?']:
            with self.subTest(query=query):
                self.assertIsNone(non_document_reply(query))

    def test_screenshot_and_other_unsupported_answers_lose_markers_and_footer(self):
        for answer in [
            '- Based on available documentation, there is no specific information provided regarding the current time in Chennai. [Source 1] [Source 2]\n\n📎 Sources: [Source 1], [Source 2]',
            "I'm sorry, but the provided context does not contain information on annual leave. [Source 1]\nSources: HR.pdf",
            "I couldn't find enough relevant information for this query. [Source 1]",
        ]:
            with self.subTest(answer=answer):
                result = apply_citation_policy({'answer': answer, 'citations': [{'doc_id': 'irrelevant'}]})
                self.assertEqual(result['citations'], [])
                self.assertNotIn('[Source', result['answer'])
                self.assertNotIn('Sources:', result['answer'])
                self.assertEqual(result['citation_status'], 'unsupported')

    def test_grounded_answers_including_negative_policy_facts_keep_citations(self):
        for answer in ['Submit leave in the HR portal. [Source 1]',
                       'There is no annual leave during probation. [Source 1]',
                       'Approval is required. [Source 1] The documents do not specify the processing time.']:
            original = {'answer': answer, 'citations': [{'doc_id': 'correct'}], 'confidence': .9}
            self.assertEqual(apply_citation_policy(original), original)

    def test_unsupported_citations_are_removed_before_storage(self):
        service = ChatHistoryService.__new__(ChatHistoryService)
        service.engine = MagicMock()
        conn = service.engine.begin.return_value.__enter__.return_value
        service.add_message('session', 'owner', 'assistant',
            "The provided context does not contain the information requested. [Source 1]\nSources: irrelevant.pdf",
            citations=[{'doc_id': 'irrelevant'}], confidence=.8)
        params = conn.execute.call_args.args[1]
        self.assertEqual(json.loads(params['citations']), [])
        self.assertEqual(params['confidence'], 0)
        self.assertEqual(json.loads(params['meta'])['citation_status'], 'unsupported')
        self.assertNotIn('[Source', params['content'])

    def test_old_live_question_citations_are_hidden_on_reload(self):
        service = ChatHistoryService.__new__(ChatHistoryService)
        service.engine = MagicMock()
        conn = service.engine.connect.return_value.__enter__.return_value
        conn.execute.side_effect = [MagicMock(fetchone=lambda: SimpleNamespace(user_id='owner')),
            [SimpleNamespace(_mapping={'role': 'user', 'content': 'what is the time in chennai?'}),
             SimpleNamespace(_mapping={'role': 'assistant', 'content': 'Some unrelated answer [Source 1]',
                'citations': [{'doc_id': 'unrelated'}], 'confidence': .8, 'retrieval_meta': {}})]]
        answer = service.get_session_messages('session', 'owner')[1]
        self.assertEqual(answer['citations'], [])
        self.assertEqual(answer['content'], non_document_reply('what is the time in chennai?'))
        self.assertEqual(answer['retrieval_meta']['citation_status'], 'not_applicable')


class CitationRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_time_skips_retrieval_and_model_and_persists_no_sources(self):
        from api import main
        history = MagicMock()
        with patch.object(main, '_run_chat', AsyncMock()) as pipeline, \
             patch.object(main, 'chat_history_service', history):
            response = await main.chat(main.ChatRequest(query='what is the time in chennai?'),
                                       SimpleNamespace(user_id='owner'))
        pipeline.assert_not_awaited()
        self.assertEqual(response.citation_status, 'not_applicable')
        self.assertEqual(response.citations, [])
        self.assertEqual(response.chunks_used, 0)
        self.assertEqual(history.add_message.call_args.kwargs['citations'], [])


if __name__ == '__main__':
    unittest.main()
