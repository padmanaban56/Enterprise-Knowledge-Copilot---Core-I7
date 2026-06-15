import { useState, useEffect } from 'react'
import { Upload, FileText, Ticket, RefreshCw, Check, X, BarChart3, Target, Clock, TrendingUp, AlertTriangle, CheckCircle, Lightbulb, Users, Trash2, AlertOctagon, XCircle, Loader2 } from 'lucide-react'
import { api } from '../services/api'
import UsersAdmin from '../components/UsersAdmin'
import AccessRequestsAdmin from '../components/AccessRequestsAdmin'
import AuditLogAdmin from '../components/AuditLogAdmin'

const REPOS = ['HR','Finance','IT','Engineering','Projects','External']
const ROLES_LIST = ['EMPLOYEE','MANAGER','HR','FINANCE','IT_ADMIN','EXECUTIVE']

function EvalMetrics({ metrics }) {
  if (!metrics || metrics.error) return (
    <div className="text-slate-600 text-sm text-center py-6">{metrics?.error || 'No evaluation data yet. Run some queries first.'}</div>
  )
  const bars = [
    { label: 'High (≥75%)', count: metrics.confidence_distribution?.high_gte_075 || 0, color: '#10b981' },
    { label: 'Medium (55-75%)', count: metrics.confidence_distribution?.medium_055_075 || 0, color: '#f59e0b' },
    { label: 'Low (35-55%)', count: metrics.confidence_distribution?.low_035_055 || 0, color: '#ef4444' },
    { label: 'Very Low (<35%)', count: metrics.confidence_distribution?.very_low_lt_035 || 0, color: '#991b1b' },
  ]
  const total = bars.reduce((s, b) => s + b.count, 0) || 1
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: 'Precision@5', value: metrics.precision_at_5 != null ? `${(metrics.precision_at_5*100).toFixed(0)}%` : '—', icon: Target, note: '≥80% target' },
          { label: 'Recall@5',    value: metrics.recall_at_5 != null ? `${(metrics.recall_at_5*100).toFixed(0)}%` : '—', icon: BarChart3, note: '≥80% target' },
          { label: 'MRR',         value: metrics.mrr != null ? metrics.mrr.toFixed(3) : '—', icon: TrendingUp, note: 'Mean Reciprocal Rank' },
          { label: 'Hit Rate',    value: metrics.hit_rate != null ? `${(metrics.hit_rate*100).toFixed(0)}%` : '—', icon: CheckCircle, note: '≥1 relevant in top-5' },
        ].map(({ label, value, icon: Icon, note }) => (
          <div key={label} className="card p-4">
            <Icon size={14} className="text-brand-400 mb-2" />
            <div className="text-xl font-bold text-slate-100">{value}</div>
            <div className="text-xs text-slate-500">{label}</div>
            <div className="text-xs text-slate-700 mt-0.5">{note}</div>
          </div>
        ))}
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        <div className="card p-4">
          <Clock size={14} className="text-amber-400 mb-2" />
          <div className="text-xl font-bold text-slate-100">{metrics.avg_latency_ms || 0}ms</div>
          <div className="text-xs text-slate-500">Avg Latency</div>
          <div className="text-xs text-slate-600 mt-0.5">P95: {metrics.p95_latency_ms||0}ms · P99: {metrics.p99_latency_ms||0}ms</div>
        </div>
        <div className="card p-4">
          <BarChart3 size={14} className="text-blue-400 mb-2" />
          <div className="text-xl font-bold text-slate-100">{metrics.total_evaluated || 0}</div>
          <div className="text-xs text-slate-500">Queries Evaluated</div>
          <div className="text-xs text-slate-600 mt-0.5">Low conf: {((metrics.low_confidence_rate||0)*100).toFixed(0)}%</div>
        </div>
        <div className="card p-4">
          <AlertTriangle size={14} className="text-amber-400 mb-2" />
          <div className="text-xl font-bold text-slate-100">{metrics.knowledge_gap_count || 0}</div>
          <div className="text-xs text-slate-500">Knowledge Gaps</div>
          <div className="text-xs text-slate-600 mt-0.5">Unresolved gaps</div>
        </div>
      </div>
      <div>
        <div className="text-xs text-slate-500 mb-2">Confidence Distribution (reranker proxy for relevance)</div>
        {bars.map(b => (
          <div key={b.label} className="flex items-center gap-3 mb-1.5">
            <div className="text-xs text-slate-500 w-28 flex-shrink-0">{b.label}</div>
            <div className="flex-1 bg-slate-800 rounded-full h-2">
              <div className="h-2 rounded-full transition-all" style={{ width: `${(b.count/total)*100}%`, background: b.color }} />
            </div>
            <div className="text-xs text-slate-400 w-8 text-right">{b.count}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function KnowledgeGapsPanel({ gaps, onResolve }) {
  if (!gaps?.length) return (
    <div className="text-slate-600 text-sm text-center py-6">
      <Lightbulb size={20} className="mx-auto mb-2 opacity-40" />
      No knowledge gaps detected. Your corpus is covering queries well.
    </div>
  )
  return (
    <div className="space-y-2 max-h-64 overflow-y-auto">
      {gaps.map(gap => (
        <div key={gap.gap_id} className="flex items-start justify-between gap-3 p-3 bg-slate-800/50 rounded-lg">
          <div className="min-w-0 flex-1">
            <div className="text-sm text-slate-300 truncate">{gap.query_text}</div>
            <div className="flex gap-2 mt-1 text-xs text-slate-600">
              <span>{gap.intent}</span>
              <span>·</span>
              <span className="text-amber-500">×{gap.frequency} times</span>
              {gap.repositories_searched?.length > 0 && (
                <span>· {gap.repositories_searched.join(', ')}</span>
              )}
              <span>· {new Date(gap.last_seen).toLocaleDateString()}</span>
            </div>
          </div>
          <button onClick={() => onResolve(gap.gap_id)}
            className="text-xs text-emerald-400 hover:text-emerald-300 flex-shrink-0 px-2 py-1 border border-emerald-500/30 rounded-lg">
            Resolve
          </button>
        </div>
      ))}
    </div>
  )
}

// P10: ingestion job stages -> human-readable labels
const STAGE_LABELS = {
  queued: 'Queued',
  saving_upload: 'Saving upload',
  parsing_document: 'Parsing document',
  classifying: 'Classifying',
  chunking: 'Chunking',
  enriching_chunks: 'Enriching chunks',
  extracting_images: 'Extracting images',
  redacting_pii: 'Auditing PII',
  indexing: 'Indexing',
  finalizing: 'Finalizing',
  completed: 'Completed',
}
const formatStage = (stage) => STAGE_LABELS[stage] || (stage || '').replace(/_/g, ' ')

const TERMINAL_JOB_STATUSES = ['completed', 'failed', 'cancelled']

function UploadJobRow({ job, onCancel }) {
  const isActive = !TERMINAL_JOB_STATUSES.includes(job.status) && job.status !== 'rejected'
  const isCancelling = job.status === 'cancelling'

  let icon, barColor, textColor
  if (job.status === 'completed') { icon = <Check size={14}/>; barColor = 'bg-emerald-500'; textColor = 'text-emerald-400' }
  else if (job.status === 'failed' || job.status === 'rejected') { icon = <X size={14}/>; barColor = 'bg-red-500'; textColor = 'text-red-400' }
  else if (job.status === 'cancelled') { icon = <XCircle size={14}/>; barColor = 'bg-slate-500'; textColor = 'text-slate-500' }
  else if (isCancelling) { icon = <Loader2 size={14} className="animate-spin"/>; barColor = 'bg-amber-500'; textColor = 'text-amber-400' }
  else { icon = <Loader2 size={14} className="animate-spin"/>; barColor = 'bg-brand-500'; textColor = 'text-brand-300' }

  return (
    <div className="p-3 bg-slate-800/50 rounded-lg">
      <div className="flex items-center justify-between gap-3 mb-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className={textColor}>{icon}</span>
          <span className="text-sm text-slate-300 truncate">{job.filename}</span>
        </div>
        {isActive ? (
          <button
            onClick={() => onCancel(job.job_id)}
            disabled={isCancelling}
            className="text-xs text-red-400 hover:text-red-300 flex-shrink-0 px-2 py-1 border border-red-500/30 rounded-lg disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1"
          >
            <XCircle size={12}/> {isCancelling ? 'Cancelling…' : 'Cancel'}
          </button>
        ) : (
          <span className={`text-xs ${textColor} flex-shrink-0 capitalize`}>{job.status}</span>
        )}
      </div>

      {job.status !== 'rejected' && (
        <div className="w-full bg-slate-800 rounded-full h-1.5 mb-1.5">
          <div className={`h-1.5 rounded-full transition-all ${barColor}`} style={{ width: `${job.progress ?? 0}%` }} />
        </div>
      )}

      <div className="text-xs text-slate-600">
        {job.status === 'completed' && job.result ? (
          job.result.tickets_ingested !== undefined ? (
            <span>{job.result.tickets_ingested} tickets ingested</span>
          ) : (
            <span>{job.result.chunks_created} chunks · {job.result.doc_type} · {job.result.repository}
              {job.result.image_chunks > 0 && <> · {job.result.image_chunks} image chunk{job.result.image_chunks === 1 ? '' : 's'}</>}
            </span>
          )
        ) : job.status === 'failed' ? (
          <span className="text-red-400">{job.error || 'Upload failed'}</span>
        ) : job.status === 'cancelled' ? (
          <span>Cancelled</span>
        ) : job.status === 'rejected' ? (
          <span className="text-red-400">{job.error || 'Unsupported file type'}</span>
        ) : (
          <span>{formatStage(job.stage)}{job.progress != null ? ` · ${job.progress}%` : ''}</span>
        )}
      </div>
    </div>
  )
}

export default function Admin() {
  const [status, setStatus]           = useState(null)
  const [evalMetrics, setEvalMetrics] = useState(null)
  const [gaps, setGaps]               = useState([])
  const [feedback, setFeedback]       = useState(null)
  const [uploadJobs, setUploadJobs]   = useState([]) // P10: [{job_id, filename, status, stage, progress, message, error, result, batch_id}]
  const [ticketJobs, setTicketJobs]   = useState([]) // P10: same shape as uploadJobs
  const [repo, setRepo]               = useState('IT')
  const [origin, setOrigin]           = useState('INTERNAL')
  const [roles, setRoles]             = useState(['EMPLOYEE','MANAGER'])
  const [activeTab, setActiveTab]     = useState('upload')
  const [clearModal, setClearModal]   = useState(null) // null | 'all' | 'documents' | 'tickets'
  const [clearConfirmText, setClearConfirmText] = useState('')
  const [clearing, setClearing]       = useState(false)
  const [clearResult, setClearResult] = useState(null)

  const loadAll = () => {
    api.status().then(setStatus).catch(()=>{})
    api.evaluationMetrics(7).then(setEvalMetrics).catch(()=>{})
    api.getKnowledgeGaps(20).then(r => setGaps(r.gaps || [])).catch(()=>{})
    api.getFeedbackSummary(7).then(setFeedback).catch(()=>{})
  }

  useEffect(() => { loadAll() }, [])

  const handleFileUpload = async (e) => {
    const files = Array.from(e.target.files || [])
    e.target.value = '' // allow re-selecting the same file(s) later
    if (!files.length) return

    // A single .zip archive is treated as a bulk upload — every supported
    // file inside it (.pdf/.docx/.pptx) becomes its own tracked job.
    const isZip = files.length === 1 && files[0].name.toLowerCase().endsWith('.zip')

    const fd = new FormData()
    if (isZip) {
      fd.append('file', files[0])
    } else if (files.length === 1) {
      fd.append('file', files[0])
    } else {
      files.forEach(f => fd.append('files', f))
    }
    fd.append('repository', repo)
    fd.append('doc_origin', origin)
    fd.append('access_roles', roles.join(','))

    try {
      let newJobs
      if (isZip) {
        const r = await api.ingestBulkZip(fd)
        newJobs = r.jobs.map(j => ({ ...j, batch_id: r.batch_id }))
      } else if (files.length === 1) {
        const r = await api.ingestFile(fd)
        newJobs = [{ job_id: r.job_id, filename: r.filename, status: r.status }]
      } else {
        const r = await api.ingestBulk(fd)
        newJobs = r.jobs.map(j => ({ ...j, batch_id: r.batch_id }))
      }
      setUploadJobs(prev => [
        ...newJobs.map(j => ({ progress: 0, stage: 'queued', ...j })),
        ...prev,
      ])
    } catch (err) {
      setUploadJobs(prev => [
        { job_id: null, filename: isZip || files.length === 1 ? files[0].name : `${files.length} files`, status: 'failed', error: err.message },
        ...prev,
      ])
    }
  }

  const handleCancelBatch = async (batchId) => {
    setUploadJobs(prev => prev.map(j => (
      j.batch_id === batchId && !TERMINAL_JOB_STATUSES.includes(j.status)
        ? { ...j, status: 'cancelling' }
        : j
    )))
    try {
      await api.cancelIngestBatch(batchId)
    } catch (err) {
      // next poll tick will reconcile actual statuses
    }
  }

  const handleCancelJob = async (jobId, setJobs) => {
    if (!jobId) return
    setJobs(prev => prev.map(j => j.job_id === jobId ? { ...j, status: 'cancelling' } : j))
    try {
      await api.cancelIngestJob(jobId)
    } catch (err) {
      // If cancellation failed because the job already finished, the next
      // poll tick will pick up its real terminal status.
    }
  }
  const handleCancelUploadJob = (jobId) => handleCancelJob(jobId, setUploadJobs)
  const handleCancelTicketJob = (jobId) => handleCancelJob(jobId, setTicketJobs)

  // P10: poll a list of jobs for progress until each reaches a terminal state
  const pollJobs = (jobs, setJobs, onAnyCompleted) => {
    const activeIds = jobs
      .filter(j => j.job_id && !TERMINAL_JOB_STATUSES.includes(j.status))
      .map(j => j.job_id)
    if (!activeIds.length) return undefined

    const interval = setInterval(async () => {
      const updates = await Promise.all(
        activeIds.map(id => api.getIngestJob(id).catch(() => null))
      )
      let anyCompleted = false
      setJobs(prev => prev.map(j => {
        const u = updates.find(x => x && x.job_id === j.job_id)
        if (!u) return j
        if (u.status === 'completed') anyCompleted = true
        return { ...j, ...u }
      }))
      if (anyCompleted && onAnyCompleted) onAnyCompleted()
    }, 1500)

    return () => clearInterval(interval)
  }

  // P10: poll active document upload jobs for progress until terminal
  useEffect(() => pollJobs(uploadJobs, setUploadJobs, loadAll), [uploadJobs])

  // P10: poll active ticket ingestion jobs for progress until terminal
  useEffect(() => pollJobs(ticketJobs, setTicketJobs, loadAll), [ticketJobs])

  const handleTicketUpload = async (e) => {
    const file = e.target.files[0]; if (!file) return
    e.target.value = '' // allow re-selecting the same file later
    const fd = new FormData()
    fd.append('file', file)
    try {
      const r = await api.ingestTickets(fd)
      setTicketJobs(prev => [{ job_id: r.job_id, filename: r.filename, status: r.status, progress: 0, stage: 'queued' }, ...prev])
    } catch (err) {
      setTicketJobs(prev => [{ job_id: null, filename: file.name, status: 'failed', error: err.message }, ...prev])
    }
  }

  const handleResolveGap = async (gapId) => {
    try {
      await api.resolveGap(gapId)
      setGaps(prev => prev.filter(g => g.gap_id !== gapId))
    } catch(e) {}
  }

  const toggleRole = (r) => setRoles(prev => prev.includes(r) ? prev.filter(x=>x!==r) : [...prev,r])

  const handleClearKnowledgeBase = async () => {
    if (clearConfirmText.trim().toUpperCase() !== 'DELETE') return
    setClearing(true)
    setClearResult(null)
    try {
      const res = await api.clearKnowledgeBase(clearModal)
      setClearResult({ ok: true, ...res })
      // Refresh status bar (doc/vector counts) after clearing
      api.status().then(setStatus).catch(() => {})
    } catch (e) {
      setClearResult({ ok: false, message: e.message })
    } finally {
      setClearing(false)
      setClearConfirmText('')
    }
  }

  const closeClearModal = () => {
    setClearModal(null)
    setClearConfirmText('')
    setClearResult(null)
  }
  const StatusDot = ({ok}) => <div className={`w-2 h-2 rounded-full ${ok?'bg-emerald-400':'bg-red-400'}`}/>

  const TABS = [
    {id:'upload', label:'Upload'},
    {id:'eval', label:'Evaluation'},
    {id:'gaps', label:`Knowledge Gaps ${gaps.length > 0 ? `(${gaps.length})` : ''}`},
    {id:'feedback', label:'Feedback'},
    {id:'users', label:'Users'},
    {id:'access', label:'Access Requests'},
    {id:'audit', label:'Audit Log'},
  ]

  return (
    <div className="p-6 space-y-5 animate-fade-in">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Admin</h1>
          <p className="text-sm text-slate-500 mt-0.5">Documents · Evaluation · Knowledge Gaps · Feedback</p>
        </div>
        <button className="btn-ghost text-xs" onClick={loadAll}><RefreshCw size={12}/>Refresh</button>
      </div>

      {/* Status bar */}
      {status && (
        <div className="card p-4 flex flex-wrap gap-6 text-sm">
          {[
            {ok: status.ollama_ready, label: 'Ollama LLM', sub: status.model},
            {ok: status.qdrant?.status !== 'error', label: 'Qdrant', sub: `${(status.qdrant?.vectors_count||0).toLocaleString()} vectors`},
            {ok: true, label: 'BM25', sub: `${(status.bm25_docs||0).toLocaleString()} docs`},
            {ok: true, label: 'Embeddings', sub: status.embedding_model?.split('/').pop()},
          ].map(({ok,label,sub}) => (
            <div key={label} className="flex items-center gap-2">
              <StatusDot ok={ok}/>
              <div><div className="text-slate-300 text-xs font-medium">{label}</div><div className="text-xs text-slate-600">{sub}</div></div>
            </div>
          ))}
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-slate-700/50">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setActiveTab(t.id)}
            className={`px-5 py-2.5 text-sm font-medium border-b-2 transition-colors ${activeTab===t.id ? 'border-brand-500 text-brand-300' : 'border-transparent text-slate-500 hover:text-slate-300'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Upload Tab */}
      {activeTab === 'upload' && (
        <div className="space-y-5">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-4"><Upload size={16} className="text-brand-400"/><div className="text-sm font-medium text-slate-300">Upload Document</div></div>
            <div className="space-y-3 mb-4">
              <div>
                <label className="text-xs text-slate-500 mb-1 block">Repository</label>
                <select className="input" value={repo} onChange={e => setRepo(e.target.value)}>
                  {REPOS.map(r => <option key={r}>{r}</option>)}
                </select>
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">Origin</label>
                <div className="flex gap-2">
                  {['INTERNAL','EXTERNAL'].map(o => (
                    <button key={o} onClick={() => setOrigin(o)}
                      className={`flex-1 py-2 rounded-lg text-xs font-medium border transition-colors ${origin===o ? 'bg-brand-600/20 border-brand-500/60 text-brand-300' : 'border-slate-700 text-slate-500 hover:text-slate-300'}`}>
                      {o}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-xs text-slate-500 mb-1 block">Access Roles (RBAC)</label>
                <div className="flex flex-wrap gap-1.5">
                  {ROLES_LIST.map(r => (
                    <button key={r} onClick={() => toggleRole(r)}
                      className={`badge cursor-pointer transition-colors ${roles.includes(r) ? 'bg-brand-600/25 text-brand-300 border border-brand-500/40' : 'bg-slate-700/50 text-slate-500 hover:text-slate-300'}`}>
                      {r}
                    </button>
                  ))}
                </div>
              </div>
            </div>
            <label className="block w-full border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors border-slate-700 hover:border-brand-500/50 hover:bg-brand-600/5">
              <FileText size={20} className="mx-auto mb-2 text-slate-500"/>
              <div className="text-sm text-slate-400">PDF, DOCX, or PPTX</div>
              <div className="text-xs text-slate-600 mt-1">Select multiple files, or a .zip archive, for a bulk upload</div>
              <input type="file" accept=".pdf,.docx,.pptx,.zip" multiple className="hidden" onChange={handleFileUpload}/>
            </label>
            {uploadJobs.length > 0 && (
              <div className="mt-3 space-y-2 max-h-72 overflow-y-auto">
                {[...new Set(uploadJobs.filter(j => j.batch_id).map(j => j.batch_id))].map(batchId => {
                  const batchJobs = uploadJobs.filter(j => j.batch_id === batchId)
                  const active = batchJobs.filter(j => !TERMINAL_JOB_STATUSES.includes(j.status))
                  if (!active.length) return null
                  return (
                    <div key={batchId} className="flex items-center justify-between px-1">
                      <span className="text-xs text-slate-500">
                        Bulk upload: {batchJobs.length - active.length}/{batchJobs.length} done
                      </span>
                      <button
                        onClick={() => handleCancelBatch(batchId)}
                        className="text-xs text-red-400 hover:text-red-300 flex items-center gap-1 px-2 py-1 border border-red-500/30 rounded-lg"
                      >
                        <XCircle size={12}/> Cancel remaining ({active.length})
                      </button>
                    </div>
                  )
                })}
                {uploadJobs.map((job, i) => (
                  <UploadJobRow key={job.job_id || `rejected-${i}`} job={job} onCancel={handleCancelUploadJob}/>
                ))}
              </div>
            )}
          </div>

          <div className="card p-5">
            <div className="flex items-center gap-2 mb-4"><Ticket size={16} className="text-amber-400"/><div className="text-sm font-medium text-slate-300">Load Tickets CSV</div></div>
            <div className="text-xs text-slate-500 mb-4 leading-relaxed">
              Columns: <span className="font-mono text-slate-400">id, subject, description,	priority,	category,	createdAt,	requesterEmail,	status,	resolution,	resolvedAt </span>
            </div>
            <label className="block w-full border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors border-slate-700 hover:border-amber-500/50 hover:bg-amber-600/5">
              <Ticket size={20} className="mx-auto mb-2 text-slate-500"/>
              <div className="text-sm text-slate-400">tickets.csv</div>
              <div className="text-xs text-slate-600 mt-1">2000+ tickets ≈ 30 sec</div>
              <input type="file" accept=".csv" className="hidden" onChange={handleTicketUpload}/>
            </label>
            {ticketJobs.length > 0 && (
              <div className="mt-3 space-y-2 max-h-72 overflow-y-auto">
                {ticketJobs.map((job, i) => (
                  <UploadJobRow key={job.job_id || `rejected-${i}`} job={job} onCancel={handleCancelTicketJob}/>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Danger Zone */}
        <div className="card p-5 mt-5 border border-red-500/20">
          <div className="flex items-center gap-2 mb-2">
            <AlertOctagon size={16} className="text-red-400" />
            <div className="text-sm font-medium text-red-300">Danger Zone</div>
          </div>
          <p className="text-xs text-slate-500 mb-4">
            Permanently wipe ingested data. This removes records from Postgres
            <span className="font-mono text-slate-400"> AND </span> the BM25
            search index <span className="font-mono text-slate-400">AND</span> the
            Qdrant vector store. This cannot be undone — you'll need to
            re-upload documents/tickets afterwards.
          </p>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => setClearModal('documents')} className="btn-ghost text-xs text-red-400 border border-red-500/20 hover:bg-red-500/10">
              <Trash2 size={13} /> Clear Documents
            </button>
            <button onClick={() => setClearModal('tickets')} className="btn-ghost text-xs text-red-400 border border-red-500/20 hover:bg-red-500/10">
              <Trash2 size={13} /> Clear Tickets
            </button>
            <button onClick={() => setClearModal('all')} className="btn-ghost text-xs text-red-300 border border-red-500/40 hover:bg-red-500/15 font-medium">
              <Trash2 size={13} /> Clear Everything
            </button>
          </div>
        </div>

        {/* Confirmation Modal */}
        {clearModal && (
          <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={closeClearModal}>
            <div className="card p-6 max-w-md w-full border border-red-500/30" onClick={e => e.stopPropagation()}>
              <div className="flex items-center gap-2 mb-3">
                <AlertOctagon size={20} className="text-red-400" />
                <div className="text-base font-semibold text-slate-100">Are you sure?</div>
              </div>
              {!clearResult ? (
                <>
                  <p className="text-sm text-slate-400 mb-4">
                    {clearModal === 'all' && 'This will permanently delete ALL documents, chunks, and tickets from Postgres, and fully clear the BM25 and Qdrant search indices.'}
                    {clearModal === 'documents' && 'This will permanently delete all documents and chunks from Postgres. The BM25 and Qdrant indices (which also contain tickets) will be fully cleared — re-upload tickets.csv afterwards if needed.'}
                    {clearModal === 'tickets' && 'This will permanently delete all tickets from Postgres. The BM25 and Qdrant indices (which also contain documents) will be fully cleared — re-upload documents afterwards if needed.'}
                    {' '}<span className="text-red-400 font-medium">This action cannot be undone.</span>
                  </p>
                  <p className="text-xs text-slate-500 mb-2">Type <span className="font-mono text-red-400">DELETE</span> to confirm:</p>
                  <input
                    type="text"
                    autoFocus
                    value={clearConfirmText}
                    onChange={e => setClearConfirmText(e.target.value)}
                    placeholder="DELETE"
                    className="w-full bg-[#0f1520] border border-red-500/30 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder:text-slate-700 focus:outline-none focus:ring-1 focus:ring-red-500/50 mb-4"
                  />
                  <div className="flex gap-2">
                    <button
                      onClick={handleClearKnowledgeBase}
                      disabled={clearConfirmText.trim().toUpperCase() !== 'DELETE' || clearing}
                      className="flex-1 py-2 rounded-lg text-sm font-medium bg-red-600 hover:bg-red-700 text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center justify-center gap-2"
                    >
                      {clearing ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />}
                      {clearing ? 'Clearing...' : 'Yes, delete permanently'}
                    </button>
                    <button onClick={closeClearModal} className="btn-ghost text-sm">Cancel</button>
                  </div>
                </>
              ) : clearResult.ok ? (
                <>
                  <div className="flex items-center gap-2 text-emerald-400 text-sm mb-3">
                    <Check size={14} /> Cleared successfully
                  </div>
                  <div className="text-xs text-slate-500 space-y-1 mb-4">
                    {clearResult.postgres?.documents_deleted !== undefined && (
                      <div>{clearResult.postgres.documents_deleted} document(s) removed from Postgres</div>
                    )}
                    {clearResult.postgres?.tickets_deleted !== undefined && (
                      <div>{clearResult.postgres.tickets_deleted} ticket(s) removed from Postgres</div>
                    )}
                    {clearResult.bm25_cleared && <div>BM25 index cleared</div>}
                    {clearResult.qdrant_cleared && <div>Qdrant collection cleared and recreated</div>}
                  </div>
                  <button onClick={closeClearModal} className="btn-primary w-full justify-center text-sm">Done</button>
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-red-400 text-sm mb-3">
                    <X size={14} /> {clearResult.message}
                  </div>
                  <button onClick={closeClearModal} className="btn-ghost w-full justify-center text-sm">Close</button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
      )}

      {/* Evaluation Tab */}
      {activeTab === 'eval' && (
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2"><BarChart3 size={16} className="text-brand-400"/><div className="text-sm font-medium text-slate-300">Retrieval Evaluation (last 7 days)</div></div>
            <button className="btn-ghost text-xs" onClick={() => api.evaluationMetrics(7).then(setEvalMetrics)}><RefreshCw size={12}/>Refresh</button>
          </div>
          <EvalMetrics metrics={evalMetrics}/>
        </div>
      )}

      {/* Knowledge Gaps Tab */}
      {activeTab === 'gaps' && (
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-2"><AlertTriangle size={16} className="text-amber-400"/><div className="text-sm font-medium text-slate-300">Knowledge Gaps ({gaps.length} unresolved)</div></div>
            <button className="btn-ghost text-xs" onClick={() => api.getKnowledgeGaps(20).then(r => setGaps(r.gaps||[]))}><RefreshCw size={12}/>Refresh</button>
          </div>
          <div className="text-xs text-slate-500 mb-3">Queries with zero or very low confidence results — indicating missing documentation in your corpus.</div>
          <KnowledgeGapsPanel gaps={gaps} onResolve={handleResolveGap}/>
        </div>
      )}

      {/* Feedback Tab */}
      {activeTab === 'feedback' && (
        <div className="card p-5">
          <div className="flex items-center gap-2 mb-4"><TrendingUp size={16} className="text-emerald-400"/><div className="text-sm font-medium text-slate-300">User Feedback (last 7 days)</div></div>
          {feedback ? (
            <div className="space-y-4">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {[
                  {label:'Total Feedback', value: feedback.total_feedback, color:'text-slate-100'},
                  {label:'Positive', value: feedback.positive, color:'text-emerald-400'},
                  {label:'Negative', value: feedback.negative, color:'text-red-400'},
                  {label:'Satisfaction', value: `${((feedback.satisfaction_rate||0)*100).toFixed(0)}%`, color: feedback.satisfaction_rate >= 0.7 ? 'text-emerald-400' : 'text-amber-400'},
                ].map(({label,value,color}) => (
                  <div key={label} className="card p-4">
                    <div className={`text-2xl font-bold ${color}`}>{value}</div>
                    <div className="text-xs text-slate-500 mt-0.5">{label}</div>
                  </div>
                ))}
              </div>
              {feedback.top_negative_queries?.length > 0 && (
                <div>
                  <div className="text-xs text-slate-500 mb-2">Queries with Most Negative Feedback</div>
                  <div className="space-y-1.5">
                    {feedback.top_negative_queries.map((q, i) => (
                      <div key={i} className="flex items-center justify-between p-2.5 bg-slate-800/50 rounded-lg text-sm">
                        <span className="text-slate-400 truncate mr-3">{q.query}</span>
                        <span className="badge bg-red-500/20 text-red-400 flex-shrink-0">×{q.count}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ) : (
            <div className="text-slate-600 text-sm text-center py-6">No feedback data yet. Users can rate answers in the Chat page.</div>
          )}
        </div>
      )}

      {/* Users Tab */}
      {activeTab === 'users' && (
        <div className="card p-5">
          <UsersAdmin />
        </div>
      )}

      {/* Access Requests Tab */}
      {activeTab === 'access' && (
        <div className="card p-5">
          <AccessRequestsAdmin />
        </div>
      )}

      {/* Audit Log Tab */}
      {activeTab === 'audit' && (
        <div className="card p-5">
          <AuditLogAdmin />
        </div>
      )}
    </div>
  )
}