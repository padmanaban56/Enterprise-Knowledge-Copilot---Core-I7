import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

import httpx

from scripts.share_app import create_app, COOKIE


class SharingGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        (root / 'index.html').write_text('<html>Copilot test</html>')
        self.calls = []

        async def upstream(request):
            self.calls.append(request)
            return httpx.Response(200, json={'ok': True})

        self.auth = httpx.BasicAuth('share', 'test-password-for-sharing-only')
        app = create_app('share', 'test-password-for-sharing-only', 'test-session-signing-key-' * 2,
                         frontend_dir=root, upstream_transport=httpx.MockTransport(upstream))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://share.example')

    async def asyncTearDown(self):
        await self.client.aclose()
        self.directory.cleanup()

    async def unlock(self):
        response = await self.client.get('/', auth=self.auth)
        self.assertEqual(response.status_code, 200)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('Secure', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])

    async def test_entire_site_requires_sharing_password(self):
        for path in ['/', '/chat', '/api/documents', '/assets/main.js']:
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 401)
        self.assertFalse(self.calls)

    async def test_forged_cookie_is_rejected(self):
        self.client.cookies.set(COOKIE, '9999999999.fake.invalid')
        self.assertEqual((await self.client.get('/')).status_code, 401)

    async def test_gate_cookie_and_app_bearer_coexist_and_secrets_are_not_proxied(self):
        await self.unlock()
        self.assertEqual((await self.client.get('/api/documents')).status_code, 401)
        with patch('scripts.share_app.decode_token', return_value={'sub': 'user', 'role': 'employee'}), patch('scripts.share_app.get_current_user', AsyncMock(return_value=SimpleNamespace(role='employee',access_roles=[]))):
            response = await self.client.get('/api/documents', headers={'Authorization': 'Bearer test-jwt'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[0].headers['authorization'], 'Bearer test-jwt')
        self.assertNotIn('cookie', self.calls[0].headers)
        self.assertNotIn('test-password', str(self.calls[0].headers))

    async def test_login_works_but_sso_provisioning_and_anonymous_admin_requests_do_not(self):
        await self.unlock()
        self.assertEqual((await self.client.post('/api/auth/login', json={'email': 'test', 'password': 'test'})).status_code, 200)
        self.assertEqual((await self.client.post('/api/auth/sso/callback', json={})).status_code, 403)
        self.assertEqual((await self.client.post('/api/admin/clear-knowledge-base', json={})).status_code, 401)

    async def test_administrative_endpoints_require_admin_claims(self):
        await self.unlock()
        headers = {'Authorization': 'Bearer test-jwt'}
        with patch('scripts.share_app.decode_token', return_value={'sub': 'user', 'role': 'employee'}), patch('scripts.share_app.get_current_user', AsyncMock(return_value=SimpleNamespace(role='employee',access_roles=[]))):
            response = await self.client.post('/api/admin/clear-knowledge-base', headers=headers, json={})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.calls)

    async def test_cross_site_mutations_and_path_traversal_are_rejected(self):
        await self.unlock()
        response = await self.client.post('/api/auth/login', headers={'Origin': 'https://unrelated.example'}, json={})
        self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.client.get('/%2e%2e/private.txt')).status_code, 404)
        self.assertEqual((await self.client.get('/chat')).status_code, 200)


if __name__ == '__main__':
    unittest.main()
