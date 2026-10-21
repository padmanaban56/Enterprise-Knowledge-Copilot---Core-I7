import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Download, FileText } from 'lucide-react'
import { api } from '../services/api'
import { documentFilename } from '../services/documentNames'

export default function DocumentDetail() {
  const { docId } = useParams()
  const [state, setState] = useState({ loading: true })

  useEffect(() => {
    let active = true
    let objectUrl
    async function load() {
      setState({ loading: true })
      let doc
      try {
        doc = await api.getDocument(docId)
        if (!active) return
        setState({ loading: true, doc })
        const blob = await api.getDocumentFile(docId)
        if (!active) return
        objectUrl = URL.createObjectURL(blob)
        setState({ loading: false, doc, url: objectUrl,
          pdf: blob.type === 'application/pdf' || doc.source_type?.toUpperCase() === 'PDF' })
      } catch (error) {
        if (active) setState({ loading: false, doc, error: error.message })
      }
    }
    load()
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [docId])

  const name = state.doc ? documentFilename(state.doc) : 'Document'
  return (
    <div className="p-6 animate-fade-in space-y-4">
      <Link to="/documents" className="btn-ghost text-sm inline-flex">
        <ArrowLeft size={14} /> Back to Documents
      </Link>
      <div className="card p-5 flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-start gap-3 min-w-0">
          <FileText size={20} className="text-slate-400 mt-1 flex-shrink-0" />
          <div className="min-w-0">
            <h1 className="text-lg font-semibold text-slate-100 break-words">{name}</h1>
            {state.doc && <div className="flex gap-2 mt-2 text-xs text-slate-400">
              <span>{state.doc.repository_display_name || state.doc.repository}</span>
              {state.doc.doc_type && <span>{state.doc.doc_type}</span>}
            </div>}
          </div>
        </div>
        {state.url && <a href={state.url} download={name} className="btn-primary text-sm">
          <Download size={14} /> Download file
        </a>}
      </div>
      <section aria-label="Document content" className="space-y-3">
        <h2 className="text-sm font-semibold text-slate-200">Document Content</h2>
        {state.loading && <p role="status" className="text-sm text-slate-400">Opening document...</p>}
        {state.error && <div role="alert" className="card p-6 text-sm text-slate-300">{state.error}</div>}
        {state.url && (state.pdf
          ? <iframe title={name} src={state.url} className="w-full min-h-[80vh] rounded-lg bg-white" />
          : <div className="card p-6 text-sm text-slate-300">Preview is available for PDF files. Use Download file to open this document in its desktop application.</div>)}
      </section>
    </div>
  )
}
