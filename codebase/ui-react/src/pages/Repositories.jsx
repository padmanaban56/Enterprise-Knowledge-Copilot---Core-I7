import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronRight, FileText, Database, RefreshCw } from 'lucide-react'
import { api } from '../services/api'

function docDisplayName(doc) {
  return doc.source_file?.split('/').pop() || doc.title || 'Untitled'
}

const REPO_META = {
  HR:          { emoji: '👥', color: '#10b981', bg: '#10b98115', desc: 'Employee handbooks, leave & payroll policies' },
  Finance:     { emoji: '💰', color: '#f59e0b', bg: '#f59e0b15', desc: 'Invoice SOPs, budgets, expense procedures' },
  IT:          { emoji: '🖥️', color: '#3b82f6', bg: '#3b82f615', desc: 'Network runbooks, VPN guides, incident SOPs' },
  Engineering: { emoji: '⚙️', color: '#8b5cf6', bg: '#8b5cf615', desc: 'Architecture docs, APIs, deployment guides' },
  Projects:    { emoji: '📋', color: '#ec4899', bg: '#ec489915', desc: 'Charters, roadmaps, risk registers' },
  External:    { emoji: '🌐', color: '#64748b', bg: '#64748b15', desc: 'Vendor docs, Kubernetes, GitLab handbook' },
}

export default function Repositories() {
  const navigate = useNavigate()
  const [repos, setRepos] = useState([])
  const [selected, setSelected] = useState(null)
  const [docs, setDocs] = useState([])
  const [loading, setLoading] = useState(true)
  const [docsLoading, setDocsLoading] = useState(false)

  useEffect(() => {
    api.getRepositories()
      .then(r => setRepos(r.repositories || []))
      .finally(() => setLoading(false))
  }, [])

  const selectRepo = (name) => {
    setSelected(name)
    setDocsLoading(true)
    api.getRepoDocuments(name, 100)
      .then(r => setDocs(r.documents || []))
      .finally(() => setDocsLoading(false))
  }

  if (loading) return <div className="p-6 text-slate-500 text-sm">Loading…</div>

  const meta = selected ? (REPO_META[selected] || {}) : {}

  return (
    <div className="p-6 animate-fade-in">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-slate-100">Knowledge Repositories</h1>
        <p className="text-sm text-slate-500 mt-0.5">Browse documents organised by business domain</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Repo list */}
        <div className="space-y-2">
          {repos.map(repo => {
            const m = REPO_META[repo.name] || {}
            const isActive = selected === repo.name
            return (
              <button key={repo.name} onClick={() => selectRepo(repo.name)}
                className={`w-full text-left p-4 rounded-xl border transition-all duration-200 
                  ${isActive ? 'border-opacity-60 bg-opacity-10' : 'border-slate-700/50 bg-[#1a2233] hover:border-slate-600'}`}
                style={isActive ? { borderColor: m.color, background: m.bg } : {}}>
                <div className="flex items-center gap-3">
                  <div className="text-xl">{m.emoji || '📁'}</div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-slate-200">{repo.display_name}</div>
                    <div className="text-xs text-slate-500 truncate mt-0.5">{m.desc}</div>
                  </div>
                  <div className="text-right flex-shrink-0">
                    <div className="text-sm font-semibold text-slate-300">{repo.document_count}</div>
                    <div className="text-xs text-slate-600">docs</div>
                  </div>
                </div>
                <div className="flex gap-3 mt-3 text-xs text-slate-600">
                  <span>{(repo.chunk_count || 0).toLocaleString()} chunks</span>
                  {repo.last_updated && (
                    <span>Updated {new Date(repo.last_updated).toLocaleDateString()}</span>
                  )}
                </div>
              </button>
            )
          })}
        </div>

        {/* Documents panel */}
        <div className="lg:col-span-2">
          {!selected ? (
            <div className="card h-64 flex items-center justify-center">
              <div className="text-center text-slate-600">
                <Database size={32} className="mx-auto mb-3 opacity-40" />
                <div className="text-sm">Select a repository to browse documents</div>
              </div>
            </div>
          ) : (
            <div className="card overflow-hidden">
              <div className="px-5 py-4 border-b border-slate-700/50 flex items-center justify-between"
                style={{ borderLeftWidth: 3, borderLeftColor: meta.color }}>
                <div>
                  <div className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                    {meta.emoji} {selected} Repository
                  </div>
                  <div className="text-xs text-slate-500 mt-0.5">{docs.length} documents</div>
                </div>
                {docsLoading && <RefreshCw size={14} className="text-slate-500 animate-spin" />}
              </div>

              {docsLoading ? (
                <div className="p-8 text-center text-slate-600 text-sm">Loading documents…</div>
              ) : docs.length === 0 ? (
                <div className="p-8 text-center text-slate-600 text-sm">
                  No documents in this repository yet.<br />
                  <span className="text-slate-700">Upload files from the Admin page.</span>
                </div>
              ) : (
                <div className="divide-y divide-slate-700/30">
                  {docs.map((doc, i) => (
                    <div key={doc.doc_id || i}
                      onClick={() => doc.doc_id && navigate(`/documents/${doc.doc_id}`)}
                      className="px-5 py-3 hover:bg-slate-700/20 transition-colors cursor-pointer">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex items-start gap-3 min-w-0">
                          <FileText size={14} className="text-slate-500 mt-0.5 flex-shrink-0" />
                          <div className="min-w-0">
                            <div className="text-sm text-slate-200 truncate">{docDisplayName(doc)}</div>
                            <div className="text-xs text-slate-600 mt-0.5 truncate font-mono">{doc.doc_id}</div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 flex-shrink-0">
                          <span className="badge bg-slate-700/50 text-slate-400">{doc.doc_type}</span>
                          <span className="text-xs text-slate-600">{doc.chunk_count} chunks</span>
                        </div>
                      </div>
                      {doc.access_roles && doc.access_roles.length > 0 && (
                        <div className="flex gap-1 mt-2 ml-5">
                          {doc.access_roles.slice(0, 4).map(role => (
                            <span key={role} className="badge bg-brand-600/15 text-brand-400 text-[10px]">{role}</span>
                          ))}
                          {doc.access_roles.length > 4 && (
                            <span className="badge bg-slate-700/50 text-slate-500 text-[10px]">+{doc.access_roles.length - 4}</span>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
