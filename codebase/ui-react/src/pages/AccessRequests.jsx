import { useState, useEffect } from 'react'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'
import { ShieldCheck, Send, Clock, Check, X, AlertCircle, Loader2, FileText, ChevronDown, ChevronUp } from 'lucide-react'

const ACCESS_ROLES = [
  { value: 'HR', label: 'HR', desc: 'Human Resources policies, onboarding, leave & benefits documents' },
  { value: 'FINANCE', label: 'Finance', desc: 'Invoices, expense policies, budgets, approval workflows' },
  { value: 'MANAGER', label: 'Manager', desc: 'Team management resources, performance review templates' },
  { value: 'IT_ADMIN', label: 'IT Admin', desc: 'Infrastructure runbooks, admin consoles, elevated tickets' },
  { value: 'EXECUTIVE', label: 'Executive', desc: 'Strategy documents, board materials, company-wide reports' },
]

const DURATION_OPTIONS = [
  { value: '', label: 'Permanent' },
  { value: '7', label: '7 days' },
  { value: '14', label: '14 days' },
  { value: '30', label: '30 days' },
  { value: '90', label: '90 days' },
]

function StatusBadge({ status }) {
  const map = {
    pending: { cls: 'bg-amber-500/10 text-amber-300 border-amber-500/20', icon: Clock },
    approved: { cls: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20', icon: Check },
    rejected: { cls: 'bg-red-500/10 text-red-300 border-red-500/20', icon: X },
  }
  const { cls, icon: Icon } = map[status] || map.pending
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full border font-medium inline-flex items-center gap-1 ${cls}`}>
      <Icon size={11} /> {status}
    </span>
  )
}

export default function AccessRequests() {
  const { user } = useAuth()
  const myRoles = user?.access_roles || []
  const [requests, setRequests] = useState([])
  const [loading, setLoading] = useState(true)
  const [selectedRole, setSelectedRole] = useState('')
  const [justification, setJustification] = useState('')
  const [duration, setDuration] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [msg, setMsg] = useState(null)
  const [error, setError] = useState(null)

  // File-specific access state
  const [repositories, setRepositories] = useState([])
  const [expandedRepo, setExpandedRepo] = useState(null)
  const [repoDocuments, setRepoDocuments] = useState({}) // repoName → [docs]
  const [loadingDocs, setLoadingDocs] = useState({})
  const [selectedFiles, setSelectedFiles] = useState([]) // [{repo, docId, filename}]
  const [requestMode, setRequestMode] = useState('role') // 'role' | 'files'

  useEffect(() => {
    load()
    api.getRepositories().then(r => setRepositories(r.repositories || [])).catch(() => {})
  }, [])

  async function load() {
    setLoading(true)
    try {
      const reqs = await api.myAccessRequests()
      setRequests(reqs)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  async function loadRepoDocs(repoName) {
    if (repoDocuments[repoName]) return
    setLoadingDocs(p => ({ ...p, [repoName]: true }))
    try {
      const res = await api.getRepoDocuments(repoName, 100)
      setRepoDocuments(p => ({ ...p, [repoName]: res.documents || [] }))
    } catch {
      setRepoDocuments(p => ({ ...p, [repoName]: [] }))
    } finally {
      setLoadingDocs(p => ({ ...p, [repoName]: false }))
    }
  }

  function toggleRepo(repoName) {
    if (expandedRepo === repoName) {
      setExpandedRepo(null)
    } else {
      setExpandedRepo(repoName)
      loadRepoDocs(repoName)
    }
  }

  function toggleFile(repo, doc) {
    const key = doc.doc_id || doc.id || doc.filename
    setSelectedFiles(prev => {
      const exists = prev.find(f => f.key === key)
      if (exists) return prev.filter(f => f.key !== key)
      return [...prev, { key, repo, docId: key, filename: doc.filename || doc.title || doc.name }]
    })
  }

  function flash(m, isError = false) {
    if (isError) setError(m); else setMsg(m)
    setTimeout(() => { setMsg(null); setError(null) }, 4000)
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (requestMode === 'role') {
      if (!selectedRole || !justification.trim()) {
        flash('Please select an access type and provide a justification', true)
        return
      }
      setSubmitting(true)
      try {
        const body = {
          resource_name: selectedRole,
          justification,
          ...(duration ? { duration_days: parseInt(duration) } : {}),
        }
        await api.createAccessRequest(body)
        flash('Access request submitted — an admin will review it shortly')
        setSelectedRole('')
        setJustification('')
        setDuration('')
        load()
      } catch (e) {
        flash(e.message, true)
      } finally {
        setSubmitting(false)
      }
    } else {
      if (!selectedFiles.length || !justification.trim()) {
        flash('Please select at least one file and provide a justification', true)
        return
      }
      setSubmitting(true)
      try {
        // Submit one request per selected file
        for (const f of selectedFiles) {
          const body = {
            resource_name: `FILE:${f.repo}/${f.filename}`,
            justification,
            ...(duration ? { duration_days: parseInt(duration) } : {}),
          }
          await api.createAccessRequest(body)
        }
        flash(`${selectedFiles.length} file access request(s) submitted`)
        setSelectedFiles([])
        setJustification('')
        setDuration('')
        load()
      } catch (e) {
        flash(e.message, true)
      } finally {
        setSubmitting(false)
      }
    }
  }

  const availableRoles = ACCESS_ROLES.filter(r => !myRoles.includes(r.value))
  const pendingRoleNames = new Set(requests.filter(r => r.status === 'pending').map(r => r.resource_name))

  if (loading) return <div className="p-6 text-slate-500 text-sm">Loading...</div>

  return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-slate-100 flex items-center gap-2">
          <ShieldCheck size={20} className="text-brand-400" /> Request Access
        </h1>
        <p className="text-sm text-slate-500 mt-1">
          Need access to documents outside your current role? Submit a request and an admin will review it.
        </p>
      </div>

      {msg && <div className="flex items-center gap-2 text-sm text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2"><Check size={14} />{msg}</div>}
      {error && <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2"><AlertCircle size={14} />{error}</div>}

      {/* Current access */}
      <div className="card p-4">
        <div className="text-xs font-medium text-slate-500 mb-2">Your current access roles</div>
        <div className="flex flex-wrap gap-1.5">
          {myRoles.length === 0 ? (
            <span className="text-xs text-slate-600">None assigned yet</span>
          ) : myRoles.map(r => (
            <span key={r} className="text-xs px-2 py-0.5 rounded-full border bg-indigo-500/10 text-indigo-300 border-indigo-500/20 font-medium">{r}</span>
          ))}
        </div>
      </div>

      {/* Mode toggle */}
      <div className="flex gap-1 p-1 bg-slate-800/60 rounded-xl w-fit">
        {[['role', 'Request Role Access'], ['files', 'Request File Access']].map(([mode, label]) => (
          <button key={mode} onClick={() => setRequestMode(mode)}
            className={`px-4 py-2 text-xs font-medium rounded-lg transition-colors ${requestMode === mode ? 'bg-brand-600 text-white' : 'text-slate-400 hover:text-slate-200'}`}>
            {label}
          </button>
        ))}
      </div>

      <form onSubmit={handleSubmit} className="card p-5 space-y-4">
        {requestMode === 'role' ? (
          <>
            {availableRoles.length === 0 ? (
              <div className="text-sm text-slate-500 text-center py-2">You already have access to all available roles.</div>
            ) : (
              <div>
                <div className="text-xs font-medium text-slate-400 mb-2">What role access do you need?</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {availableRoles.map(r => {
                    const isPending = pendingRoleNames.has(r.value)
                    return (
                      <button key={r.value} type="button" disabled={isPending}
                        onClick={() => setSelectedRole(r.value)}
                        className={`text-left p-3 rounded-lg border transition-colors ${
                          selectedRole === r.value ? 'border-brand-500 bg-brand-600/10' :
                          isPending ? 'border-slate-700/30 opacity-50 cursor-not-allowed' :
                          'border-slate-700/50 hover:border-slate-600'
                        }`}>
                        <div className="text-sm font-medium text-slate-200 flex items-center gap-2">
                          {r.label}
                          {isPending && <span className="text-xs text-amber-400">(pending)</span>}
                        </div>
                        <div className="text-xs text-slate-500 mt-0.5">{r.desc}</div>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </>
        ) : (
          <div>
            <div className="text-xs font-medium text-slate-400 mb-2 flex items-center gap-2">
              <FileText size={13} /> Select specific files you need access to
            </div>
            {selectedFiles.length > 0 && (
              <div className="mb-3 flex flex-wrap gap-1.5">
                {selectedFiles.map(f => (
                  <span key={f.key} className="text-xs px-2 py-1 bg-brand-600/15 text-brand-300 border border-brand-500/30 rounded-lg flex items-center gap-1">
                    {f.filename}
                    <button type="button" onClick={() => setSelectedFiles(p => p.filter(x => x.key !== f.key))}
                      className="ml-1 hover:text-white"><X size={10} /></button>
                  </span>
                ))}
              </div>
            )}
            <div className="space-y-1 max-h-64 overflow-y-auto">
              {repositories.map(repo => (
                <div key={repo.name} className="border border-slate-700/40 rounded-lg overflow-hidden">
                  <button type="button"
                    onClick={() => toggleRepo(repo.name)}
                    className="w-full flex items-center justify-between px-3 py-2.5 bg-slate-800/40 hover:bg-slate-700/40 transition-colors text-sm">
                    <span className="text-slate-300 font-medium">{repo.display_name || repo.name}</span>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-600">{repo.document_count} docs</span>
                      {expandedRepo === repo.name ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
                    </div>
                  </button>
                  {expandedRepo === repo.name && (
                    <div className="bg-slate-900/40 px-2 py-1.5 space-y-0.5 max-h-40 overflow-y-auto">
                      {loadingDocs[repo.name] ? (
                        <div className="text-xs text-slate-600 py-2 text-center">Loading...</div>
                      ) : (repoDocuments[repo.name] || []).length === 0 ? (
                        <div className="text-xs text-slate-600 py-2 text-center">No documents found</div>
                      ) : (repoDocuments[repo.name] || []).map(doc => {
                        const key = doc.doc_id || doc.id || doc.filename
                        const isSelected = selectedFiles.find(f => f.key === key)
                        return (
                          <label key={key} className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-slate-800/60 cursor-pointer">
                            <input type="checkbox" checked={!!isSelected}
                              onChange={() => toggleFile(repo.name, doc)}
                              className="rounded border-slate-600 bg-slate-800 text-brand-500 focus:ring-brand-500/30" />
                            <FileText size={11} className="text-slate-500 flex-shrink-0" />
                            <span className="text-xs text-slate-300 truncate">{doc.filename || doc.title || doc.name}</span>
                          </label>
                        )
                      })}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Duration selection */}
        <div>
          <div className="text-xs font-medium text-slate-400 mb-2">Access duration <span className="text-slate-600 font-normal">(optional)</span></div>
          <div className="flex flex-wrap gap-2">
            {DURATION_OPTIONS.map(opt => (
              <button key={opt.value} type="button" onClick={() => setDuration(opt.value)}
                className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                  duration === opt.value ? 'border-brand-500 bg-brand-600/10 text-brand-300' : 'border-slate-700/50 text-slate-500 hover:border-slate-500 hover:text-slate-300'
                }`}>
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="block text-xs font-medium text-slate-400 mb-1.5">Justification</label>
          <textarea value={justification} onChange={e => setJustification(e.target.value)}
            placeholder="e.g. I'm working on the Q3 budget review and need access to Finance documents"
            rows={3}
            className="w-full bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500/50 resize-none" />
        </div>

        <button type="submit" disabled={submitting} className="btn-primary">
          {submitting ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
          Submit Request
        </button>
      </form>

      {/* My requests */}
      {requests.length > 0 && (
        <div>
          <div className="text-xs font-medium text-slate-500 mb-2">Your requests</div>
          <div className="space-y-2">
            {requests.map(r => (
              <div key={r.request_id} className="card p-3 flex items-center justify-between">
                <div>
                  <div className="text-sm text-slate-300">{r.resource_name}</div>
                  <div className="text-xs text-slate-600 mt-0.5">
                    {new Date(r.requested_at).toLocaleDateString()}
                    {r.duration_days && <span className="text-slate-500"> · {r.duration_days}d access</span>}
                    {r.rejection_reason && <span className="text-red-400"> · {r.rejection_reason}</span>}
                  </div>
                </div>
                <StatusBadge status={r.status} />
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
