// src/services/api.js  —  All API calls to FastAPI backend

const BASE = '/api'

function authHeaders() {
  const token = localStorage.getItem('ekc_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function request(path, options = {}) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...options.headers },
    ...options,
  })
  if (res.status === 401) {
    localStorage.removeItem('ekc_token')
    localStorage.removeItem('ekc_user')
    if (!window.location.pathname.startsWith('/login')) {
      window.location.href = '/login'
    }
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export const api = {
  // ── Auth ────────────────────────────────────────────────────────────────────
  login: (body) => request('/auth/login', { method: 'POST', body: JSON.stringify(body) }),
  ssoCallback: (body) => request('/auth/sso/callback', { method: 'POST', body: JSON.stringify(body) }),

  // ── Status ──────────────────────────────────────────────────────────────────
  status: () => request('/status'),

  // ── Chat ────────────────────────────────────────────────────────────────────
  chat: (body) => request('/chat', { method: 'POST', body: JSON.stringify(body) }),
  agentChat: (body) => request('/agent/chat', { method: 'POST', body: JSON.stringify(body) }),

  // ── Repositories ────────────────────────────────────────────────────────────
  getRepositories: () => request('/repositories'),
  getRepoDocuments: (name, limit = 50) => request(`/repositories/${name}/documents?limit=${limit}`),

  // ── Documents ───────────────────────────────────────────────────────────────
  getDocuments: (repository = null, limit = 100) =>
    request(`/documents?limit=${limit}${repository ? `&repository=${repository}` : ''}`),
  getDocument: (docId) => request(`/documents/${docId}`),
  updateDocument: (docId, body) => request(`/documents/${docId}`, { method: 'PATCH', body: JSON.stringify(body) }),
  // Fetches the original uploaded file as a Blob (auth header required, so a
  // plain <a href> can't be used directly). Caller turns this into an
  // object URL — see DocumentDetail.jsx's `openDocumentFile`.
  getDocumentFile: async (docId) => {
    const res = await fetch(`${BASE}/documents/${docId}/file`, { headers: authHeaders() })
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }))
      throw new Error(err.detail || `HTTP ${res.status}`)
    }
    return res.blob()
  },

  ingestFile: (formData) =>
    fetch(`${BASE}/ingest/file`, { method: 'POST', headers: authHeaders(), body: formData })
      .then(r => r.json()),

  // ── Ingestion job tracking (bulk upload, progress polling, cancellation) ──
  ingestBulk: (formData) =>
    fetch(`${BASE}/ingest/bulk`, { method: 'POST', headers: authHeaders(), body: formData })
      .then(r => r.json()),
  ingestBulkZip: (formData) =>
    fetch(`${BASE}/ingest/bulk/zip`, { method: 'POST', headers: authHeaders(), body: formData })
      .then(r => r.json()),
  getIngestJob: (jobId) => request(`/ingest/jobs/${jobId}`),
  listIngestJobs: (batchId = null, limit = 50) => {
    const params = new URLSearchParams({ limit })
    if (batchId) params.append('batch_id', batchId)
    return request(`/ingest/jobs?${params}`)
  },
  cancelIngestJob: (jobId) => request(`/ingest/jobs/${jobId}/cancel`, { method: 'POST' }),
  cancelIngestBatch: (batchId) => request(`/ingest/batches/${batchId}/cancel`, { method: 'POST' }),

  ingestTickets: (formData) =>
    fetch(`${BASE}/ingest/tickets`, { method: 'POST', headers: authHeaders(), body: formData })
      .then(r => r.json()),

  // ── Tickets ─────────────────────────────────────────────────────────────────
  searchTickets: (q, category, priority, limit = 10) => {
    const params = new URLSearchParams({ q, limit })
    if (category) params.append('category', category)
    if (priority) params.append('priority', priority)
    return request(`/tickets/search?${params}`)
  },
  recentTickets: (limit = 20) => request(`/tickets/recent?limit=${limit}`),
  ticketCategories: () => request('/tickets/categories'),

  // ── Feedback ─────────────────────────────────────────────────────────────────
  submitFeedback: (body) => request('/feedback', { method: 'POST', body: JSON.stringify(body) }),
  getFeedbackSummary: (days = 7) => request(`/feedback/summary?days=${days}`),

  // ── Analytics ─────────────────────────────────────────────────────────────────
  dashboardMetrics: (days = 7) => request(`/analytics/dashboard?days=${days}`),
  evaluationMetrics: (days = 7) => request(`/analytics/evaluation?days=${days}`),

  // ── Knowledge Gaps ───────────────────────────────────────────────────────────
  getKnowledgeGaps: (limit = 20) => request(`/knowledge-gaps?limit=${limit}`),
  resolveGap: (gapId) => request(`/knowledge-gaps/${gapId}/resolve`, { method: 'POST' }),

  // ── Bundles ──────────────────────────────────────────────────────────────────
  listBundles: () => request('/bundles'),
  getBundles: () => request('/bundles'),
  searchBundles: (q) => request(`/bundles/search?q=${encodeURIComponent(q)}`),
  getBundle: (id) => request(`/bundles/${id}`),
  createBundle: (body) => request('/bundles', { method: 'POST', body: JSON.stringify(body) }),
  updateBundle: (id, body) => request(`/bundles/${id}`, { method: 'PATCH', body: JSON.stringify(body) }),
  pinBundle: (id, pinned) => request(`/bundles/${id}/pin`, { method: 'PATCH', body: JSON.stringify({ pinned }) }),
  applyBundle: (id, chatId) => request(`/bundles/${id}/apply`, { method: 'POST', body: JSON.stringify({ chat_id: chatId }) }),
  deleteBundle: (id) => request(`/bundles/${id}`, { method: 'DELETE' }),

  // ── Help Center ──────────────────────────────────────────────────────────────
  saveIssueDraft: (body) => request('/issues/draft', { method: 'POST', body: JSON.stringify(body) }),
  getIssueDraft: (chatId) => request(`/issues/draft?chat_id=${encodeURIComponent(chatId)}`),
  submitIssue: (chatId) => request('/issues/submit', { method: 'POST', body: JSON.stringify({ chat_id: chatId }) }),
  faqFeedback: (faqId, vote) => request('/faq/feedback', { method: 'POST', body: JSON.stringify({ faq_id: faqId, vote }) }),

  // ── Chat History ─────────────────────────────────────────────────────────────
  listChatSessions: () => request('/chat/sessions'),
  getChatSessionMessages: (sessionId) => request(`/chat/sessions/${sessionId}/messages`),

  // ── Access Requests ───────────────────────────────────────────────────────────
  myAccessRequests: () => request('/access-requests'),
  allAccessRequests: () => request('/access-requests/all'),
  createAccessRequest: (body) => request('/access-requests', { method: 'POST', body: JSON.stringify(body) }),
  resolveAccessRequest: (id, body) => request(`/access-requests/${id}/resolve`, { method: 'POST', body: JSON.stringify(body) }),

  // ── Audit Log ─────────────────────────────────────────────────────────────────
  auditLogs: (params = {}) => {
    const qs = new URLSearchParams(params).toString()
    return request(`/audit/logs${qs ? `?${qs}` : ''}`)
  },
  auditMetrics: (days = 7) => request(`/audit/metrics?days=${days}`),
  clearKnowledgeBase: (scope = 'all') => request('/admin/clear-knowledge-base', { method: 'POST', body: JSON.stringify({ scope, confirm: true }) }),
  listUsers: () => request('/auth/users'),
  updateUserRoles: (userId, body) => request(`/auth/users/${userId}/roles`, { method: 'PATCH', body: JSON.stringify(body) }),
  adminResetPassword: (userId, newPassword) => request(`/auth/users/${userId}/reset-password`, { method: 'POST', body: JSON.stringify({ new_password: newPassword }) }),
  changePassword: (body) => request('/auth/change-password', { method: 'POST', body: JSON.stringify(body) }),
  createUser: (body) => request('/create_user', { method: 'POST', body: JSON.stringify(body) }),
  grantAccess: (body) => request('/grant_access', { method: 'POST', body: JSON.stringify(body) }),
}