import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { transformSync } from 'esbuild'
import { api } from './api.js'

function makeStorage() {
  const values = new Map()
  return { getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) }
}

beforeEach(() => {
  globalThis.localStorage = makeStorage()
  globalThis.window = { location: { pathname: '/dashboard', href: '/dashboard' } }
})

function providerFor(login) {
  // Exercise the login callback before React gets a chance to run effects or
  // navigate. This reproduces the first-dashboard-request timing boundary.
  const hooks = { createContext: () => ({ Provider: 'provider' }), useContext: () => {},
    useState: initial => [typeof initial === 'function' ? initial() : initial, () => {}],
    useEffect: () => {}, useCallback: callback => callback }
  const source = readFileSync(new URL('../context/AuthContext.jsx', import.meta.url), 'utf8')
  const { code } = transformSync(source, { loader: 'jsx', jsx: 'automatic', format: 'cjs' })
  const module = { exports: {} }
  vm.runInNewContext(code, { module, exports: module.exports, localStorage,
    require: name => {
      if (name === 'react') return hooks
      if (name === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }) }
      if (name === '../services/api') return { api: { login } }
      throw new Error('Unexpected import: ' + name)
    } })
  return module.exports.AuthProvider({ children: null }).props.value
}

test('successful login stores credentials before navigation and the first dashboard fetch', async () => {
  const auth = providerFor(async () => ({ access_token: 'fresh-token', user: { username: 'demo' }, must_change_password: false }))
  await auth.login('demo@example.test', 'test-password')
  assert.equal(localStorage.getItem('ekc_token'), 'fresh-token')
  assert.equal(JSON.parse(localStorage.getItem('ekc_user')).username, 'demo')
  const requests = []
  globalThis.fetch = async (url, options) => { requests.push(options.headers.Authorization); return Response.json({}) }
  await Promise.all([api.dashboardMetrics(7), api.getRepositories()])
  assert.deepEqual(requests, ['Bearer fresh-token', 'Bearer fresh-token'])
})

test('logout clears persisted credentials immediately', () => {
  localStorage.setItem('ekc_token', 'old-token')
  localStorage.setItem('ekc_user', JSON.stringify({ username: 'demo' }))
  providerFor(async () => {}).logout()
  assert.equal(localStorage.getItem('ekc_token'), null)
  assert.equal(localStorage.getItem('ekc_user'), null)
})

test('a delayed unauthenticated request cannot delete a login that just completed', async () => {
  let respond
  globalThis.fetch = () => new Promise(resolve => { respond = resolve })
  const pending = api.getRepositories()
  localStorage.setItem('ekc_token', 'new-token')
  respond(Response.json({ detail: 'Sign in to the app.' }, { status: 401 }))
  await assert.rejects(pending, /Sign in/)
  assert.equal(localStorage.getItem('ekc_token'), 'new-token')
  assert.equal(window.location.href, '/dashboard')
})

test('a failed request from an older token cannot log out a newer session', async () => {
  localStorage.setItem('ekc_token', 'old-token')
  let respond
  globalThis.fetch = () => new Promise(resolve => { respond = resolve })
  const pending = api.getRepositories()
  localStorage.setItem('ekc_token', 'new-token')
  respond(Response.json({ detail: 'Token expired' }, { status: 401 }))
  await assert.rejects(pending, /expired/)
  assert.equal(localStorage.getItem('ekc_token'), 'new-token')
  assert.equal(window.location.href, '/dashboard')
})

test('a genuinely expired current token still clears the session and redirects', async () => {
  localStorage.setItem('ekc_token', 'expired-token')
  localStorage.setItem('ekc_user', '{}')
  globalThis.fetch = async () => Response.json({ detail: 'Token expired' }, { status: 401 })
  await assert.rejects(api.getRepositories(), /expired/)
  assert.equal(localStorage.getItem('ekc_token'), null)
  assert.equal(window.location.href, '/login')
})

test('sharing-password challenges do not delete the app login', async () => {
  localStorage.setItem('ekc_token', 'valid-token')
  globalThis.fetch = async () => new Response('Sharing password required.', {
    status: 401, headers: { 'WWW-Authenticate': 'Basic realm="Copilot sharing"' } })
  await assert.rejects(api.getRepositories(), /Sharing access needs to be renewed/)
  assert.equal(localStorage.getItem('ekc_token'), 'valid-token')
  assert.equal(window.location.href, '/dashboard')
})

test('incorrect login credentials surface an error without clearing a separate current session', async () => {
  localStorage.setItem('ekc_token', 'valid-token')
  globalThis.fetch = async () => Response.json({ detail: 'Invalid credentials' }, { status: 401 })
  await assert.rejects(api.login({ email: 'demo@example.test', password: 'wrong' }), /Invalid credentials/)
  assert.equal(localStorage.getItem('ekc_token'), 'valid-token')
})
