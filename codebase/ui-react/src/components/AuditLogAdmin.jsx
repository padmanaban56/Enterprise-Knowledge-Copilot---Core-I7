import { useState, useEffect } from 'react'
import { api } from '../services/api'
import { Activity, Search, ShieldAlert, LogIn, MessageSquare, AlertTriangle } from 'lucide-react'

const ACTION_ICONS = {
  query: MessageSquare,
  low_confidence: AlertTriangle,
  login: LogIn,
  access_request: ShieldAlert,
}

const ACTIONS = ['all', 'query', 'low_confidence', 'login', 'access_request']

export default function AuditLogAdmin() {
  const [logs, setLogs] = useState([])
  const [metrics, setMetrics] = useState(null)
  const [loading, setLoading] = useState(true)
  const [actionFilter, setActionFilter] = useState('all')
  const [emailFilter, setEmailFilter] = useState('')

  useEffect(() => { load() }, [actionFilter])

  async function load() {
    setLoading(true)
    try {
      const [logsRes, metricsRes] = await Promise.all([
        api.auditLogs({ action: actionFilter, user_email: emailFilter, limit: 50 }),
        api.auditMetrics(7),
      ])
      setLogs(logsRes)
      setMetrics(metricsRes)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  function handleSearch(e) {
    e.preventDefault()
    load()
  }

  if (loading && !metrics) return <div className="text-slate-500 text-sm p-4">Loading audit log...</div>

  return (
    <div className="space-y-4">
      {/* Metrics */}
      {metrics && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {[
            { label: 'Queries (7d)', value: metrics.total_queries },
            { label: 'Logins (7d)', value: metrics.logins },
            { label: 'Access Requests (7d)', value: metrics.access_requests },
            { label: 'Low Confidence (7d)', value: metrics.low_confidence_events },
          ].map(m => (
            <div key={m.label} className="card p-4">
              <div className="text-2xl font-semibold text-slate-100">{m.value}</div>
              <div className="text-xs text-slate-500 mt-1">{m.label}</div>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <form onSubmit={handleSearch} className="flex items-center gap-2">
        <Activity size={14} className="text-slate-500" />
        <select value={actionFilter} onChange={e => setActionFilter(e.target.value)}
          className="bg-[#0f1520] border border-slate-700/50 rounded-lg px-2 py-1.5 text-xs text-slate-300 focus:outline-none">
          {ACTIONS.map(a => <option key={a} value={a}>{a === 'all' ? 'All actions' : a.replace('_', ' ')}</option>)}
        </select>
        <div className="relative flex-1 max-w-xs">
          <Search size={12} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-500" />
          <input type="text" placeholder="Filter by user email" value={emailFilter}
            onChange={e => setEmailFilter(e.target.value)}
            className="w-full bg-[#0f1520] border border-slate-700/50 rounded-lg pl-7 pr-2 py-1.5 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-brand-500/50" />
        </div>
        <button type="submit" className="btn-ghost text-xs">Search</button>
      </form>

      {/* Table */}
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700/50">
              {['Action', 'User', 'Detail', 'Confidence', 'Latency', 'Time'].map(h => (
                <th key={h} className="text-left text-xs text-slate-500 font-medium px-4 py-2">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {logs.length === 0 ? (
              <tr><td colSpan={6} className="px-4 py-6 text-center text-slate-600 text-sm">No audit events found.</td></tr>
            ) : logs.map(l => {
              const Icon = ACTION_ICONS[l.action] || Activity
              return (
                <tr key={l.log_id} className="border-b border-slate-700/30 hover:bg-slate-800/20">
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-1.5 text-xs text-slate-300">
                      <Icon size={12} className={l.action === 'low_confidence' ? 'text-amber-400' : 'text-slate-500'} />
                      {l.action.replace('_', ' ')}
                    </div>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-slate-400">{l.user_email || 'anonymous'}</td>
                  <td className="px-4 py-2.5 text-xs text-slate-400 truncate max-w-xs">{l.query_text || '-'}</td>
                  <td className="px-4 py-2.5 text-xs text-slate-500">{l.confidence != null ? `${Math.round(l.confidence * 100)}%` : '-'}</td>
                  <td className="px-4 py-2.5 text-xs text-slate-500">{l.latency_ms ? `${l.latency_ms}ms` : '-'}</td>
                  <td className="px-4 py-2.5 text-xs text-slate-600">{new Date(l.created_at).toLocaleString()}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
