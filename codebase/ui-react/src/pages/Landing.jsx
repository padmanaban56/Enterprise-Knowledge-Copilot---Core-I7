import { Link } from 'react-router-dom'
import {
  Brain, Search, Ticket, Bot, ShieldCheck, FileSearch,
  ArrowRight, CheckCircle2,
} from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import logo from '../components/layout/logo.png'

const FEATURES = [
  {
    icon: FileSearch,
    title: 'Hybrid RAG Search',
    desc: 'Dense + keyword + question-vector retrieval with cross-encoder reranking, source citations, and confidence scoring on every answer.',
  },
  {
    icon: Ticket,
    title: 'Ticket Intelligence',
    desc: 'Exact-match, full-text, and semantic ticket lookup with automatic linking to related incidents and resolutions.',
  },
  {
    icon: Bot,
    title: 'Agentic Assistant',
    desc: 'A ReAct-style agent that plans, searches documents, looks up tickets, and summarizes — with full step-by-step transparency.',
  },
  {
    icon: ShieldCheck,
    title: 'Enterprise RBAC & PII Redaction',
    desc: 'Role-based document access and automatic PII redaction at ingestion.',
  },
]

const HIGHLIGHTS = [
  'Cites every answer with source, section & page',
  'Escalates to a human or raises a ticket when unsure',
  'Knowledge gap detection & feedback-driven learning',
  'Admin console for uploads, analytics & user management',
]

export default function Landing() {
  const { isAuthenticated } = useAuth()

  return (
    <div className="min-h-screen bg-[#0d1117] text-slate-100">
      {/* Nav */}
      <header className="border-b border-slate-700/50">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-lg  flex items-center justify-center">
              {/* <Brain size={16} className="text-white" /> */}
                 <img src={logo} alt="AI Assistant"/>
            </div>
            <span className="font-semibold">Core I7 Enterprise Knowledge Copilot</span>
          </div>
          <nav className="flex items-center gap-3">
            {isAuthenticated ? (
              <Link to="/dashboard" className="btn-primary">
                Go to Dashboard <ArrowRight size={16} />
              </Link>
            ) : (
              <>
                <Link to="/login" className="btn-primary">
                  Sign In <ArrowRight size={16} />
                </Link>
              </>
            )}
          </nav>
        </div>
      </header>

      {/* Hero */}
      <section className="max-w-6xl mx-auto px-6 pt-20 pb-16 text-center">
        {/* <div className="inline-flex items-center gap-2 text-xs font-medium text-brand-400 bg-brand-500/10 border border-brand-500/20 rounded-full px-3 py-1 mb-6">
          <Search size={12} /> RAG + Agentic Workflow
        </div> */}
        <h1 className="text-4xl md:text-5xl font-bold leading-tight max-w-3xl mx-auto">
          Ask anything about your company's
          <span className="text-brand-400"> docs, tickets & policies</span>
        </h1>
        <p className="text-slate-400 mt-5 max-w-xl mx-auto">
          One copilot that searches scattered documentation, retrieves the
          right answer with citations, summarizes it, and escalates when it's
          not confident — so your team stops digging through wikis and tickets.
        </p>
        <div className="flex items-center justify-center gap-3 mt-8">
          <Link to={isAuthenticated ? '/chat' : '/login'} className="btn-primary text-base px-6 py-3">
            {isAuthenticated ? 'Open Chat Assistant' : 'Sign in to start'} <ArrowRight size={18} />
          </Link>
        </div>
      </section>

      {/* Feature grid */}
      <section className="max-w-6xl mx-auto px-6 pb-16">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {FEATURES.map(({ icon: Icon, title, desc }) => (
            <div key={title} className="card-hover p-5">
              <div className="w-10 h-10 rounded-lg bg-brand-500/10 flex items-center justify-center mb-3">
                <Icon size={18} className="text-brand-400" />
              </div>
              <div className="text-sm font-semibold text-slate-100 mb-1.5">{title}</div>
              <div className="text-xs text-slate-400 leading-relaxed">{desc}</div>
            </div>
          ))}
        </div>
      </section>

      {/* Highlights */}
      <section className="max-w-6xl mx-auto px-6 pb-24">
        <div className="card p-8 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
          {HIGHLIGHTS.map((h) => (
            <div key={h} className="flex items-start gap-3">
              <CheckCircle2 size={18} className="text-brand-400 flex-shrink-0 mt-0.5" />
              <span className="text-sm text-slate-300">{h}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-slate-700/50">
        <div className="max-w-6xl mx-auto px-6 py-6 flex flex-col md:flex-row items-center justify-between gap-2 text-xs text-slate-600">
          <span>Core i7 | Enterprise Knowledge Copilot · Built for NASSCOM Hackathon Use Case 2</span>
          <span>RAG + Agentic Workflow · Hybrid Retrieval · RBAC · PII Redaction</span>
        </div>
      </footer>
    </div>
  )
}
