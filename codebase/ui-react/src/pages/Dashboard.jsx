import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Database, Ticket, FolderOpen, TrendingUp, Clock, ShieldCheck, BarChart3 } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts'
import { api } from '../services/api'
import { useAuth } from '../context/AuthContext'

const REPO_COLORS = {
  HR: '#10b981', Finance: '#f59e0b', IT: '#3b82f6',
  Engineering: '#8b5cf6', Projects: '#ec4899', External: '#64748b',
}

const REPO_ICONS = {
  HR: '👥', Finance: '💰', IT: '🖥️', Engineering: '⚙️', Projects: '📋', External: '🌐',
}

function StatCard({ icon: Icon, label, value, sub, color = 'brand' }) {
  const colorMap = {
    brand: 'text-brand-400', green: 'text-emerald-400',
    amber: 'text-amber-400', blue: 'text-blue-400', red: 'text-red-400',
  }
  return (
    <div className="stat-card">
      <div className={`${colorMap[color]} mb-2`}><Icon size={18} /></div>
      <div className="text-2xl font-bold text-slate-100">{value}</div>
      <div className="text-sm text-slate-400">{label}</div>
      {sub && <div className="text-xs text-slate-600 mt-1">{sub}</div>}
    </div>
  )
}

function ConfidenceBadge({ score }) {
  if (score >= 0.75) return <span className="badge bg-emerald-500/20 text-emerald-400">HIGH {(score*100).toFixed(0)}%</span>
  if (score >= 0.55) return <span className="badge bg-amber-500/20 text-amber-400">MED {(score*100).toFixed(0)}%</span>
  return <span className="badge bg-red-500/20 text-red-400">LOW {(score*100).toFixed(0)}%</span>
}

function getGreeting() {
  const h = new Date().getHours()
  if (h < 12) return 'Good morning'
  if (h < 17) return 'Good afternoon'
  return 'Good evening'
}

