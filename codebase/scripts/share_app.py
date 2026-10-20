"""Password-protected production frontend and authenticated API gateway for sharing.

Bind only to loopback; publish this gateway through an HTTPS tunnel. Credentials
are passed in the environment, never URL parameters or command-line arguments.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from api.auth import decode_token, get_current_user

COOKIE = 'copilot_share_access'
MAX_BODY = 32 * 1024 * 1024
HOP_HEADERS = {'host', 'connection', 'keep-alive', 'proxy-authenticate',
               'proxy-authorization', 'te', 'trailer', 'transfer-encoding',
               'upgrade', 'content-length', 'content-encoding', 'cookie'}


def create_app(username, password, session_key, frontend_dir=None, upstream_transport=None):
    if not username or len(password) < 20 or len(session_key) < 32:
        raise ValueError('Strong sharing credentials are required')
    root = Path(frontend_dir or Path(__file__).resolve().parents[1] / 'ui-react/dist').resolve()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    def signature(payload):
        return hmac.new(session_key.encode(), payload.encode(), hashlib.sha256).hexdigest()

    def valid_cookie(value):
        try:
            expires, nonce, signed = value.split('.')
            return int(expires) > time.time() and hmac.compare_digest(signature(f'{expires}.{nonce}'), signed)
        except (AttributeError, ValueError):
            return False

    def basic_matches(value):
        try:
            scheme, encoded = value.split(' ', 1)
            if scheme.lower() != 'basic':
                return False
            supplied_user, supplied_password = base64.b64decode(encoded, validate=True).decode().split(':', 1)
            return (secrets.compare_digest(supplied_user.encode(), username.encode()) and
                    secrets.compare_digest(supplied_password.encode(), password.encode()))
        except (ValueError, UnicodeError):
            return False

    @app.middleware('http')
    async def sharing_gate(request: Request, call_next):
        basic = basic_matches(request.headers.get('authorization', ''))
        if not basic and not valid_cookie(request.cookies.get(COOKIE)):
            return Response('Sharing password required.', status_code=401,
                headers={'WWW-Authenticate': 'Basic realm="Copilot sharing", charset="UTF-8"',
                         'Cache-Control': 'no-store'})
        # Prevent another site from submitting authenticated actions through a
        # visitor's sharing cookie. Host comes from the HTTPS tunnel request.
        origin = request.headers.get('origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin != f'https://{request.headers.get("host")}':
            return JSONResponse({'detail': 'Cross-site requests are not allowed.'}, status_code=403)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                                 'X-Frame-Options': 'SAMEORIGIN', 'Referrer-Policy': 'same-origin'})
        if basic:
            payload = f'{int(time.time()) + 43200}.{secrets.token_urlsafe(16)}'
            response.set_cookie(COOKIE, f'{payload}.{signature(payload)}', max_age=43200,
                                secure=True, httponly=True, samesite='strict')
        return response

    @app.api_route('/api/{path:path}', methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS', 'HEAD'])
    async def proxy_api(path: str, request: Request):
        # Demo SSO provisioning is unsuitable for access over a public link.
        if path.startswith('auth/sso'):
            return JSONResponse({'detail': 'Use email and password sign-in for sharing.'}, status_code=403)
        public_login = path == 'auth/login' and request.method == 'POST'
        if not public_login:
            authorization = request.headers.get('authorization', '')
            if not authorization.lower().startswith('bearer '):
                return JSONResponse({'detail': 'Sign in to the app.'}, status_code=401)
            try:
                claims = decode_token(authorization.split(' ', 1)[1])
                if not claims.get('sub') or claims['sub'] == 'anonymous':
                    raise ValueError('Anonymous token')
                current = await get_current_user(authorization.split(' ', 1)[1])
            except Exception:
                return JSONResponse({'detail': 'Sign in to the app.'}, status_code=401)
            roles = {str(role).upper() for role in current.access_roles}
            roles.add(str(current.role).upper())
            admin_action = (path.startswith(('admin/', 'ingest/', 'auth/users', 'auth/admin'))
                            or path in ('create_user', 'grant_access')
                            or (request.method not in ('GET', 'HEAD', 'OPTIONS') and path.startswith(('documents/', 'repositories/'))))
            if admin_action and not roles.intersection({'ADMIN', 'IT_ADMIN', 'EXECUTIVE'}):
                return JSONResponse({'detail': 'Administrator access required.'}, status_code=403)
        body = bytearray()
        async for part in request.stream():
            body.extend(part)
            if len(body) > MAX_BODY:
                return JSONResponse({'detail': 'Sharing uploads are limited to 32 MB.'}, status_code=413)
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() not in HOP_HEADERS | {'authorization', 'origin', 'accept-encoding'}
                   and not key.lower().startswith(('x-forwarded-', 'cf-'))}
        if request.headers.get('authorization', '').lower().startswith('bearer '):
            headers['authorization'] = request.headers['authorization']
        try:
            async with httpx.AsyncClient(timeout=240, trust_env=False, transport=upstream_transport) as client:
                upstream = await client.request(request.method, f'http://127.0.0.1:8000/{path}',
                    params=request.query_params.multi_items(), headers=headers, content=bytes(body))
            response_headers = {key: value for key, value in upstream.headers.items()
                                if key.lower() not in HOP_HEADERS | {'set-cookie', 'www-authenticate'}}
            return Response(upstream.content, status_code=upstream.status_code, headers=response_headers)
        except httpx.HTTPError:
            return JSONResponse({'detail': 'The local app is unavailable. Ask the host to start it.'}, status_code=502)

    @app.api_route('/{path:path}', methods=['GET', 'HEAD'])
    async def frontend(path: str):
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            return Response('Not found', status_code=404)
        if target.is_file():
            return FileResponse(target)
        if path.startswith('assets/') or '.' in Path(path).name:
            return Response('Not found', status_code=404)
        index = root / 'index.html'
        if not index.is_file():
            return Response('Build the frontend before sharing.', status_code=503)
        return FileResponse(index)

    return app


def from_environment():
    return create_app(os.environ['COPILOT_SHARE_USERNAME'], os.environ['COPILOT_SHARE_PASSWORD'],
                      os.environ['COPILOT_SHARE_SESSION_KEY'])


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(from_environment(), host='127.0.0.1', port=int(os.environ.get('COPILOT_SHARE_PORT', '3001')),
                access_log=False)
