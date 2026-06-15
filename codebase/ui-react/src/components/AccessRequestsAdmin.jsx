import { useState, useEffect } from 'react'
import { api } from '../services/api'
import { ShieldCheck, Check, X, Clock, AlertCircle } from 'lucide-react'

function StatusBadge({ status }) {
  const map = {
    pending: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
    approved: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20',
    rejected: 'bg-red-500/10 text-red-300 border-red-500/20',
  }
  return <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${map[status] || map.pending}`}>{status}</span>
}

export default function AccessRequestsAdmin() {
  const [requests, setRequests] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [msg, setMsg] = useState(null)
  const [rejectingId, setRejectingId] = useState(null)
  const [rejectReason, setRejectReason] = useState('')

  useEffect(() => { load() }, [])

  async function load() {
    setLoading(true)
    try { setRequests(await api.allAccessRequests()) }
    catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  function flash(m, isError = false) {
    if (isError) setError(m); else setMsg(m)
    setTimeout(() => { setMsg(null); setError(null) }, 4000)
  }

  async function handleApprove(id) {
    try {
      await api.resolveAccessRequest(id, { approve: true })
      flash('Access granted')
      load()
    } catch (e) { flash(e.message, true) }
  }

  async function handleReject(id) {
    try {
      await api.resolveAccessRequest(id, { approve: false, rejection_reason: rejectReason || null })
      flash('Request rejected')
      setRejectingId(null)
      setRejectReason('')
      load()
    } catch (e) { flash(e.message, true) }
  }

  if (loading) return <div className="text-slate-500 text-sm p-4">Loading access requests...</div>

  const pending = requests.filter(r => r.status === 'pending')
  const resolved = requests.filter(r => r.status !== 'pending')

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-slate-300 font-medium">
        <ShieldCheck size={16} /> {pending.length} pending request{pending.length !== 1 ? 's' : ''}
      </div>

      {msg && <div className="flex items-center gap-2 text-sm text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2"><Check size={14} />{msg}</div>}
      {error && <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2"><AlertCircle size={14} />{error}</div>}

      {pending.length === 0 ? (
        <div className="text-slate-600 text-sm text-center py-6">No pending access requests.</div>
      ) : (
        <div className="space-y-2">
          {pending.map(r => (
            <div key={r.request_id} className="card p-4 border border-amber-500/20">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <div className="text-sm font-medium text-slate-200">{r.user_name} <span className="text-slate-500">({r.user_email})</span></div>
                  <div className="text-xs text-slate-500 mt-0.5">
                    Requesting <span className="text-brand-400 font-medium">{r.resource_name}</span> access
                  </div>
                  {r.justification && <div className="text-xs text-slate-400 mt-2 italic">"{r.justification}"</div>}
                  <div className="text-xs text-slate-600 mt-1 flex items-center gap-1">
                    <Clock size={11} /> {new Date(r.requested_at).toLocaleString()}
                  </div>
                </div>
                <div className="flex flex-col gap-2 flex-shrink-0">
                  <button onClick={() => handleApprove(r.request_id)} className="btn-primary text-xs px-3 py-1.5">
                    <Check size={12} /> Approve
                  </button>
                  {rejectingId === r.request_id ? (
                    <div className="flex flex-col gap-1.5 w-44">
                      <input type="text" placeholder="Reason (optional)" value={rejectReason}
                        onChange={e => setRejectReason(e.target.value)}
                        className="bg-[#0f1520] border border-slate-700/50 rounded px-2 py-1 text-xs text-slate-200 focus:outline-none focus:ring-1 focus:ring-brand-500/50" />
                      <div className="flex gap-1.5">
                        <button onClick={() => handleReject(r.request_id)} className="btn-ghost text-xs px-2 py-1 flex-1">Confirm</button>
                        <button onClick={() => { setRejectingId(null); setRejectReason('') }} className="btn-ghost text-xs px-2 py-1">
                          <X size={12} />
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button onClick={() => setRejectingId(r.request_id)} className="btn-ghost text-xs px-3 py-1.5 text-red-400">
                      <X size={12} /> Reject
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {resolved.length > 0 && (
        <div className="mt-6">
          <div className="text-xs font-medium text-slate-500 mb-2">Resolved</div>
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700/50">
                  {['User', 'Resource', 'Status', 'Resolved By', 'Date'].map(h => (
                    <th key={h} className="text-left text-xs text-slate-500 font-medium px-4 py-2">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {resolved.slice(0, 20).map(r => (
                  <tr key={r.request_id} className="border-b border-slate-700/30">
                    <td className="px-4 py-2 text-slate-300">{r.user_name}</td>
                    <td className="px-4 py-2 text-slate-400">{r.resource_name}</td>
                    <td className="px-4 py-2"><StatusBadge status={r.status} /></td>
                    <td className="px-4 py-2 text-slate-500">{r.resolved_by_name || '-'}</td>
                    <td className="px-4 py-2 text-slate-600 text-xs">{r.resolved_at ? new Date(r.resolved_at).toLocaleDateString() : '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
