import { useState, useEffect, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Search,
  Ticket,
  AlertCircle,
  CheckCircle,
  ChevronDown,
  ChevronUp
} from 'lucide-react'
import { api } from '../services/api'

const PRIORITY_STYLES = {
  High: 'bg-red-500/20 text-red-400',
  Medium: 'bg-amber-500/20 text-amber-400',
  Low: 'bg-emerald-500/20 text-emerald-400',
}

const STATUS_STYLES = {
  open: 'bg-blue-500/20 text-blue-400',
  resolved: 'bg-emerald-500/20 text-emerald-400',
  closed: 'bg-slate-600/50 text-slate-400',
  rejected: 'bg-red-500/20 text-red-400',
}

function KnownIssueCard({ issue }) {
  const [open, setOpen] = useState(false)
  if (!issue?.found) return null
  return (
    <div className="card p-4 border-l-4 border-amber-500/60 bg-amber-500/5 mb-4">
      <div className="flex items-start justify-between cursor-pointer" onClick={() => setOpen(o => !o)}>
        <div className="flex items-center gap-2">
          <AlertCircle size={15} className="text-amber-400 flex-shrink-0" />
          <div>
            <div className="text-sm font-medium text-amber-300">Known Issue Pattern Detected</div>
            <div className="text-xs text-slate-500 mt-0.5">
              {issue.ticket_count} similar tickets · {(issue.resolution_rate * 100).toFixed(0)}% resolved
            </div>
          </div>
        </div>
        {open ? <ChevronUp size={14} className="text-slate-500" /> : <ChevronDown size={14} className="text-slate-500" />}
      </div>
      {open && issue.common_resolution && (
        <div className="mt-3 pt-3 border-t border-amber-500/20">
          <div className="text-xs text-slate-400 font-medium mb-1">Most Common Resolution:</div>
          <div className="text-sm text-slate-300 bg-slate-800/60 rounded-lg p-3 leading-relaxed">
            {issue.common_resolution}
          </div>
          {issue.categories && Object.keys(issue.categories).length > 0 && (
            <div className="flex gap-2 mt-2 flex-wrap">
              {Object.entries(issue.categories).slice(0, 4).map(([cat, n]) => (
                <span key={cat} className="badge bg-slate-700/60 text-slate-400 text-xs">{cat} ({n})</span>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Tickets() {
  const [searchParams] = useSearchParams()
  const [query, setQuery] = useState(() => searchParams.get('search') || '')
  const [results, setResults] = useState(null)
  const [recent, setRecent] = useState([])
  const [categories, setCategories] = useState([])
  const [searching, setSearching] = useState(false)
  const [catFilter, setCatFilter] = useState('')
  const [priFilter, setPriFilter] = useState('')

  // NEW: expand/collapse state
  const [expanded, setExpanded] = useState({})

  const toggle = (id) => {
    setExpanded(prev => ({
      ...prev,
      [id]: !prev[id]
    }))
  }

  useEffect(() => {
    api.recentTickets(15).then(r => setRecent(r.tickets || []))
    api.ticketCategories().then(r => setCategories(r.categories || []))
  }, [])

  const handleSearch = useCallback(async () => {
    if (!query.trim()) return
    setSearching(true)
    try {
      const r = await api.searchTickets(
        query,
        catFilter || undefined,
        priFilter || undefined,
        15
      )
      setResults(r)
    } finally {
      setSearching(false)
    }
  }, [query, catFilter, priFilter])

  useEffect(() => {
    if (searchParams.get('search')) handleSearch()
  }, [])

  const tickets = results ? results.tickets : recent

  return (
    <div className="p-6 animate-fade-in">

      {/* HEADER */}
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-slate-100">
          Ticket Intelligence
        </h1>
        <p className="text-sm text-slate-500">
          Search support tickets and resolutions
        </p>
      </div>

      {/* Category stats */}
      {categories.length > 0 && (
        <div className="grid grid-cols-3 lg:grid-cols-6 gap-2 mb-5">
          {categories.slice(0, 6).map(cat => (
            <button key={cat.category}
              onClick={() => { setCatFilter(cat.category === catFilter ? '' : cat.category) }}
              className={`card-hover p-3 text-center transition-all ${catFilter === cat.category ? 'border-brand-500/60 bg-brand-600/10' : ''}`}>
              <div className="text-base font-bold text-slate-200">{cat.total}</div>
              <div className="text-xs text-slate-500 truncate">{cat.category}</div>
              <div className="text-xs text-emerald-500 mt-0.5">{(cat.resolved / (cat.total || 1) * 100).toFixed(0)}% resolved</div>
            </button>
          ))}
        </div>
      )}

      {/* SEARCH */}
      <div className="flex gap-2 mb-5">

        <div className="relative flex-1">
          <Search size={14} className="absolute left-3 top-3 text-slate-500" />

          <input
            className="input pl-9"
            placeholder="Search tickets..."
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
          />
        </div>

        <select
          className="input w-40"
          value={catFilter}
          onChange={e => setCatFilter(e.target.value)}
        >
          <option value="">All Departments</option>

          {categories.map(c => (
            <option key={c.category} value={c.category}>
              {c.category}
            </option>
          ))}
        </select>

        <select
          className="input w-36"
          value={priFilter}
          onChange={e => setPriFilter(e.target.value)}
        >
          <option value="">All Priority</option>
          <option value="High">High</option>
          <option value="Medium">Medium</option>
          <option value="Low">Low</option>
        </select>

        <button
          className="btn-primary"
          onClick={handleSearch}
          disabled={searching}
        >
          {searching ? 'Searching...' : 'Search'}
        </button>

      </div>

      {/* RESULTS */}
      <div className="card overflow-hidden">

        <div className="px-4 py-3 border-b border-slate-700 text-xs text-slate-400">
          {tickets.length} tickets
        </div>

        {tickets.length === 0 ? (
          <div className="p-10 text-center text-slate-600">
            No tickets found
          </div>
        ) : (
          <div className="divide-y divide-slate-700/20">
            {tickets.map((t) => {
              const isOpen = expanded[t.ticket_id]
              return (
                <div
                  key={t.ticket_id}
                  className="px-4 py-4 hover:bg-slate-700/10 transition-colors"
                >
                  <div className="flex justify-between items-start">

                  <div className="text-sm font-semibold text-slate-100 truncate">
                      {t.subject}
                    </div>

                    <span
                      className={`badge ${
                        STATUS_STYLES[t.status] ||
                        'bg-slate-700 text-slate-400'
                      }`}
                    >
                      {t.status}
                    </span>

                  </div>

                  <div className="mt-2 flex items-center justify-between gap-4">

                  <div className="flex items-center gap-5 min-w-0 flex-1">

                    <span className="font-mono text-xs text-slate-500">
                      {t.ticket_id}
                    </span>

                    <span className="text-xs text-slate-400 truncate">
                      <span className="text-emerald-400">
                        Resolution:
                      </span>{" "}
                      {t.resolution || "No resolution available"}
                    </span>

                  </div>

                  <span className="badge bg-slate-700/50 text-slate-400 flex-shrink-0">
                    {t.category}
                  </span>

                </div>

                  {/* DETAILS BUTTON */}
                  <button
                    onClick={() => toggle(t.ticket_id)}
                    className="mt-3 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300"
                  >
                    {isOpen ? (
                      <>
                        <ChevronUp size={12} />
                        Details
                      </>
                    ) : (
                      <>
                        <ChevronDown size={12} />
                        Details
                      </>
                    )}
                  </button>

                  {/* EXPANDED */}
                  {isOpen && (
                    <div className="mt-3 p-4 rounded-lg border border-slate-700 bg-slate-800/30">

                      <div className="mb-4">
                        <div className="text-xs text-slate-500 mb-1">
                          Description
                        </div>

                        <div className="text-sm text-slate-300">
                          {t.description || "No description available"}
                        </div>
                      </div>

                      <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm">

                        <div>
                          <span className="text-slate-500">
                            Requester:
                          </span>{" "}
                          <span className="text-slate-300">
                            {t.requester_email || "N/A"}
                          </span>
                        </div>

                        <div>
                          <span className="text-slate-500">
                            Priority:
                          </span>{" "}
                          <span className="text-slate-300">
                            {t.priority || "N/A"}
                          </span>
                        </div>

                        <div>
                          <span className="text-slate-500">
                            Created:
                          </span>{" "}
                          <span className="text-slate-300">
                            {t.created_at || "N/A"}
                          </span>
                        </div>

                        <div>
                          <span className="text-slate-500">
                            Department:
                          </span>{" "}
                          <span className="text-slate-300">
                            {t.category || "N/A"}
                          </span>
                        </div>

                        <div>
                          <span className="text-slate-500">
                            Resolved:
                          </span>{" "}
                          <span className="text-slate-300">
                            {t.resolved_at || "N/A"}
                          </span>
                        </div>

                        <div>
                          <span className="text-slate-500">
                            Status:
                          </span>{" "}
                          <span className="text-slate-300">
                            {t.status || "N/A"}
                          </span>
                        </div>

                      </div>

                      <div className="mt-4">
                        <div className="text-xs text-slate-500 mb-1">
                          Full Resolution
                        </div>

                        <div className="text-sm text-emerald-300">
                          {t.resolution || "No resolution available"}
                        </div>
                      </div>

                    </div>
                  )}

                </div>
              )
            })}

          
          </div>
        )}
      </div>
    </div>
  )
}