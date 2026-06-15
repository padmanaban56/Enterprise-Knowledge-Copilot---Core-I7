import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, FileText, Layers, ChevronDown, ChevronRight, Tag, AlertCircle, BookOpen, List, ExternalLink } from 'lucide-react'
import { api } from '../services/api'

const REPO_COLORS = { HR:'#10b981', Finance:'#f59e0b', IT:'#3b82f6', Engineering:'#8b5cf6', Projects:'#ec4899', External:'#64748b' }

function docDisplayName(doc) {
  return doc?.source_file?.split('/').pop() || doc?.title || 'Untitled'
}

function ChunkCard({ chunk, index, defaultOpen }) {
  const [open, setOpen] = useState(!!defaultOpen)
  return (
    <div className="card overflow-hidden">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-700/20 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          {open ? <ChevronDown size={14} className="text-slate-500 flex-shrink-0" /> : <ChevronRight size={14} className="text-slate-500 flex-shrink-0" />}
          <span className="text-xs font-mono text-slate-600 flex-shrink-0">#{index + 1}</span>
          <span className="text-sm text-slate-200 truncate">{chunk.section_title || `Chunk ${index + 1}`}</span>
        </div>
        {chunk.page_number ? (
          <span className="badge bg-slate-700/50 text-slate-400 text-[10px] flex-shrink-0">Page {chunk.page_number}</span>
        ) : null}
      </button>

      {open && (
        <div className="px-4 pb-4 space-y-3 border-t border-slate-700/30 pt-3">
          {chunk.summary && (
            <div>
              <div className="text-xs font-medium text-slate-500 mb-1">Summary</div>
              <p className="text-sm text-slate-300 leading-relaxed">{chunk.summary}</p>
            </div>
          )}

          {chunk.keywords?.length > 0 && (
            <div>
              <div className="text-xs font-medium text-slate-500 mb-1.5 flex items-center gap-1"><Tag size={11} /> Keywords</div>
              <div className="flex flex-wrap gap-1.5">
                {chunk.keywords.map((kw, i) => (
                  <span key={i} className="badge bg-brand-600/15 text-brand-400 text-[11px]">{kw}</span>
                ))}
              </div>
            </div>
          )}

          <div>
            <div className="text-xs font-medium text-slate-500 mb-1">Content</div>
            <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">{chunk.content}</p>
          </div>
        </div>
      )}
    </div>
  )
}

