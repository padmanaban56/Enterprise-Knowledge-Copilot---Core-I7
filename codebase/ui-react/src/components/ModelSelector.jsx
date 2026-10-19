import { useEffect, useRef, useState } from 'react'
import { ChevronDown, RefreshCw } from 'lucide-react'
import { api } from '../services/api'

function price(model) {
  if (model.provider === 'ollama') return 'Local'
  const prompt = Number(model.pricing?.prompt), completion = Number(model.pricing?.completion)
  if (!Number.isFinite(prompt) || !Number.isFinite(completion)) return 'Pricing unavailable'
  if (prompt === 0 && completion === 0) return 'Free'
  return `$${(prompt * 1e6).toFixed(2)} in / $${(completion * 1e6).toFixed(2)} out per 1M tokens`
}

export default function ModelSelector({ value, onChange, disabled }) {
  const [models, setModels] = useState([])
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const [openWeights, setOpenWeights] = useState(true)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [configured, setConfigured] = useState(true)
  const root = useRef(null)
  async function refresh() {
    setLoading(true); setError('')
    try {
      const result = await api.getModels()
      const allowed = (result.models || []).filter(model => model.provider === 'ollama' || (
        model.id.endsWith(':free') && model.pricing?.prompt != null && model.pricing?.completion != null &&
        Object.values(model.pricing).every(cost => cost !== '' && Number.isFinite(Number(cost)) && Number(cost) === 0)))
      setModels(allowed)
      setConfigured(result.openrouter_configured)
      setError((result.errors || []).join(' '))
    } catch (err) { setError(err.message) }
    finally { setLoading(false) }
  }
  useEffect(() => { refresh() }, [])
  useEffect(() => {
    if (disabled || !value || !models.length || models.some(model => `${model.provider}|${model.id}` === value)) return
    const fallback = (value.startsWith('openrouter|') && models.find(model => model.id === 'liquid/lfm-2.5-2.6b:free')) || models.find(model => model.provider === 'ollama') || models[0]
    onChange(`${fallback.provider}|${fallback.id}`)
  }, [models, value, disabled])
  useEffect(() => {
    const dismiss = event => { if (!root.current?.contains(event.target)) setOpen(false) }
    const escape = event => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', dismiss)
    document.addEventListener('keydown', escape)
    return () => { document.removeEventListener('mousedown', dismiss); document.removeEventListener('keydown', escape) }
  }, [])
  const chosen = models.find(model => `${model.provider}|${model.id}` === value)
  const visible = models.filter(model =>
    (!openWeights || model.provider === 'ollama' || model.open_weights) &&
    `${model.name} ${model.id}`.toLowerCase().includes(search.toLowerCase()))
  return <div className="relative" ref={root}>
    <button type="button" disabled={disabled} aria-expanded={open} aria-label="Choose answer model"
      className="flex gap-2 items-center text-xs px-3 py-2 rounded-lg border border-slate-600 text-slate-200 disabled:opacity-50 max-w-64"
      onClick={() => setOpen(!open)}>
      <span className="truncate">{chosen?.name || value.split('|')[1] || 'Default local model'}</span><ChevronDown size={13} />
    </button>
    {open && <section aria-label="Answer model selection" className="absolute right-0 top-full mt-2 w-[min(26rem,85vw)] z-50 rounded-xl border border-slate-600 bg-[#141b29] shadow-2xl p-3">
      <div className="flex justify-between items-center mb-2"><h3 className="text-sm text-slate-100 font-medium">Free answer models</h3>
        <button aria-label="Refresh models" className="btn-ghost" disabled={loading} onClick={refresh}><RefreshCw size={13} /></button></div>
      <input autoFocus aria-label="Search models" className="input mb-2" placeholder="Search model name or ID…" value={search} onChange={event => setSearch(event.target.value)} />
      <label className="flex gap-2 items-center text-xs text-slate-400 mb-3"><input type="checkbox" checked={openWeights} onChange={event => setOpenWeights(event.target.checked)} />Open-weight models only</label>
      {!configured && <p className="text-xs text-amber-400 mb-2">OpenRouter needs an API key configured on the server.</p>}
      {error && <p role="alert" className="text-xs text-amber-400 mb-2">{error}</p>}
      <div className="max-h-72 overflow-y-auto space-y-1">
        {loading && <p className="text-sm text-slate-400 p-3">Loading models…</p>}
        {!loading && !visible.length && <p className="text-sm text-slate-500 p-3">No matching models.</p>}
        {visible.map(model => <button key={`${model.provider}|${model.id}`} type="button"
          aria-pressed={value === `${model.provider}|${model.id}`}
          className={`w-full text-left p-2 rounded-lg hover:bg-slate-700/60 ${value === `${model.provider}|${model.id}` ? 'bg-brand-600/20' : ''}`}
          onClick={() => { onChange(`${model.provider}|${model.id}`); setOpen(false) }}>
          <span className="block text-sm text-slate-200">{model.name}</span>
          <span className="block text-xs text-slate-500 break-all">{model.provider === 'ollama' ? 'Ollama' : 'OpenRouter'} · {model.id}</span>
          <span className="block text-xs text-slate-400 mt-1">{price(model)}</span>
        </button>)}
      </div>
      {value && <button type="button" className="btn-ghost text-xs w-full mt-2" onClick={() => { onChange(''); setOpen(false) }}>Clear model selection (use local default)</button>}
      <p className="text-xs text-slate-500 mt-3">OpenRouter sends your question and retrieved context to the selected model provider.</p>
    </section>}
  </div>
}
