import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Edit2, Package, Plus, Trash2, X } from 'lucide-react'
import { api } from '../services/api'

const displayName = doc => doc.source_file?.split(/[\\/]/).pop() || doc.title || 'Untitled document'

export default function BundlePanel({ bundles, selectedIds, onToggle, onRefresh, initialDocumentIds, loadError, disabled = false }) {
  const [editor, setEditor] = useState(null)
  const [name, setName] = useState('')
  const [documentIds, setDocumentIds] = useState([])
  const [documents, setDocuments] = useState([])
  const [loadingDocs, setLoadingDocs] = useState(false)
  const [search, setSearch] = useState('')
  const [repository, setRepository] = useState('All')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [deleting, setDeleting] = useState(null)
  const nameInput = useRef(null)

  useEffect(() => {
    if (!editor) return
    let active = true
    setLoadingDocs(true)
    api.getDocuments(null, 1000).then(result => {
      if (active) setDocuments(result.documents || [])
    }).catch(err => { if (active) setError(err.message) })
      .finally(() => { if (active) setLoadingDocs(false) })
    return () => { active = false }
  }, [editor])

  useEffect(() => {
    if (!editor && !deleting) return
    const previousFocus = document.activeElement
    if (editor) nameInput.current?.focus()
    return () => { previousFocus?.focus() }
  }, [editor, deleting])

  useEffect(() => {
    if (!editor && !deleting) return
    const onKey = event => {
      if (event.key === 'Escape' && !busy) { setEditor(null); setDeleting(null) }
      if (event.key === 'Tab') {
        const dialog = document.querySelector('[aria-labelledby="bundle-dialog-title"]')
        const controls = [...(dialog?.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled)') || [])]
        const first = controls[0], last = controls[controls.length - 1]
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('keydown', onKey) }
  }, [editor, deleting, busy])

  function openEditor(bundle = null) {
    setName(bundle?.name || '')
    setDocumentIds([...(bundle?.document_ids || initialDocumentIds || [])])
    setSearch(''); setRepository('All'); setError('')
    setEditor({ id: bundle?.bundle_id || null })
  }

  async function save(event) {
    event.preventDefault()
    if (!name.trim() || busy || loadingDocs) return
    setBusy(true); setError('')
    try {
      const body = { name: name.trim(), document_ids: documentIds }
      if (editor.id) await api.updateBundle(editor.id, body)
      else await api.createBundle(body)
      await onRefresh()
      setEditor(null)
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  async function remove() {
    setBusy(true); setError('')
    try {
      await api.deleteBundle(deleting.bundle_id)
      if (selectedIds.includes(deleting.bundle_id)) onToggle(deleting.bundle_id)
      await onRefresh()
      setDeleting(null)
    } catch (err) { setError(err.message) }
    finally { setBusy(false) }
  }

  const knownIds = new Set(documents.map(doc => doc.doc_id))
  const allDocuments = [...documents, ...documentIds.filter(id => !knownIds.has(id)).map(id => ({
    doc_id: id, title: 'Unavailable document', repository: 'Unavailable',
  }))]
  const visibleDocuments = allDocuments.filter(doc =>
    (repository === 'All' || doc.repository === repository) &&
    `${displayName(doc)} ${doc.repository || ''}`.toLowerCase().includes(search.toLowerCase()))

  return <>
    <button disabled={disabled} className="btn-primary w-full justify-center mb-3" onClick={() => openEditor()}>
      <Plus size={14} /> New bundle
    </button>
    {loadError && <p role="alert" className="text-xs text-red-400 px-2 mb-2">{loadError}</p>}
    <p className="px-2 py-1.5 text-xs text-slate-500">Select bundles to scope your query</p>
    {!bundles.length && !loadError && <p className="text-xs text-slate-500 px-2 py-3">Create a named collection of documents from any repository.</p>}
    {bundles.map(bundle => <div key={bundle.bundle_id}
      className={`rounded-lg mb-2 border p-2 ${selectedIds.includes(bundle.bundle_id) ? 'border-brand-500/50 bg-brand-600/15' : 'border-slate-700/50'}`}>
      <label className="flex gap-2 items-center cursor-pointer">
        <input type="checkbox" disabled={disabled || !bundle.document_ids.length} title={!bundle.document_ids.length ? 'Add documents before using this bundle' : undefined} checked={selectedIds.includes(bundle.bundle_id)} onChange={() => onToggle(bundle.bundle_id)} />
        <Package size={13} className="text-slate-500 shrink-0" />
        <span className="text-xs text-slate-200 break-words min-w-0 flex-1">{bundle.name}</span>
      </label>
      <div className="flex items-center justify-between mt-2 gap-1">
        <span className="text-xs text-slate-500">{bundle.document_ids.length} documents</span>
        <div className="flex gap-1">
          <button disabled={disabled} className="p-1.5 rounded hover:bg-slate-700 text-slate-400" aria-label={`Edit ${bundle.name}`} title="Rename or edit documents" onClick={() => openEditor(bundle)}><Edit2 size={13} /></button>
          <button disabled={disabled} className="p-1.5 rounded hover:bg-red-900/30 text-slate-400 hover:text-red-400" aria-label={`Delete ${bundle.name}`} title="Delete bundle" onClick={() => { setError(''); setDeleting(bundle) }}><Trash2 size={13} /></button>
        </div>
      </div>
    </div>)}
    {(editor || deleting) && createPortal(
      <div className="fixed inset-0 z-[100] bg-black/70 flex items-center justify-center p-4">
        <section role="dialog" aria-modal="true" aria-labelledby="bundle-dialog-title" className="w-full max-w-2xl max-h-[90vh] flex flex-col bg-[#141b29] rounded-xl border border-slate-700 shadow-2xl">
          <div className="flex justify-between items-center p-5 border-b border-slate-700/50">
            <h2 id="bundle-dialog-title" className="font-semibold text-slate-100">{deleting ? 'Delete bundle' : editor.id ? 'Edit bundle' : 'Create bundle'}</h2>
            <button aria-label="Close bundle dialog" className="btn-ghost" disabled={busy} onClick={() => { setEditor(null); setDeleting(null) }}><X size={18} /></button>
          </div>
          {deleting ? <div className="p-5">
            <p className="text-sm text-slate-300">Delete “{deleting.name}”? It will be removed from your active bundles. Its documents will remain available.</p>
            {error && <p role="alert" className="text-red-400 text-sm mt-3">{error}</p>}
            <div className="flex justify-end gap-2 mt-6">
              <button className="btn-ghost" disabled={busy} onClick={() => setDeleting(null)}>Cancel</button>
              <button className="btn-primary bg-red-600 hover:bg-red-500" disabled={busy} onClick={remove}>{busy ? 'Deleting…' : 'Delete bundle'}</button>
            </div>
          </div> : <form onSubmit={save} className="flex flex-col min-h-0">
            <div className="p-5 space-y-4 overflow-y-auto min-h-0">
              <label className="block text-sm text-slate-300">Bundle name
                <input ref={nameInput} className="input mt-2" required maxLength={255} value={name} onChange={event => setName(event.target.value)} placeholder="e.g. Finance and Engineering" />
              </label>
              <div className="flex gap-2">
                <input aria-label="Search bundle documents" className="input flex-1" placeholder="Search documents…" value={search} onChange={event => setSearch(event.target.value)} />
                <select aria-label="Filter bundle documents by repository" className="input w-40" value={repository} onChange={event => setRepository(event.target.value)}>
                  <option value="All">All repositories</option>
                  {[...new Set(documents.map(doc => doc.repository).filter(Boolean))].sort().map(repo => <option key={repo} value={repo}>{repo}</option>)}
                </select>
              </div>
              <div className="flex justify-between text-xs text-slate-400">
                <span>{documentIds.length} documents selected</span>
                <button type="button" onClick={() => setDocumentIds([])} className="hover:text-slate-100">Clear selection</button>
              </div>
              <div className="max-h-72 overflow-y-auto border border-slate-700 rounded-lg divide-y divide-slate-700/50">
                {loadingDocs ? <p className="p-4 text-sm text-slate-400">Loading documents…</p> : visibleDocuments.length ? visibleDocuments.map(doc => <label key={doc.doc_id} className="flex gap-3 items-start p-3 cursor-pointer hover:bg-slate-800">
                  <input className="mt-1" type="checkbox" checked={documentIds.includes(doc.doc_id)} onChange={() => setDocumentIds(ids => ids.includes(doc.doc_id) ? ids.filter(id => id !== doc.doc_id) : [...ids, doc.doc_id])} />
                  <span className="min-w-0"><span className="block text-sm text-slate-200 break-words">{displayName(doc)}</span><span className="text-xs text-slate-500">{doc.repository}</span></span>
                </label>) : <p className="p-4 text-sm text-slate-500">No matching documents.</p>}
              </div>
              {error && <p role="alert" className="text-sm text-red-400">{error}</p>}
            </div>
            <div className="p-5 border-t border-slate-700/50 flex justify-end gap-2">
              <button type="button" className="btn-ghost" disabled={busy} onClick={() => setEditor(null)}>Cancel</button>
              <button className="btn-primary" disabled={busy || loadingDocs || !name.trim()}>{busy ? 'Saving…' : editor.id ? 'Save changes' : 'Create bundle'}</button>
            </div>
          </form>}
        </section>
      </div>, document.body)}
  </>
}
