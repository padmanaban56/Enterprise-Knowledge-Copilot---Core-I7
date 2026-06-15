import { useState, useEffect, useRef } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  Search,
  FileText,
  Package,
  X,
  Check,
  AlertCircle,
  ChevronDown
} from 'lucide-react'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'

const REPO_COLORS = {
  HR: '#10b981',
  Finance: '#f59e0b',
  IT: '#3b82f6',
  Engineering: '#8b5cf6',
  Projects: '#ec4899',
  External: '#64748b'
}

const DOC_TYPES = ['All','SOP','Policy','Runbook','Guide','Presentation','Ticket']
const REPOS = ['All','HR','Finance','IT','Engineering','Projects','External']
const ORIGIN_OPTIONS = ['INTERNAL', 'EXTERNAL']
const ACCESS_ROLE_OPTIONS = ['EMPLOYEE','MANAGER','HR','FINANCE','IT_ADMIN','EXECUTIVE']
const ADMIN_ROLES = ['ADMIN','IT_ADMIN','EXECUTIVE']

function docDisplayName(doc) {
  return doc.source_file?.split('/').pop() || doc.title || 'Untitled'
}

/* ================= CUSTOM DROPDOWN ================= */
function Dropdown({ value, options, onChange, width = "w-40" }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  return (
    <div className={`relative ${width}`} ref={ref} style={{ zIndex: open ? 9999 : 1 }}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-3 py-2 rounded-lg border border-slate-700 bg-slate-900 text-slate-200 hover:border-brand-500/50"
      >
        <span className="truncate text-sm">{value}</span>
        <ChevronDown size={14} />
      </button>

      {open && (
        <div className="absolute z-[9999] mt-1 w-full bg-[#0f172a] border border-slate-700 rounded-lg shadow-xl overflow-hidden">
          {options.map(opt => (
            <button
              key={opt}
              onClick={() => {
                onChange(opt)
                setOpen(false)
              }}
              className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-700/50"
            >
              {opt}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/* ================= ACCESS ROLES ================= */
function AccessRolesEditor({ value, onChange }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  const roles = value || []

  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const toggle = (r) => {
    const next = roles.includes(r)
      ? roles.filter(x => x !== r)
      : [...roles, r]
    onChange(next)
  }

  return (
    <div className="relative" ref={ref}>
      <button
        onClick={() => setOpen(!open)}
        className="flex gap-1 flex-wrap text-xs text-slate-300"
      >
        {roles.length === 0 && <span className="text-slate-500">none</span>}
        {roles.slice(0,2).map(r => (
          <span key={r} className="badge bg-brand-600/20 text-brand-300">{r}</span>
        ))}
        {roles.length > 2 && <span className="badge">+{roles.length-2}</span>}
      </button>

      {open && (
        <div className="absolute z-[9999] mt-1 w-40 bg-slate-900 border border-slate-700 rounded-lg p-2">
          {ACCESS_ROLE_OPTIONS.map(r => (
            <label key={r} className="flex items-center gap-2 text-xs p-1 hover:bg-slate-800 rounded">
              <input type="checkbox" checked={roles.includes(r)} onChange={() => toggle(r)} />
              {r}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

/* ================= MAIN ================= */
export default function Documents() {
  const navigate = useNavigate()
  const { user } = useAuth()

  const [docs, setDocs] = useState([])
  const [filtered, setFiltered] = useState([])
  const [loading, setLoading] = useState(true)

  const [searchParams] = useSearchParams()
  const [search, setSearch] = useState(() => searchParams.get('search') || '')

  const [repoFilter, setRepoFilter] = useState('All')
  const [typeFilter, setTypeFilter] = useState('All')

  const [repos, setRepos] = useState([])

  const isAdmin = (user?.role || '').toUpperCase() === 'ADMIN'

  useEffect(() => {
    api.getDocuments(null, 200)
      .then(r => {
        setDocs(r.documents || [])
        setFiltered(r.documents || [])
      })
      .finally(() => setLoading(false))

    api.getRepositories()
      .then(r => setRepos(r.repositories || []))
  }, [])

  useEffect(() => {
    let res = docs

    if (search) {
      res = res.filter(d =>
        d.source_file?.toLowerCase().includes(search.toLowerCase())
      )
    }

    if (repoFilter !== 'All') res = res.filter(d => d.repository === repoFilter)
    if (typeFilter !== 'All') res = res.filter(d => d.doc_type === typeFilter)

    setFiltered(res)
  }, [search, repoFilter, typeFilter, docs])

  function updateDoc(id, patch) {
    setDocs(prev => prev.map(d => d.doc_id === id ? { ...d, ...patch } : d))
    api.updateDocument(id, patch)
  }

  return (
    <div className="p-6">

      {/* HEADER */}
      <h1 className="text-xl text-slate-100">Documents</h1>
      <p className="text-xs text-slate-500 mb-4">{docs.length} documents</p>

      {/* FILTERS */}
      <div className="flex gap-3 mb-5">
        <input
          className="input flex-1"
          placeholder="Search documents..."
          value={search}
          onChange={e => setSearch(e.target.value)}
        />

        <Dropdown
          value={repoFilter}
          options={REPOS}
          onChange={setRepoFilter}
        />

        <Dropdown
          value={typeFilter}
          options={DOC_TYPES}
          onChange={setTypeFilter}
          width="w-36"
        />
      </div>

      {/* TABLE */}
      <div className="card overflow-visible relative">
        <table className="w-full text-sm relative">

          <thead className="text-xs text-slate-500">
            <tr>
              <th className="p-3 text-left">Document</th>
              <th>Repository</th>
              <th>Type</th>
              <th>Origin</th>
              <th>Access</th>
            </tr>
          </thead>

          <tbody>
            {filtered.map(doc => (
              <tr key={doc.doc_id} className="border-t border-slate-800">

                {/* NAME */}
                <td className="p-3">
                  <div className="text-slate-200">{docDisplayName(doc)}</div>
                  <div className="text-xs text-slate-500">{doc.doc_id}</div>
                </td>

                {/* REPO */}
                <td>
                  {isAdmin ? (
                    <Dropdown
                      value={doc.repository}
                      options={REPOS.filter(r => r !== 'All')}
                      onChange={v => updateDoc(doc.doc_id, { repository: v })}
                      width="w-28"
                    />
                  ) : doc.repository}
                </td>

                {/* TYPE */}
                <td className="text-slate-400">{doc.doc_type}</td>

                {/* ORIGIN */}
                <td>
                  {isAdmin ? (
                    <Dropdown
                      value={doc.doc_origin}
                      options={ORIGIN_OPTIONS}
                      onChange={v => updateDoc(doc.doc_id, { doc_origin: v })}
                      width="w-28"
                    />
                  ) : doc.doc_origin}
                </td>

                {/* ACCESS */}
                <td>
                  {isAdmin ? (
                    <AccessRolesEditor
                      value={doc.access_roles}
                      onChange={roles => updateDoc(doc.doc_id, { access_roles: roles })}
                    />
                  ) : (
                    (doc.access_roles || []).join(', ')
                  )}
                </td>

              </tr>
            ))}
          </tbody>

        </table>
      </div>

    </div>
  )
}