export default function DocumentDetail() {
  const { docId } = useParams()
  const navigate = useNavigate()
  const [doc, setDoc] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [view, setView] = useState('read') // 'read' | 'chunks'
  const [openingFile, setOpeningFile] = useState(false)
  const [fileError, setFileError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api.getDocument(docId)
      .then(setDoc)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [docId])

  async function openDocumentFile() {
    setFileError(null)
    setOpeningFile(true)
    try {
      const blob = await api.getDocumentFile(docId)
      const url = URL.createObjectURL(blob)
      window.open(url, '_blank')
      // Revoke after the new tab has had a moment to load the blob URL.
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
    } catch (e) {
      setFileError(e.message)
      setTimeout(() => setFileError(null), 4000)
    } finally {
      setOpeningFile(false)
    }
  }


  if (loading) return <div className="p-6 text-slate-500 text-sm">Loading…</div>

  if (error || !doc) {
    return (
      <div className="p-6 animate-fade-in">
        <button onClick={() => navigate('/documents')} className="btn-ghost text-sm mb-4">
          <ArrowLeft size={14} /> Back to Documents
        </button>
        <div className="card p-12 text-center">
          <AlertCircle size={32} className="mx-auto mb-3 text-slate-700" />
          <div className="text-slate-400 text-sm">{error || 'Document not found'}</div>
        </div>
      </div>
    )
  }

  const repoColor = REPO_COLORS[doc.repository] || '#94a3b8'

  return (
    <div className="p-6 animate-fade-in space-y-6">
      <button onClick={() => navigate('/documents')} className="btn-ghost text-sm">
        <ArrowLeft size={14} /> Back to Documents
      </button>

      {/* Header / metadata card */}
      <div className="card p-5">
        <div className="flex items-start gap-3">
          <div className="p-2.5 rounded-lg bg-slate-700/40 flex-shrink-0">
            <FileText size={20} className="text-slate-400" />
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-lg font-semibold text-slate-100 break-words">{docDisplayName(doc)}</h1>
            <div className="text-xs text-slate-600 font-mono mt-1 break-all">{doc.doc_id}</div>
          </div>
          {doc.has_file && (
            <button
              onClick={openDocumentFile}
              disabled={openingFile}
              className="btn-ghost text-sm flex-shrink-0 border border-slate-700/60"
            >
              <ExternalLink size={14} /> {openingFile ? 'Opening…' : 'Open file'}
            </button>
          )}
        </div>

        {fileError && (
          <div className="mt-3 flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
            <AlertCircle size={14} />{fileError}
          </div>
        )}

        <div className="flex flex-wrap gap-2 mt-4">
          {doc.repository && (
            <span className="badge text-xs font-medium" style={{ background: `${repoColor}15`, color: repoColor }}>
              {doc.repository_display_name || doc.repository}
            </span>
          )}
          {doc.doc_type && <span className="badge bg-slate-700/50 text-slate-400">{doc.doc_type}</span>}
          {doc.doc_origin && (
            <span className={`badge ${doc.doc_origin === 'INTERNAL' ? 'bg-emerald-500/15 text-emerald-400' : 'bg-slate-700/50 text-slate-400'}`}>
              {doc.doc_origin}
            </span>
          )}
          <span className="badge bg-slate-700/50 text-slate-400 flex items-center gap-1">
            <Layers size={11} /> {doc.chunk_count ?? doc.chunks_found ?? 0} chunks
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-4 pt-4 border-t border-slate-700/30 text-xs">
          <div>
            <div className="text-slate-600 mb-1">Source file</div>
            <div className="text-slate-300 truncate" title={doc.source_file}>{doc.source_file?.split('/').pop()}</div>
          </div>
          <div>
            <div className="text-slate-600 mb-1">Department</div>
            <div className="text-slate-300">{doc.department || '—'}</div>
          </div>
          <div>
            <div className="text-slate-600 mb-1">Ingested</div>
            <div className="text-slate-300">{doc.ingested_at ? new Date(doc.ingested_at).toLocaleString() : '—'}</div>
          </div>
          <div>
            <div className="text-slate-600 mb-1">Last updated</div>
            <div className="text-slate-300">{doc.updated_at ? new Date(doc.updated_at).toLocaleString() : '—'}</div>
          </div>
        </div>

        {doc.access_roles?.length > 0 && (
          <div className="mt-4 pt-4 border-t border-slate-700/30">
            <div className="text-xs text-slate-600 mb-1.5">Access roles</div>
            <div className="flex flex-wrap gap-1.5">
              {doc.access_roles.map(r => (
                <span key={r} className="badge bg-brand-600/15 text-brand-400 text-[11px]">{r}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Chunks */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-200">Document Content</h2>
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-500">{doc.chunks?.length || 0} of {doc.chunk_count ?? 0} chunks</span>
            {doc.chunks?.length > 0 && (
              <div className="flex items-center bg-slate-800/60 border border-slate-700/60 rounded-lg p-0.5">
                <button
                  onClick={() => setView('read')}
                  className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs transition-colors ${view === 'read' ? 'bg-brand-600/20 text-brand-300' : 'text-slate-500 hover:text-slate-300'}`}
                >
                  <BookOpen size={11} /> Read
                </button>
                <button
                  onClick={() => setView('chunks')}
                  className={`flex items-center gap-1 px-2 py-1 rounded-md text-xs transition-colors ${view === 'chunks' ? 'bg-brand-600/20 text-brand-300' : 'text-slate-500 hover:text-slate-300'}`}
                >
                  <List size={11} /> Chunks
                </button>
              </div>
            )}
          </div>
        </div>

        {!doc.chunks || doc.chunks.length === 0 ? (
          <div className="card p-8 text-center text-slate-600 text-sm">
            No chunk content found for this document.
          </div>
        ) : view === 'read' ? (
          <div className="card p-5 space-y-5">
            {doc.chunks.map((chunk, i) => {
              const showPageBreak = chunk.page_number && chunk.page_number !== doc.chunks[i - 1]?.page_number
              return (
                <div key={chunk.chunk_id || i}>
                  {showPageBreak && (
                    <div className="flex items-center gap-2 mb-2 text-xs text-slate-600">
                      <div className="h-px bg-slate-700/50 flex-1" />
                      Page {chunk.page_number}
                      <div className="h-px bg-slate-700/50 flex-1" />
                    </div>
                  )}
                  {chunk.section_title && (
                    <div className="text-sm font-medium text-slate-300 mb-1">{chunk.section_title}</div>
                  )}
                  <p className="text-sm text-slate-300 leading-relaxed whitespace-pre-wrap">{chunk.content}</p>
                </div>
              )
            })}
          </div>
        ) : (
          <div className="space-y-2">
            {doc.chunks.map((chunk, i) => (
              <ChunkCard key={chunk.chunk_id || i} chunk={chunk} index={i} defaultOpen={false} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