export default function Dashboard() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [metrics, setMetrics] = useState(null)
  const [repos, setRepos] = useState([])
  const [accessRequestCount, setAccessRequestCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [days, setDays] = useState(7)

  const ADMIN_ROLES = ['ADMIN', 'IT_ADMIN', 'EXECUTIVE']
  const userRoles = new Set([
    (user?.role || '').toUpperCase(),
    ...(user?.access_roles || []).map(r => r.toUpperCase()),
  ])
  const isAdmin = ADMIN_ROLES.some(r => userRoles.has(r))

  useEffect(() => {
    const fetches = [api.dashboardMetrics(days), api.getRepositories()]
    if (!isAdmin) fetches.push(api.myAccessRequests().catch(() => []))
    Promise.all(fetches)
      .then(([m, r, reqs]) => {
        setMetrics(m)
        setRepos(r.repositories || [])
        if (reqs) setAccessRequestCount(reqs.length || 0)
      })
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [days])

  if (loading) return (
    <div className="flex items-center justify-center h-full">
      <div className="text-slate-500 text-sm">Loading…</div>
    </div>
  )

  const intentData = metrics?.intent_distribution?.map(d => ({
    name: d.intent, value: d.count,
  })) || []

  const dailyData = metrics?.daily_queries?.map(d => ({
    day: new Date(d.day).toLocaleDateString('en', { month: 'short', day: 'numeric' }),
    queries: d.queries,
    confidence: +(d.avg_conf * 100).toFixed(1),
  })) || []

  // Repositories filtered by user access when not admin
  const visibleRepos = isAdmin
    ? repos
    : repos.filter(repo => {
        const userAccessRoles = (user?.access_roles || []).map(r => r.toUpperCase())
        return userAccessRoles.includes(repo.name?.toUpperCase()) || userAccessRoles.includes('EMPLOYEE')
      })

  return (
    <div className="p-6 space-y-6 animate-fade-in">
      {/* Warm greeting hero */}
      <div className="rounded-2xl overflow-hidden"
        style={{ background: 'linear-gradient(135deg, #1e40af 0%, #7c3aed 60%, #4f46e5 100%)' }}>
        <div className="px-7 py-8">
          <h1 className="text-2xl font-bold text-white mb-1">
            {getGreeting()}, {user?.username?.split(' ')[0] || 'there'}!
          </h1>
          <p className="text-blue-200 text-sm max-w-lg leading-relaxed">
            {isAdmin
              ? 'Here\'s an overview of your Enterprise Knowledge Platform. Manage documents, users, and analytics from here.'
              : 'Search across your company\'s knowledge base. Ask anything — policies, procedures, documentation.'}
          </p>
          <div className="mt-5 flex items-center gap-3">
            <button
              onClick={() => navigate('/chat')}
              className="px-5 py-2.5 bg-white text-indigo-700 font-semibold text-sm rounded-xl hover:bg-blue-50 transition-colors shadow-sm">
              Start Chatting
            </button>
            {!isAdmin && (
              <button
                onClick={() => navigate('/repositories')}
                className="px-5 py-2.5 bg-white/15 text-white font-medium text-sm rounded-xl hover:bg-white/25 transition-colors border border-white/20">
                Browse Repositories
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Period selector */}
      <div className="flex items-center justify-between">
        <div className="text-sm font-medium text-slate-400">Overview</div>
        <div className="flex gap-1">
          {[7, 14, 30].map(d => (
            <button key={d} onClick={() => setDays(d)}
              className={`px-3 py-1.5 text-xs rounded-lg font-medium transition-colors ${days === d ? 'bg-brand-600 text-white' : 'text-slate-400 hover:text-slate-100 bg-slate-800'}`}>
              {d}d
            </button>
          ))}
        </div>
      </div>

      {/* KPI row 1 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={FileText} label="Documents Indexed" value={metrics?.total_documents ?? '—'} color="brand" />
        <StatCard icon={Database} label="Chunks Indexed" value={(metrics?.total_chunks ?? 0).toLocaleString()} color="blue" />
        <StatCard icon={Ticket} label="Tickets Indexed" value={(metrics?.total_tickets ?? 0).toLocaleString()} color="amber" />
        <StatCard icon={FolderOpen} label="Repositories" value={metrics?.total_repositories ?? 6} color="green" />
      </div>

      {/* KPI row 2 — admin sees all-user stats; regular users see their personal stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard icon={TrendingUp} label={isAdmin ? 'Total Queries' : 'My Queries'} value={metrics?.total_queries ?? 0} sub={`Last ${days} days`} />
        <StatCard icon={BarChart3} label="Avg Confidence"
          value={`${((metrics?.avg_confidence ?? 0) * 100).toFixed(0)}%`}
          sub={metrics?.avg_confidence >= 0.65 ? '🟢 Good' : '🟡 Review'} />
        <StatCard icon={Clock} label="Avg Latency" value={`${metrics?.avg_latency_ms ?? 0}ms`} color="amber" />
        {isAdmin ? (
          <StatCard icon={ShieldCheck} label="Access Requests"
            value={metrics?.access_request_count ?? '—'}
            color={metrics?.access_request_count > 0 ? 'amber' : 'green'} />
        ) : (
          <StatCard icon={ShieldCheck} label="My Access Requests"
            value={accessRequestCount}
            color={accessRequestCount > 0 ? 'amber' : 'green'} />
        )}
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="card p-5">
          <div className="text-sm font-medium text-slate-300 mb-4">Query Volume (last {days} days)</div>
          {dailyData.length > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={dailyData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                <XAxis dataKey="day" tick={{ fontSize: 11, fill: '#64748b' }} />
                <YAxis tick={{ fontSize: 11, fill: '#64748b' }} />
                <Tooltip contentStyle={{ background: '#1a2233', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="queries" fill="#6366f1" radius={[3, 3, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : <div className="h-48 flex items-center justify-center text-slate-600 text-sm">No query data yet</div>}
        </div>

        <div className="card p-5">
          <div className="text-sm font-medium text-slate-300 mb-4">Query Intent Distribution</div>
          {intentData.length > 0 ? (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width="50%" height={180}>
                <PieChart>
                  <Pie data={intentData} cx="50%" cy="50%" innerRadius={50} outerRadius={80} paddingAngle={3} dataKey="value">
                    {intentData.map((_, i) => (
                      <Cell key={i} fill={['#6366f1','#10b981','#f59e0b','#3b82f6','#ec4899'][i % 5]} />
                    ))}
                  </Pie>
                  <Tooltip contentStyle={{ background: '#1a2233', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }} />
                </PieChart>
              </ResponsiveContainer>
              <div className="space-y-2">
                {intentData.map((d, i) => (
                  <div key={d.name} className="flex items-center gap-2 text-sm">
                    <div className="w-2.5 h-2.5 rounded-full flex-shrink-0"
                      style={{ background: ['#6366f1','#10b981','#f59e0b','#3b82f6','#ec4899'][i % 5] }} />
                    <span className="text-slate-400">{d.name}</span>
                    <span className="text-slate-300 font-medium ml-auto">{d.value}</span>
                  </div>
                ))}
              </div>
            </div>
          ) : <div className="h-48 flex items-center justify-center text-slate-600 text-sm">No intent data yet</div>}
        </div>
      </div>

      {/* Repository cards — filtered for non-admins */}
      <div>
        <div className="text-sm font-medium text-slate-300 mb-3">
          Knowledge Repositories {!isAdmin && <span className="text-slate-600 font-normal">— your accessible repos</span>}
        </div>
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
          {(isAdmin ? repos : visibleRepos).map(repo => (
            <div key={repo.name} className="card-hover p-4 cursor-pointer"
              onClick={() => navigate(`/repositories`)}>
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center text-base"
                    style={{ background: `${REPO_COLORS[repo.name]}20` }}>
                    {REPO_ICONS[repo.name] || '📁'}
                  </div>
                  <div>
                    <div className="text-sm font-medium text-slate-200">{repo.display_name}</div>
                    <div className="text-xs text-slate-600">{repo.name}</div>
                  </div>
                </div>
                <div className="w-2 h-2 rounded-full mt-1 flex-shrink-0"
                  style={{ background: REPO_COLORS[repo.name] || '#6366f1' }} />
              </div>
              <div className="flex gap-4 text-xs text-slate-500">
                <span><span className="text-slate-300 font-medium">{repo.document_count}</span> docs</span>
                <span><span className="text-slate-300 font-medium">{(repo.chunk_count || 0).toLocaleString()}</span> chunks</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Recent queries */}
      {metrics?.recent_queries?.length > 0 && (
        <div>
          <div className="text-sm font-medium text-slate-300 mb-3">
            {isAdmin ? 'Recent Queries (All Users)' : 'My Recent Queries'}
          </div>
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-700/50 text-xs text-slate-500">
                  <th className="px-4 py-3 text-left font-medium">Query</th>
                  <th className="px-4 py-3 text-left font-medium">Confidence</th>
                  <th className="px-4 py-3 text-left font-medium">Latency</th>
                  <th className="px-4 py-3 text-left font-medium">Time</th>
                </tr>
              </thead>
              <tbody>
                {metrics.recent_queries.slice(0, 8).map((q, i) => (
                  <tr key={i}
                    onClick={() => q.session_id && navigate(`/chat?session=${q.session_id}`)}
                    className="border-b border-slate-700/30 hover:bg-slate-700/20 cursor-pointer transition-colors">
                    <td className="px-4 py-3 text-slate-300 truncate max-w-xs">{q.query_text}</td>
                    <td className="px-4 py-3"><ConfidenceBadge score={q.confidence || 0} /></td>
                    <td className="px-4 py-3 text-slate-500">{q.latency_ms}ms</td>
                    <td className="px-4 py-3 text-slate-600 text-xs">
                      {new Date(q.timestamp).toLocaleTimeString()}
                    </td>
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
