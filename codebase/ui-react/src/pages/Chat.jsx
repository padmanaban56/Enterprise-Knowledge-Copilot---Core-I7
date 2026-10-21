import { useState, useRef, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import {
  Send, Brain, User, ChevronDown, ChevronUp, ThumbsUp, ThumbsDown,
  Zap, Database, BarChart2, BookOpen, GitBranch,
  Layers, Activity, History, Plus, PanelLeftClose, PanelLeft, Package, Pin, Trash2, X,
  FolderOpen, FileText, CheckSquare, Pencil, Check
} from 'lucide-react'
import { api } from '../services/api'
import BundlePanel from '../components/BundlePanel'
import ModelSelector from '../components/ModelSelector'
import { citationHref, citationParts, uniqueCitationSources } from '../services/citations'
import clsx from 'clsx'

const REPO_COLORS = {
  HR:'#10b981', Finance:'#f59e0b', IT:'#3b82f6',
  Engineering:'#8b5cf6', Projects:'#ec4899', External:'#64748b'
}

// Documents from /repositories/{name}/documents return `source_file` (and a
// `title` that, for many existing docs, is just the uploaded UUID-style
// filename stem) — prefer the real filename for display.
function docDisplayName(doc) {
  return doc.source_file?.split('/').pop() || doc.filename || doc.title || 'Untitled'
}

// ── Pipeline Trace Viewer ────────────────────────────────────────────────────
// NOTE: We keep trace state in the parent Message component (not in here)
// so it survives tab switching.
function PipelineTrace({ trace, stats, hydeUsed, subQueries, open, onToggle }) {
  if (!trace?.length) return null

  const stepColors = [
    'border-slate-600', 'border-blue-600', 'border-purple-600',
    'border-cyan-600', 'border-green-600', 'border-orange-600',
    'border-yellow-500', 'border-pink-600', 'border-red-500', 'border-emerald-500'
  ]

  return (
    <div className="mt-2 rounded-xl border border-slate-700/40 overflow-hidden">
      <button onClick={onToggle}
        className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-800/60 hover:bg-slate-700/40 transition-colors text-xs">
        <div className="flex items-center gap-2 text-slate-400">
          <Activity size={12} />
          <span className="font-medium">Pipeline Trace</span>
          <span className="text-slate-600">— {trace.length} steps · dense={stats?.dense||0} q={stats?.question||0} sum={stats?.summary||0} bm25={stats?.bm25||0} → {stats?.final_chunks||0} final</span>
          {hydeUsed && <span className="badge bg-yellow-500/15 text-yellow-400">HyDE</span>}
          {subQueries?.length > 1 && <span className="badge bg-pink-500/15 text-pink-400">decomposed</span>}
        </div>
        {open ? <ChevronUp size={12} className="text-slate-500" /> : <ChevronDown size={12} className="text-slate-500" />}
      </button>
      {open && (
        <div className="p-3 bg-slate-900/50 space-y-2 max-h-80 overflow-y-auto">
          {trace.map((step, i) => (
            <div key={step.step} className={`border-l-2 pl-3 py-1 ${stepColors[i] || 'border-slate-600'}`}>
              <div className="flex items-center gap-2 mb-0.5">
                <span className="text-xs font-medium text-slate-300">Step {step.step}: {step.name}</span>
                {step.confidence && (
                  <span className="badge bg-slate-700 text-slate-400">{(step.confidence*100).toFixed(0)}%</span>
                )}
              </div>
              <div className="text-xs text-slate-500 font-mono leading-relaxed">
                {step.output && typeof step.output === 'string' && <div>→ {step.output}</div>}
                {step.departments?.length > 0 && <div>dept: {step.departments.join(', ')}</div>}
                {step.repositories?.length > 0 && <div>repos: {step.repositories.join(', ')}</div>}
                {step.expanded?.length > 0 && <div>expanded: {step.expanded.slice(0,3).join(' | ')}</div>}
                {step.sub_queries?.length > 1 && <div>split: {step.sub_queries.join(' / ')}</div>}
                {step.filters && Object.keys(step.filters).length > 0 && (
                  <div>filters: {JSON.stringify(step.filters)}</div>
                )}
                {step.pii_found && <div className="text-amber-500">PII detected: {step.types?.join(', ')}</div>}
                {step.signal_scores && Object.keys(step.signal_scores).length > 0 && (
                  <div>signals: {Object.entries(step.signal_scores).map(([k,v]) => `${k}=${(v*100).toFixed(0)}%`).join(', ')}</div>
                )}
                {step.name === 'Retrieval' && (
                  <div className="space-y-1 mt-0.5">
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                      <span>dense: {step.dense}</span>
                      <span>question: {step.question}</span>
                      <span>summary: {step.summary}</span>
                      <span>bm25: {step.bm25}</span>
                      <span>→ rrf: {step.rrf_candidates}</span>
                      <span>→ reranked: {step.reranked}</span>
                      <span className={step.below_threshold > 0 ? 'text-amber-500' : ''}>below threshold: {step.below_threshold}</span>
                      <span className={step.final_chunks === 0 ? 'text-red-400' : 'text-emerald-400'}>→ final: {step.final_chunks}</span>
                      <span>→ in context: {step.context_chunks}</span>
                    </div>
                    {step.mode === 'document_scope_summary' && (
                      <div className="text-brand-400">mode: scoped document summary (similarity search skipped)</div>
                    )}
                    {step.scope_escaped && (
                      <div className="text-amber-500">Left your selected document/bundle scope — answered from {step.scope?.toLowerCase()} scope instead</div>
                    )}
                    {step.chunks_per_document && Object.keys(step.chunks_per_document).length > 0 && (
                      <div>
                        chunks found per scoped document:{' '}
                        {Object.entries(step.chunks_per_document).map(([docId, count]) => (
                          <span key={docId} className="mr-2">{docId.slice(0, 8)}…: {count}</span>
                        ))}
                      </div>
                    )}
                    {step.cascade?.length > 0 && (
                      <div>
                        cascade: {step.cascade.map(l => `${l.label}(${l.chunks})`).join(' → ')}
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Sources & Retrieval Detail ───────────────────────────────────────────────
function SourcesPanel({ citations, transparency, reposSearched, expandedQueries }) {
  const [tab, setTab] = useState('citations')
  const sources = uniqueCitationSources(citations)
  if (!citations?.length && !transparency?.length) return null

  return (
    <div className="mt-2 rounded-xl border border-slate-700/40 overflow-hidden text-xs">
      <div className="flex border-b border-slate-700/40 bg-slate-800/40">
        {[['citations',`Sources (${sources.length})`],['chunks','Chunk Detail'],['meta','Query Meta']].map(([key,label])=>(
          <button key={key} onClick={() => setTab(key)}
            className={`px-4 py-2 font-medium transition-colors flex-1 ${tab===key ? 'text-brand-300 border-b-2 border-brand-500 bg-slate-800/60' : 'text-slate-500 hover:text-slate-300'}`}>
            {label}
          </button>
        ))}
      </div>
      <div className="p-3 space-y-1.5 max-h-56 overflow-y-auto bg-slate-900/40">
        {tab === 'citations' && sources.map(({ citation: c, sourceNumbers, pages },i) => (
          <a key={i} href={citationHref(c)} target="_blank" rel="noopener noreferrer"
            title={c.doc_type === 'Ticket' ? 'View in Tickets' : 'Open source document in a new tab'}
            className="flex items-start gap-2 p-2 bg-slate-800/50 rounded-lg cursor-pointer hover:bg-slate-700/50 transition-colors">
            <div className="min-w-5 px-1 rounded flex items-center justify-center bg-brand-600/20 text-brand-400 font-bold text-[10px] flex-shrink-0">{sourceNumbers.join(', ')}</div>
            <div className="min-w-0 flex-1">
              <div className="text-slate-300 font-medium truncate">{c.source}</div>
              <div className="text-slate-500 mt-0.5 flex gap-2 flex-wrap">
                <span>{c.section}</span>
                {pages.length > 0 && <span>p.{pages.join(', ')}</span>}
                {c.repository && (
                  <span className="badge text-[10px]" style={{background:`${REPO_COLORS[c.repository]}15`,color:REPO_COLORS[c.repository]||'#94a3b8'}}>{c.repository}</span>
                )}
              </div>
            </div>
            <div className="text-right flex-shrink-0">
              <div className="text-emerald-400 font-medium">{(c.score*100).toFixed(0)}%</div>
              <div className="text-slate-600 text-[10px]">Tier {c.priority_tier}</div>
            </div>
          </a>
        ))}

        {tab === 'chunks' && transparency?.map((c,i) => (
          <div key={i} className="p-2 bg-slate-800/40 rounded-lg">
            <div className="flex items-center justify-between mb-1">
              <div className="text-slate-300 font-medium truncate mr-2">{c.source} · {c.section||'General'}</div>
              <span className="text-emerald-400 font-medium flex-shrink-0">{(c.final_score*100).toFixed(0)}%</span>
            </div>
            <div className="flex gap-3 text-slate-600 flex-wrap">
              <span>RRF {(c.rrf_score*1000).toFixed(1)}</span>
              <span>Rerank {(c.rerank_score*100).toFixed(0)}%</span>
              <span>Tier {c.priority_tier}</span>
              {c.freshness_decay !== 0 && <span className="text-amber-600">decay {c.freshness_decay}</span>}
              {c.feedback_boost !== 0 && <span className="text-emerald-600">fb +{c.feedback_boost.toFixed(2)}</span>}
              <span className="badge bg-slate-700/40 text-slate-500">{c.retrieval_source}</span>
            </div>
          </div>
        ))}

        {tab === 'meta' && (
          <div className="space-y-2">
            {reposSearched?.length > 0 && (
              <div>
                <div className="text-slate-500 font-medium mb-1">Repositories Searched</div>
                <div className="flex gap-1.5 flex-wrap">
                  {reposSearched.map(r=>(
                    <span key={r} className="badge text-xs" style={{background:`${REPO_COLORS[r]}15`,color:REPO_COLORS[r]||'#94a3b8'}}>{r}</span>
                  ))}
                </div>
              </div>
            )}
            {expandedQueries?.length > 0 && (
              <div>
                <div className="text-slate-500 font-medium mb-1">Query Expansions</div>
                {expandedQueries.map((q,i)=>(
                  <div key={i} className="text-slate-400 py-0.5">{i===0?'→ ':'↪ '}{q}</div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

// ── Feedback Buttons ─────────────────────────────────────────────────────────
function FeedbackRow({ msg, sessionId, onFeedback }) {
  const [given, setGiven] = useState(null)
  if (msg.role !== 'assistant' || !msg.meta) return null

  const submit = async (rating) => {
    setGiven(rating)
    onFeedback && onFeedback(rating)
    try {
      await api.submitFeedback({
        session_id: sessionId,
        query_text: msg.userQuery || '',
        rating,
        cited_chunk_ids: msg.meta.citations?.map(c => c.chunk_id).filter(Boolean) || [],
        repositories_used: msg.meta.repositories_searched || [],
        confidence: msg.meta.confidence || 0,
      })
    } catch(e) { console.error('Feedback failed:', e) }
  }

  return (
    <div className="flex items-center gap-2 mt-2">
      <span className="text-xs text-slate-600">Was this helpful?</span>
      <button onClick={() => submit(1)} disabled={given !== null}
        className={`p-1 rounded transition-colors ${given===1 ? 'text-emerald-400' : 'text-slate-600 hover:text-emerald-400'}`}>
        <ThumbsUp size={12} />
      </button>
      <button onClick={() => submit(-1)} disabled={given !== null}
        className={`p-1 rounded transition-colors ${given===-1 ? 'text-red-400' : 'text-slate-600 hover:text-red-400'}`}>
        <ThumbsDown size={12} />
      </button>
      {given !== null && <span className="text-xs text-slate-600">Thanks!</span>}
    </div>
  )
}

// ── Message Component ─────────────────────────────────────────────────────────
// FIX: showSources & showTrace are stored in the message object (lifted state)
// so they don't reset when navigating away and back to the chat tab.
function Message({ msg, sessionId, onToggleSources, onToggleTrace }) {
  if (msg.role === 'event') return (
    <div role="note" className="flex items-center gap-3 py-1 text-xs text-slate-500">
      <span className="flex-1 border-t border-slate-700/40" />
      <span className="max-w-[85%] text-center break-words">{msg.content}</span>
      <span className="flex-1 border-t border-slate-700/40" />
    </div>
  )
  const isUser = msg.role === 'user'

  if (isUser) return (
    <div className="flex gap-3 justify-end">
      <div className="max-w-lg">
        <div className="bg-brand-600/20 border border-brand-500/25 rounded-2xl rounded-tr-sm px-4 py-3 text-sm text-slate-200 leading-relaxed">{msg.content}</div>
      </div>
      <div className="w-8 h-8 rounded-full bg-slate-700 flex items-center justify-center flex-shrink-0 mt-1">
        <User size={14} className="text-slate-400" />
      </div>
    </div>
  )

  const m = msg.meta || {}
  const sources = uniqueCitationSources(m.citations)
  const conf = m.confidence || 0
  const confClass = conf >= 0.75 ? 'text-emerald-400 bg-emerald-500/10' : conf >= 0.55 ? 'text-amber-400 bg-amber-500/10' : 'text-red-400 bg-red-500/10'

  return (
    <div className="flex gap-3">
      <div className="w-8 h-8 rounded-full bg-brand-600/25 border border-brand-500/30 flex items-center justify-center flex-shrink-0 mt-1">
        <Brain size={14} className="text-brand-400" />
      </div>
      <div className="flex-1 max-w-2xl">
        <div className="card px-4 py-3 text-sm text-slate-200 leading-relaxed whitespace-pre-wrap">
          {citationParts(msg.content, m.citations).map((part, index) => part.citation
            ? <a key={index} href={citationHref(part.citation)} target="_blank" rel="noopener noreferrer"
                title={`Open ${part.citation.source || 'source document'}${part.citation.page > 0 ? `, page ${part.citation.page}` : ''}`}
                className="text-brand-300 underline underline-offset-2 hover:text-brand-200">{part.text}</a>
            : part.text)}
        </div>
        {m.citations?.length > 0 && <div className="mt-2 flex flex-wrap gap-2 text-xs" aria-label="Source documents">
          {sources.map(({ citation, sourceNumbers, pages }, index) => <a key={index} href={citationHref(citation)} target="_blank" rel="noopener noreferrer"
            className="inline-flex items-center gap-1 rounded-lg border border-slate-700/50 px-2 py-1 text-brand-300 hover:bg-slate-800"
            title="Open source in a new tab"><FileText size={12} />[{sourceNumbers.join(', ')}] {citation.source || 'Source'}{pages.length ? ` · p.${pages.join(', ')}` : ''}</a>)}
        </div>}

        {m.guardrail?.blocked && <div role="status" className="mt-2 text-xs text-amber-400">Safety check: {m.guardrail.stage === 'input' ? 'request' : 'response'} blocked</div>}
        {m.confidence !== undefined && !m.guardrail?.blocked && (!m.citation_status || m.citation_status === 'grounded') && (
          <div className="flex items-center gap-2 mt-2 flex-wrap">
            <span className={`badge text-xs font-medium ${confClass}`}>
              <BarChart2 size={10} className="mr-1" />
              {m.confidence_label} {(conf*100).toFixed(0)}%
            </span>
            {m.intent && <span className="badge bg-slate-700/50 text-slate-500">{m.intent}</span>}
            {m.hyde_used && <span className="badge bg-yellow-500/15 text-yellow-400">HyDE</span>}
            {m.sub_queries?.length > 1 && <span className="badge bg-pink-500/15 text-pink-400">decomposed</span>}
            {m.latency_ms && <span className="text-xs text-slate-600 flex items-center gap-1"><Zap size={10}/>{m.latency_ms}ms</span>}
            {m.chunks_used > 0 && <span className="text-xs text-slate-600 flex items-center gap-1"><Database size={10}/>{m.chunks_used} chunks</span>}
            {m.citations?.length > 0 && (
              <button onClick={onToggleSources}
                className="text-xs text-brand-400 hover:text-brand-300 flex items-center gap-1">
                <BookOpen size={10}/> {sources.length} sources
                {msg.showSources ? <ChevronUp size={10}/> : <ChevronDown size={10}/>}
              </button>
            )}
            {m.pipeline_trace?.length > 0 && (
              <button onClick={onToggleTrace}
                className="text-xs text-slate-500 hover:text-slate-300 flex items-center gap-1">
                <Activity size={10}/> trace
                {msg.showTrace ? <ChevronUp size={10}/> : <ChevronDown size={10}/>}
              </button>
            )}
            {m.confidence_breakdown?.reasoning && (
              <span className="text-xs text-slate-600 italic hidden lg:inline">{m.confidence_breakdown.reasoning}</span>
            )}
          </div>
        )}

        {msg.showSources && <SourcesPanel citations={m.citations} transparency={m.retrieval_transparency} reposSearched={m.repositories_searched} expandedQueries={m.expanded_queries} />}
        {msg.showTrace && <PipelineTrace trace={m.pipeline_trace} stats={m.pipeline_stats} hydeUsed={m.hyde_used} subQueries={m.sub_queries} open={msg.showTrace} onToggle={onToggleTrace} />}
        <FeedbackRow msg={msg} sessionId={sessionId} />
      </div>
    </div>
  )
}

function TypingIndicator() {
  return (
    <div className="flex gap-3">
      <div className="w-8 h-8 rounded-full bg-brand-600/25 border border-brand-500/30 flex items-center justify-center flex-shrink-0">
        <Brain size={14} className="text-brand-400" />
      </div>
      <div className="card px-4 py-3 flex items-center gap-1.5">
        <span className="typing-dot"/><span className="typing-dot"/><span className="typing-dot"/>
      </div>
    </div>
  )
}

const SUGGESTIONS = [
  'How do I request annual leave?',
  'VPN setup process for remote access',
  'What is the invoice approval procedure?',
  'Kubernetes pod crash troubleshooting steps',
]

export default function Chat() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [selectedModel, setSelectedModel] = useState(() => localStorage.getItem('ekc-answer-model') ?? 'ollama|phi3:mini')
  function selectModel(value) {
    if (loading || loadingHistory) return
    applyContext({ ...selectionRef.current, model: value })
    localStorage.setItem('ekc-answer-model', value)
  }
  const [sessionId, setSessionId] = useState(() => searchParams.get('session') || crypto.randomUUID())
  const [historyOpen, setHistoryOpen] = useState(true)
  const [sessions, setSessions] = useState([])
  const [editingSession, setEditingSession] = useState(null)
  const [chatName, setChatName] = useState('')
  const [savingName, setSavingName] = useState(false)
  const [renameError, setRenameError] = useState('')
  const [loadingHistory, setLoadingHistory] = useState(false)

  // Context sidebar state
  const [sidebarTab, setSidebarTab] = useState('repos') // 'repos' | 'bundles'
  const [repositories, setRepositories] = useState([])
  const [repoDocuments, setRepoDocuments] = useState({}) // repoName → [docs]
  const [loadingRepoDocs, setLoadingRepoDocs] = useState({})
  const [expandedRepo, setExpandedRepo] = useState(null)
  const [selectedDocIds, setSelectedDocIds] = useState([]) // [{docId, filename, repo}]
  const [bundles, setBundles] = useState([])
  const [bundleError, setBundleError] = useState('')
  const [selectedBundleIds, setSelectedBundleIds] = useState([])

  const bottomRef = useRef(null)
  const contextQueue = useRef(Promise.resolve())
  const currentSession = useRef(sessionId)
  const selectionRef = useRef(null)
  selectionRef.current = { model: selectedModel, docs: selectedDocIds, bundleIds: selectedBundleIds }
  const [contextError, setContextError] = useState('')

  function persistContext(next) {
    const sid = currentSession.current
    const request = {
      request_id: crypto.randomUUID(),
      provider: next.model ? next.model.split('|')[0] : null,
      model: next.model ? next.model.split('|')[1] : null,
      document_ids: next.docs.map(doc => doc.docId), bundle_ids: [...next.bundleIds],
    }
    const job = contextQueue.current.catch(() => {}).then(async () => {
      const result = await api.updateChatContext(sid, request)
      if (currentSession.current !== sid) return
      const events = result.messages.map(message => ({
        role: 'event', content: message.content, messageId: message.message_id,
        meta: message.retrieval_meta, createdAt: message.created_at,
      }))
      setMessages(previous => {
        const known = new Set(previous.map(message => message.messageId).filter(Boolean))
        return [...previous, ...events.filter(message => !known.has(message.messageId))]
      })
      setContextError('')
      setSearchParams({ session: sid })
      refreshSessions()
      return result
    })
    contextQueue.current = job
    job.catch(error => { if (currentSession.current === sid) setContextError(error.message) })
    return job
  }

  function applyContext(next) {
    if (loading || loadingHistory) return
    selectionRef.current = next
    setSelectedModel(next.model)
    setSelectedDocIds(next.docs)
    setSelectedBundleIds(next.bundleIds)
    persistContext(next)
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  // Toggle sources visibility — stored in message to survive tab navigation
  function toggleMessageSources(idx) {
    setMessages(prev => prev.map((m, i) => i === idx ? { ...m, showSources: !m.showSources } : m))
  }
  function toggleMessageTrace(idx) {
    setMessages(prev => prev.map((m, i) => i === idx ? { ...m, showTrace: !m.showTrace } : m))
  }

  const loadSession = useCallback(async (sid) => {
    setLoadingHistory(true)
    try {
      const res = await api.getChatSessionMessages(sid)
      const loaded = res.messages.map(m => {
        if (m.role === 'event') return { role: 'event', content: m.content, messageId: m.message_id, meta: m.retrieval_meta, createdAt: m.created_at }
        if (m.role === 'assistant') {
          const meta = m.retrieval_meta || {}
          return {
            role: 'assistant',
            content: m.content,
            showSources: false,
            showTrace: false,
            meta: {
              confidence: m.confidence,
              citations: m.citations || [],
              intent: meta.intent,
              repositories_searched: meta.repositories_searched,
              low_confidence: meta.low_confidence,
              guardrail: meta.guardrail,
              citation_status: meta.citation_status,
              latency_ms: meta.latency_ms,
            },
          }
        }
        return { role: 'user', content: m.content }
      })
      setMessages(loaded)
      currentSession.current = sid
      contextQueue.current = Promise.resolve()
      setContextError('')
      const state = res.context_state
      const next = {
        model: state ? (state.model ? `${state.provider}|${state.model}` : '') : selectionRef.current.model,
        docs: (state?.documents || []).map(doc => ({ docId: doc.doc_id, filename: doc.filename, repo: doc.repository })),
        bundleIds: state?.bundle_ids || [],
      }
      selectionRef.current = next
      setSelectedModel(next.model); setSelectedDocIds(next.docs); setSelectedBundleIds(next.bundleIds)
      setSessionId(sid)
      setSearchParams({ session: sid })
    } catch (e) {
      console.error('Failed to load session', e)
      setMessages([])
    } finally {
      setLoadingHistory(false)
      setHistoryOpen(false)
    }
  }, [setSearchParams])

  const refreshSessions = useCallback(async () => {
    try {
      const res = await api.listChatSessions()
      setSessions(res.sessions || [])
    } catch {
      setSessions([])
    }
  }, [])

  async function saveChatName(event) {
    event.preventDefault()
    if (!editingSession || !chatName.trim() || savingName) return
    setSavingName(true)
    setRenameError('')
    try {
      const updated = await api.renameChatSession(editingSession, chatName.trim())
      setSessions(previous => previous.map(session => session.session_id === updated.session_id
        ? { ...session, title: updated.title } : session))
      setEditingSession(null)
    } catch (error) {
      setRenameError(error.message)
    } finally {
      setSavingName(false)
    }
  }

  const refreshBundles = useCallback(async () => {
    try {
      const res = await api.getBundles()
      setBundles(res || [])
      if (selectionRef.current?.bundleIds.length) persistContext(selectionRef.current)
      setBundleError('')
    } catch (err) {
      setBundleError(err.message || 'Unable to load bundles')
    }
  }, [])

  const loadRepoDocs = useCallback(async (repoName) => {
    if (repoDocuments[repoName]) return
    setLoadingRepoDocs(p => ({ ...p, [repoName]: true }))
    try {
      const res = await api.getRepoDocuments(repoName, 100)
      setRepoDocuments(p => ({ ...p, [repoName]: res.documents || [] }))
    } catch {
      setRepoDocuments(p => ({ ...p, [repoName]: [] }))
    } finally {
      setLoadingRepoDocs(p => ({ ...p, [repoName]: false }))
    }
  }, [repoDocuments])

  function toggleExpandRepo(repoName) {
    if (expandedRepo === repoName) {
      setExpandedRepo(null)
    } else {
      setExpandedRepo(repoName)
      loadRepoDocs(repoName)
    }
  }

  function toggleDocSelection(doc, repoName) {
    const key = doc.doc_id || doc.id
    const prev = selectionRef.current
    const exists = prev.docs.some(item => item.docId === key)
    applyContext({ ...prev, docs: exists ? prev.docs.filter(item => item.docId !== key)
      : [...prev.docs, { docId: key, filename: docDisplayName(doc), repo: repoName }] })
  }

  function toggleBundleSelection(bundleId) {
    const prev = selectionRef.current
    applyContext({ ...prev, bundleIds: prev.bundleIds.includes(bundleId)
      ? prev.bundleIds.filter(id => id !== bundleId) : [...prev.bundleIds, bundleId] })
  }

  function clearScope() {
    applyContext({ ...selectionRef.current, docs: [], bundleIds: [] })
  }

  function startNewChat() {
    currentSession.current = crypto.randomUUID()
    contextQueue.current = Promise.resolve()
    setContextError('')
    setMessages([])
    setSessionId(currentSession.current)
    setSearchParams({})
    setHistoryOpen(false)
    setSelectedDocIds([]); setSelectedBundleIds([])
    selectionRef.current = { model: selectedModel, docs: [], bundleIds: [] }
  }

  function groupSessionsByDate(list) {
    const now = new Date()
    const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const startOfYesterday = new Date(startOfToday); startOfYesterday.setDate(startOfToday.getDate() - 1)
    const sevenDaysAgo = new Date(startOfToday); sevenDaysAgo.setDate(startOfToday.getDate() - 7)

    const groups = { Today: [], Yesterday: [], 'Previous 7 Days': [], Older: [] }
    for (const s of list) {
      const d = new Date(s.updated_at)
      if (d >= startOfToday) groups.Today.push(s)
      else if (d >= startOfYesterday) groups.Yesterday.push(s)
      else if (d >= sevenDaysAgo) groups['Previous 7 Days'].push(s)
      else groups.Older.push(s)
    }
    return Object.entries(groups).filter(([, items]) => items.length > 0)
  }

  const sendMessage = useCallback(async (text) => {
    const q = (text || input).trim()
    if (!q || loading) return
    setInput('')
    const userMsg = { role: 'user', content: q }
    setLoading(true)

    try {
      const saved = await persistContext(selectionRef.current)
      if (currentSession.current !== sessionId) return
      setMessages(prev => [...prev, userMsg])
      const history = messages.filter(m => m.role === 'user' || m.role === 'assistant').slice(-6).map(m => ({ role: m.role, content: m.content }))

      // Build scope: selected doc IDs + doc IDs from selected bundles
      const scopeDocIds = saved.context_state.effective_documents.map(doc => doc.doc_id)

      const body = {
        query: q,
        provider: saved.context_state.provider || 'ollama',
        model: saved.context_state.model,
        session_id: sessionId,
        chat_history: history,
        ...(scopeDocIds.length > 0 ? { document_ids: scopeDocIds } : {}),
      }

      const resp = await api.chat(body)
      if (currentSession.current !== sessionId) return

      const assistantMsg = {
        role: 'assistant',
        content: resp.answer,
        userQuery: q,
        showSources: false,
        showTrace: false,
        meta: {
          confidence: resp.confidence,
          confidence_label: resp.confidence_label,
          confidence_breakdown: resp.confidence_breakdown,
          citations: resp.citations,
          retrieval_transparency: resp.retrieval_transparency,
          repositories_searched: resp.repositories_searched,
          expanded_queries: resp.expanded_queries,
          sub_queries: resp.sub_queries,
          intent: resp.intent,
          hyde_used: resp.hyde_used,
          chunks_used: resp.chunks_used,
          latency_ms: resp.latency_ms,
          low_confidence: resp.low_confidence,
          guardrail: resp.guardrail,
          citation_status: resp.citation_status,
          pipeline_trace: resp.pipeline_trace,
          pipeline_stats: resp.pipeline_stats,
        },
      }
      setMessages(prev => [...prev, assistantMsg])

      if (searchParams.get('session') !== sessionId) {
        setSearchParams({ session: sessionId })
      }
      refreshSessions()
    } catch (err) {
      if (currentSession.current !== sessionId) return
      console.error('Chat error:', err)
      setMessages(prev => [...prev, {
        role: 'assistant',
        content: `❌ Error: ${err.message || 'Something went wrong. Please try again.'}`,
        showSources: false, showTrace: false,
        meta: {},
      }])
    } finally {
      setLoading(false)
    }
  }, [input, loading, messages, sessionId, searchParams, setSearchParams, refreshSessions, selectedDocIds, selectedBundleIds, bundles, selectedModel])

  useEffect(() => {
    const sid = searchParams.get('session')
    if (sid) loadSession(sid)
    refreshSessions()
    refreshBundles()
    api.getRepositories().then(r => setRepositories(r.repositories || [])).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const totalScopeCount = selectedDocIds.length + selectedBundleIds.length
  const hasScope = totalScopeCount > 0

  return (
    <div className="flex h-screen">
      {/* Left: Chat History Sidebar */}
      {historyOpen && (
        <aside className="w-60 flex-shrink-0 flex flex-col border-r border-slate-700/50 bg-[#0f1520]">
          <div className="flex-shrink-0 px-4 py-3 border-b border-slate-700/50 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <History size={14} className="text-brand-400" />
              <span className="text-sm font-semibold text-slate-200">Chat History</span>
            </div>
            <button onClick={() => setHistoryOpen(false)} title="Collapse"
              className="text-slate-500 hover:text-slate-200 p-1 rounded-md hover:bg-slate-700/50 transition-colors">
              <PanelLeftClose size={15} />
            </button>
          </div>
          <div className="px-3 py-3 border-b border-slate-700/50">
            <button className="btn-primary w-full justify-center text-sm" onClick={startNewChat}>
              <Plus size={14} /> New Chat
            </button>
          </div>
          <div className="flex-1 overflow-y-auto px-2 py-2">
            {renameError && <p role="alert" className="text-xs text-red-400 px-2 py-2">{renameError}</p>}
            {sessions.length === 0 ? (
              <div className="px-2 py-4 text-xs text-slate-600 text-center">No previous conversations yet.</div>
            ) : (
              groupSessionsByDate(sessions).map(([label, items]) => (
                <div key={label} className="mb-3">
                  <div className="text-xs font-medium text-slate-600 px-2 py-1.5">{label}</div>
                  {items.map(s => (
                    <div
                      key={s.session_id}
                      className={clsx(
                        'w-full text-left px-2.5 py-2 rounded-lg transition-colors mb-0.5 flex items-center gap-2',
                        s.session_id === sessionId ? 'bg-brand-600/15 text-brand-300' : 'hover:bg-slate-700/30 text-slate-300'
                      )}
                    >
                      {editingSession === s.session_id ? (
                        <form onSubmit={saveChatName} className="flex items-center gap-1 w-full">
                          <input autoFocus aria-label="Chat name" className="input text-xs min-w-0 flex-1"
                            value={chatName} maxLength={255} disabled={savingName}
                            onChange={event => setChatName(event.target.value)}
                            onKeyDown={event => { if (event.key === 'Escape' && !savingName) setEditingSession(null) }} />
                          <button type="submit" aria-label="Save chat name" disabled={savingName || !chatName.trim()}><Check size={14} /></button>
                          <button type="button" aria-label="Cancel rename" disabled={savingName} onClick={() => setEditingSession(null)}><X size={14} /></button>
                        </form>
                      ) : <>
                        <button onClick={() => loadSession(s.session_id)} className="min-w-0 flex-1 text-left">
                          <div className="text-xs truncate">{s.title || 'New Chat'}</div>
                          <div className="text-xs text-slate-600 mt-0.5">
                            {s.message_count} messages · {new Date(s.updated_at).toLocaleDateString()}
                          </div>
                        </button>
                        <button title="Rename chat" aria-label={`Rename ${s.title || 'chat'}`} className="text-slate-500 hover:text-slate-200 flex-shrink-0"
                          onClick={() => { setEditingSession(s.session_id); setChatName(s.title || ''); setRenameError('') }}><Pencil size={13} /></button>
                      </>}
                    </div>
                  ))}
                </div>
              ))
            )}
          </div>
        </aside>
      )}

      {/* Main chat column */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* Header */}
        <div className="flex-shrink-0 px-5 py-3 border-b border-slate-700/50 bg-[#0f1520] flex items-center gap-3">
          <div className="flex items-center gap-2 flex-1">
            {!historyOpen && (
              <button onClick={() => { setHistoryOpen(true); refreshSessions() }} title="Show history"
                className="text-slate-500 hover:text-slate-200 p-1 rounded-md hover:bg-slate-700/50 transition-colors mr-1">
                <PanelLeft size={15} />
              </button>
            )}
            <Brain size={16} className="text-brand-400" />
            <span className="text-sm font-semibold text-slate-200">Chat Assistant</span>
            <span className="text-xs text-slate-600">· 9-step retrieval pipeline</span>
            {hasScope && (
              <span className="ml-2 text-xs px-2 py-0.5 rounded-full border bg-brand-600/15 text-brand-300 border-brand-500/30 flex items-center gap-1">
                <CheckSquare size={10} />
                {totalScopeCount} item{totalScopeCount > 1 ? 's' : ''} scoped
                <button disabled={loading || loadingHistory} onClick={clearScope} className="ml-1 hover:text-white"><X size={10} /></button>
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <ModelSelector value={selectedModel} onChange={selectModel} disabled={loading || loadingHistory} />
            {messages.length > 0 && (
              <button className="btn-ghost text-xs" onClick={startNewChat}>Clear</button>
            )}
          </div>
        </div>

        {loadingHistory && (
          <div className="px-5 py-2 text-xs text-slate-500 bg-slate-800/50 border-b border-slate-700/30">
            Loading conversation...
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-5 py-5 space-y-5">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full text-center animate-fade-in">
              <div className="w-16 h-16 rounded-2xl bg-brand-600/20 border border-brand-500/30 flex items-center justify-center mb-5">
                <Brain size={28} className="text-brand-400" />
              </div>
              <h2 className="text-lg font-semibold text-slate-200 mb-2">Enterprise Knowledge Copilot</h2>
              <p className="text-sm text-slate-500 max-w-sm leading-relaxed mb-2">
                Powered by a 9-step retrieval pipeline: normalisation → PII scrub → intent detection → entity extraction → repository selection → filter extraction → HyDE → query expansion → decomposition.
              </p>
              <p className="text-xs text-slate-600 mb-6">Scope your search using the Repos / Bundles panel on the right.</p>
              <div className="grid grid-cols-2 gap-2 w-full max-w-md">
                {SUGGESTIONS.map(s => (
                  <button key={s} onClick={() => sendMessage(s)}
                    className="text-left p-3 rounded-xl border border-slate-700/60 bg-slate-800/40 hover:bg-slate-700/40 hover:border-slate-600 transition-all text-xs text-slate-400">
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
          {messages.map((msg, i) => (
            <Message key={i} msg={msg} sessionId={sessionId}
              onToggleSources={() => toggleMessageSources(i)}
              onToggleTrace={() => toggleMessageTrace(i)}
            />
          ))}
          {loading && <TypingIndicator />}
          <div ref={bottomRef} />
        </div>

        {/* Selected scope chips above input */}
        {hasScope && (
          <div className="flex-shrink-0 px-5 pt-2 flex flex-wrap gap-1.5">
            {selectedDocIds.map(d => (
              <span key={d.docId} className="text-xs px-2 py-1 bg-brand-600/10 text-brand-300 border border-brand-500/20 rounded-lg flex items-center gap-1">
                <FileText size={10} />{d.filename}
                <button disabled={loading} onClick={() => toggleDocSelection({ doc_id: d.docId, source_file: d.filename }, d.repo)}
                  className="ml-0.5 hover:text-white"><X size={9} /></button>
              </span>
            ))}
            {selectedBundleIds.map(bid => {
              const b = bundles.find(x => x.bundle_id === bid)
              return (
                <span key={bid} className="text-xs px-2 py-1 bg-indigo-600/10 text-indigo-300 border border-indigo-500/20 rounded-lg flex items-center gap-1">
                  <Package size={10} />{b?.name || 'Bundle'}
                  <button disabled={loading} onClick={() => toggleBundleSelection(bid)}
                    className="ml-0.5 hover:text-white"><X size={9} /></button>
                </span>
              )
            })}
          </div>
        )}

        {/* Input */}
        {contextError && <div role="alert" className="px-5 py-2 text-xs text-red-400">Context changes could not be saved: {contextError}
          <button className="ml-2 underline" onClick={() => persistContext(selectionRef.current)}>Retry</button>
        </div>}
        <div className="flex-shrink-0 px-5 py-4 border-t border-slate-700/50 bg-[#0f1520]">
          <div className="flex gap-3 items-end">
            <textarea
              className="input flex-1 resize-none min-h-[44px] max-h-32 py-3 leading-relaxed"
              placeholder="Ask about policies, procedures, or search tickets…"
              value={input} rows={1}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => { if (e.key==='Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() }}}
            />
            <button className="btn-primary h-11 px-5 flex-shrink-0"
              onClick={() => sendMessage()} disabled={loading || !input.trim()}>
              <Send size={15} />
            </button>
          </div>
          <div className="text-xs text-slate-700 mt-1.5">
            {hasScope ? `Scoped to ${totalScopeCount} item(s)` : 'All docs'} · Shift+Enter for newline
          </div>
        </div>
      </div>

      {/* Right: Context Sidebar — Repos / Bundles */}
      <aside className="w-64 flex-shrink-0 flex flex-col border-l border-slate-700/50 bg-[#0f1520]">
        <div className="flex-shrink-0 border-b border-slate-700/50">
          <div className="flex">
            {[['repos', <FolderOpen size={12} />, 'Repos'], ['bundles', <Package size={12} />, 'Bundles']].map(([tab, icon, label]) => (
              <button key={tab} onClick={() => setSidebarTab(tab)}
                className={clsx(
                  'flex-1 flex items-center justify-center gap-1.5 text-xs font-medium py-3 transition-colors',
                  sidebarTab === tab ? 'text-brand-300 border-b-2 border-brand-500 bg-slate-800/40' : 'text-slate-500 hover:text-slate-300'
                )}>
                {icon}{label}
              </button>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-2 py-2">
          {sidebarTab === 'repos' && (
            <>
              <div className="px-2 py-1.5 text-xs text-slate-600">Select documents to scope your query</div>
              {repositories.length === 0 ? (
                <div className="text-xs text-slate-600 text-center py-4">No repositories found</div>
              ) : repositories.map(repo => (
                <div key={repo.name} className="mb-1">
                  <button
                    onClick={() => toggleExpandRepo(repo.name)}
                    className="w-full flex items-center justify-between px-2.5 py-2 rounded-lg hover:bg-slate-700/30 transition-colors">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <FolderOpen size={12} className="text-slate-500 flex-shrink-0" />
                      <span className="text-xs text-slate-300 truncate">{repo.display_name || repo.name}</span>
                    </div>
                    <div className="flex items-center gap-1 flex-shrink-0">
                      <span className="text-xs text-slate-600">{repo.document_count}</span>
                      {expandedRepo === repo.name ? <ChevronUp size={11} className="text-slate-600" /> : <ChevronDown size={11} className="text-slate-600" />}
                    </div>
                  </button>
                  {expandedRepo === repo.name && (
                    <div className="ml-2 pl-2 border-l border-slate-700/40 space-y-0.5 mb-1">
                      {loadingRepoDocs[repo.name] ? (
                        <div className="text-xs text-slate-600 py-1.5 px-1">Loading…</div>
                      ) : (repoDocuments[repo.name] || []).length === 0 ? (
                        <div className="text-xs text-slate-600 py-1.5 px-1">No documents</div>
                      ) : (repoDocuments[repo.name] || []).map(doc => {
                        const key = doc.doc_id || doc.id
                        const isSelected = selectedDocIds.find(d => d.docId === key)
                        return (
                          <label key={key} className="flex items-center gap-1.5 px-1.5 py-1 rounded-md hover:bg-slate-700/30 cursor-pointer">
                            <input type="checkbox" checked={!!isSelected} disabled={loading || loadingHistory}
                              onChange={() => toggleDocSelection(doc, repo.name)}
                              className="rounded border-slate-600 bg-slate-800 text-brand-500 focus:ring-0 h-3 w-3" />
                            <FileText size={10} className="text-slate-600 flex-shrink-0" />
                            <span className="text-xs text-slate-400 truncate">{docDisplayName(doc)}</span>
                          </label>
                        )
                      })}
                    </div>
                  )}
                </div>
              ))}
            </>
          )}

          {sidebarTab === 'bundles' && (
            <BundlePanel bundles={bundles} selectedIds={selectedBundleIds}
              disabled={loading || loadingHistory}
              onToggle={toggleBundleSelection} onRefresh={refreshBundles}
              initialDocumentIds={selectedDocIds.map(doc => doc.docId)} loadError={bundleError} />
          )}
        </div>

        {hasScope && (
          <div className="flex-shrink-0 border-t border-slate-700/50 px-3 py-2">
            <button disabled={loading || loadingHistory} onClick={clearScope} className="btn-ghost text-xs w-full justify-center text-slate-500">
              <X size={12} /> Clear scope
            </button>
          </div>
        )}
      </aside>
    </div>
  )
}
