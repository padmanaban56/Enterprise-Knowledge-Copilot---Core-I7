"""Server-side model discovery and OpenRouter generation."""
import time
import re
from decimal import Decimal, InvalidOperation
import httpx
from fastapi import HTTPException
from configs.settings import get_settings

OPENROUTER_URL = 'https://openrouter.ai/api/v1'


def is_answer_model(model):
    """Exclude explicitly specialized models from the answer generator."""
    identity = f"{model.get('id', '')} {model.get('name', '')}".lower()
    specialized = re.search(
        r'(^|[^a-z0-9])(safety|guard|safeguard|guardrail|moderation|moderator|'
        r'classifier|reranker?|embeddings?|shieldgemma)([^a-z0-9]|$)', identity)
    architecture = model.get('architecture') or {}
    return not specialized and (
        'text' in architecture.get('input_modalities', ['text']) and
        'text' in architecture.get('output_modalities', ['text']))


def is_free_model(model):
    """Fail closed unless this is an explicitly free variant with zero fees."""
    if not model.get('id', '').endswith(':free'):
        return False
    pricing = model.get('pricing') or {}
    if 'prompt' not in pricing or 'completion' not in pricing:
        return False
    try:
        return all(Decimal(str(value)).is_finite() and Decimal(str(value)) == 0
                   for value in pricing.values())
    except (InvalidOperation, ValueError, TypeError):
        return False


class ModelService:
    def __init__(self):
        self.settings = get_settings()
        self._catalog = None
        self._expires = 0
        self._active_key_index = 0

    def _keys(self):
        raw = self.settings.openrouter_api_keys.get_secret_value()
        if not raw.strip():
            raw = self.settings.openrouter_api_key.get_secret_value()
        return list(dict.fromkeys(key.strip() for key in re.split(r'[,\n]+', raw) if key.strip()))

    @property
    def configured(self):
        return bool(self._keys())

    async def openrouter_models(self):
        if self._catalog is not None and time.monotonic() < self._expires:
            return self._catalog
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(f'{OPENROUTER_URL}/models')
                response.raise_for_status()
            self._catalog = [m for m in response.json()['data']
                             if is_free_model(m) and is_answer_model(m)]
            self._expires = time.monotonic() + 900
            return self._catalog
        except (httpx.HTTPError, ValueError, KeyError):
            if self._catalog is not None:
                return self._catalog
            raise HTTPException(503, 'OpenRouter model catalog is temporarily unavailable. Try refreshing models.')

    async def list_models(self):
        models, errors = [], []
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f'{self.settings.ollama_base_url}/api/tags')
                response.raise_for_status()
            models.extend({'id':m['name'], 'name':m['name'], 'provider':'ollama',
                           'open_weights':True, 'pricing':{'prompt':'0','completion':'0'}}
                          for m in response.json().get('models', []))
        except (httpx.HTTPError, ValueError, KeyError):
            errors.append('Local Ollama is unavailable.')
        if self.configured:
            try:
                models.extend({'id':m['id'], 'name':m.get('name',m['id']),
                               'provider':'openrouter', 'open_weights':bool(m.get('hugging_face_id')),
                               'context_length':m.get('context_length'), 'pricing':m.get('pricing',{})}
                              for m in await self.openrouter_models() if is_free_model(m) and is_answer_model(m))
            except HTTPException as error:
                errors.append(error.detail)
        return {'models':models, 'openrouter_configured':self.configured, 'errors':errors}

    async def validate(self, provider, model):
        if provider != 'openrouter':
            return
        if not self.configured:
            raise HTTPException(503, 'OpenRouter is not configured on the server.')
        if not model or not model.endswith(':free'):
            raise HTTPException(400, 'Only free OpenRouter model variants are allowed. Choose a :free model.')
        if model not in {m['id'] for m in await self.openrouter_models() if is_free_model(m) and is_answer_model(m)}:
            raise HTTPException(400, 'Choose an available free chat model. Safety classifiers, embedding models, and rerankers cannot generate chat answers.')

    async def complete(self, model, system, prompt):
        await self.validate('openrouter', model)
        keys = self._keys()
        start = self._active_key_index % len(keys)
        messages = {401:'OpenRouter rejected the API key. Update the OpenRouter keys on the server.',
                        402:'OpenRouter rejected this free-model request due to account limits. Choose another free model or use local Ollama.',
                        403:'OpenRouter denied this request. Check the account and model permissions.',
                        429:'This OpenRouter model is rate limited. Try another model or retry later.'}
        async with httpx.AsyncClient(timeout=180) as client:
            for attempt in range(len(keys)):
                index = (start + attempt) % len(keys)
                headers = {'Authorization':'Bearer '+keys[index], 'X-Title':'Enterprise Knowledge Copilot'}
                try:
                    response = await client.post(f'{OPENROUTER_URL}/chat/completions', headers=headers,
                        json={'model':model, 'messages':[{'role':'system','content':system},
                              {'role':'user','content':prompt}], 'max_tokens':1024,
                              'temperature':0.1, 'stream':False,
                              'provider':{'allow_fallbacks':False,
                                          'max_price':{'prompt':0,'completion':0,'request':0,'image':0}}})
                except httpx.TimeoutException:
                    last_error = HTTPException(504, 'The selected OpenRouter model timed out. Try another model.')
                except httpx.HTTPError:
                    last_error = HTTPException(503, 'Unable to connect to OpenRouter. Try again shortly.')
                else:
                    if response.status_code >= 400:
                        last_error = HTTPException(502, messages.get(response.status_code,
                            'The selected OpenRouter model is unavailable. Try another model.'))
                    else:
                        try:
                            content = response.json()['choices'][0]['message']['content']
                            if isinstance(content,list):
                                content = ''.join(p.get('text','') for p in content if isinstance(p,dict))
                            if not isinstance(content,str) or not content.strip():
                                raise ValueError('Empty answer')
                            self._active_key_index = index
                            return content.strip()
                        except (ValueError, KeyError, IndexError, TypeError):
                            last_error = HTTPException(502, 'OpenRouter returned no answer. Try another model.')
                self._active_key_index = (index + 1) % len(keys)
        raise last_error


model_service = ModelService()
