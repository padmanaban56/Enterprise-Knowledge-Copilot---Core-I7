import unittest
from unittest.mock import AsyncMock, patch
import httpx
from fastapi import HTTPException
from pydantic import SecretStr
from api.model_service import ModelService, is_free_model, is_answer_model


class ModelServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = ModelService()
        self.service.settings = self.service.settings.model_copy(update={'openrouter_api_key':SecretStr('test-secret')})
        self.service.openrouter_models = AsyncMock(return_value=[{'id':'test/model:free','pricing':{'prompt':'0','completion':'0'}}])

    def client(self, response):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        return client

    async def test_selected_model_and_key_are_sent_only_to_openrouter(self):
        client = self.client(httpx.Response(200,json={'choices':[{'message':{'content':'Grounded answer'}}]}))
        with patch('api.model_service.httpx.AsyncClient',return_value=client):
            answer = await self.service.complete('test/model:free','system','context')
        self.assertEqual(answer,'Grounded answer')
        call=client.post.call_args
        self.assertEqual(call.args[0],'https://openrouter.ai/api/v1/chat/completions')
        self.assertEqual(call.kwargs['json']['model'],'test/model:free')
        self.assertEqual(call.kwargs['json']['provider']['max_price'],{'prompt':0,'completion':0,'request':0,'image':0})
        self.assertFalse(call.kwargs['json']['provider']['allow_fallbacks'])
        self.assertEqual(call.kwargs['headers']['Authorization'],'Bearer test-secret')
        self.assertNotIn('test-secret',str(call.kwargs['json']))

    async def test_unknown_model_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            await self.service.validate('openrouter','missing/model')
        self.assertEqual(error.exception.status_code,400)

    async def test_upstream_failure_is_actionable_and_does_not_expose_body(self):
        for status,word in ((401,'API key'),(402,'account limits'),(429,'rate limited')):
            client=self.client(httpx.Response(status,json={'error':'sensitive upstream details'}))
            with patch('api.model_service.httpx.AsyncClient',return_value=client):
                with self.assertRaises(HTTPException) as error:
                    await self.service.complete('test/model:free','system','context')
            self.assertIn(word,error.exception.detail)
            self.assertNotIn('sensitive',error.exception.detail)

    async def test_local_provider_does_not_need_openrouter(self):
        self.service.settings = self.service.settings.model_copy(update={'openrouter_api_key':SecretStr('')})
        await self.service.validate('ollama','phi3:mini')
        self.service.openrouter_models.assert_not_awaited()

    async def test_paid_model_cannot_be_requested_even_if_catalog_contains_it(self):
        self.service.openrouter_models.return_value.append({'id':'test/paid','pricing':{'prompt':'0.1','completion':'0.1'}})
        with patch('api.model_service.httpx.AsyncClient') as client:
            with self.assertRaises(HTTPException):
                await self.service.complete('test/paid','system','context')
            client.assert_not_called()

    def test_free_variant_must_have_known_zero_fees(self):
        self.assertTrue(is_free_model({'id':'test/model:free','pricing':{'prompt':'0','completion':'0.0','request':'0'}}))
        for pricing in ({}, {'prompt':'0'}, {'prompt':'0','completion':'0','request':'1'},
                        {'prompt':'0','completion':'NaN'}, {'prompt':'0','completion':None}):
            self.assertFalse(is_free_model({'id':'test/model:free','pricing':pricing}))

    def test_specialized_models_are_not_chat_models(self):
        for model_id in ('nvidia/nemotron-3.5-content-safety:free', 'meta-llama/llama-guard-3:free',
                         'test/text-embedding:free', 'test/reranker:free', 'test/moderator:free'):
            self.assertFalse(is_answer_model({'id':model_id}))
        self.assertTrue(is_answer_model({'id':'liquid/lfm-2.5-2.6b:free','name':'Liquid LFM'}))
        self.assertTrue(is_answer_model({'id':'nvidia/nemotron-3-super:free'}))

    async def test_free_safety_classifier_is_rejected_before_generation(self):
        model_id='nvidia/nemotron-3.5-content-safety:free'
        self.service.openrouter_models.return_value.append({'id':model_id,'pricing':{'prompt':'0','completion':'0'}})
        with patch('api.model_service.httpx.AsyncClient') as client:
            with self.assertRaises(HTTPException) as error:
                await self.service.complete(model_id,'system','synthetic test')
            self.assertEqual(error.exception.status_code,400)
            client.assert_not_called()
