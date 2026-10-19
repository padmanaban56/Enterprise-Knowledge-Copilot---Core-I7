import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../services/api'

const basename = value => (value || '').replaceAll('\\', '/').split('/').pop()

export default function DocumentSource() {
  const [params] = useSearchParams()
  const docId = params.get('doc_id')
  const source = params.get('source') || ''
  const repository = params.get('repository')
  const page = Number(params.get('page'))
  const [state, setState] = useState({ loading: true })

  useEffect(() => {
    let active = true
    let objectUrl
    async function load() {
      setState({ loading: true })
      let resolvedId = docId
      let doc
      try {
        // Older saved responses may only contain a filename. Resolve an exact,
        // accessible match; never open an arbitrary document with a similar name.
        if (!resolvedId) {
          if (!source) throw new Error('This citation has no document reference.')
          const result = await api.getDocuments(null, 1000)
          const matches = result.documents.filter(item =>
            basename(item.source_file) === basename(source) &&
            (!repository || item.repository === repository))
          if (matches.length !== 1) throw new Error(matches.length
            ? 'More than one document matches this saved citation. Select the document from Documents.'
            : 'The cited document is no longer available or you do not have access.')
          resolvedId = matches[0].doc_id
        }
        doc = await api.getDocument(resolvedId)
        const blob = await api.getDocumentFile(resolvedId)
        if (!active) return
        objectUrl = URL.createObjectURL(blob)
        setState({ loading: false, doc, docId: resolvedId, url: objectUrl,
          pdf: blob.type === 'application/pdf' || doc.source_type?.toUpperCase() === 'PDF' })
      } catch (error) {
        if (active) setState({ loading: false, error: error.message, doc, docId: resolvedId })
      }
    }
    load()
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [docId, source, repository])

  const name = basename(state.doc?.source_file) || source || 'Source document'
  const pageHash = Number.isInteger(page) && page > 0 ? `#page=${page}` : ''
  return (
    <div className="p-4 flex flex-col gap-4 h-full min-h-[80vh]">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <h1 className="text-lg text-slate-200 font-semibold break-all">{name}</h1>
        <div className="flex gap-4 text-sm text-brand-300">
          {state.doc && <Link to={`/documents/${encodeURIComponent(state.docId)}`}>Document details</Link>}
          {state.url && <a href={state.url} download={name}>Download file</a>}
        </div>
      </div>
      {state.loading && <p role="status" className="text-slate-400">Opening source document…</p>}
      {state.error && <div role="alert" className="card p-4 text-slate-300">
        <p>{state.error}</p>
        {state.doc && <p className="mt-2">You can still read the indexed content in Document details.</p>}
        {!state.doc && <Link className="text-brand-300 underline" to="/documents">Browse documents</Link>}
      </div>}
      {state.url && (state.pdf
        ? <iframe title={name} src={`${state.url}${pageHash}`} className="w-full flex-1 min-h-[75vh] rounded-lg bg-white" />
        : <div className="card p-6 text-slate-300">This file can be opened in its desktop application. <a className="text-brand-300 underline" href={state.url} download={name}>Download {name}</a></div>)}
    </div>
  )
}
