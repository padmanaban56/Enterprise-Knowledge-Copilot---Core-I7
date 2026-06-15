import { useState } from 'react'
import { useNavigate, useLocation, Link } from 'react-router-dom'
import { Loader2, AlertCircle, Eye, EyeOff } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import logo from '../components/layout/logo.png'

export default function Login() {
  const { login, loading, error } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)

  const from = location.state?.from?.pathname || '/dashboard'

  async function handleSubmit(e) {
    e.preventDefault()
    try {
      const result = await login(email, password)
      navigate(from, { replace: true })
      if (result.must_change_password) {
        sessionStorage.setItem('show_pwd_reminder', '1')
      }
    } catch {
      // error surfaced via useAuth().error
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0d1117] px-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 rounded-xl flex items-center justify-center mb-3">
            <img src={logo} alt="AI Assistant"/>
          </div>
          <div className="text-xl font-semibold text-slate-100">Core I7 Enterprise Knowledge Copilot</div>
          <div className="text-sm text-slate-500 mt-1">Sign in to continue</div>
        </div>

        {/* Card */}
        <div className="card p-6 animate-fade-in">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Work email</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                className="w-full bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2.5
                           text-sm text-slate-100 placeholder:text-slate-600
                           focus:outline-none focus:ring-2 focus:ring-brand-500/50 focus:border-brand-500/50"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5">Password</label>
              <div className="relative">
                <input
                  type={showPassword ? 'text' : 'password'}
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2.5 pr-10
                             text-sm text-slate-100 placeholder:text-slate-600
                             focus:outline-none focus:ring-2 focus:ring-brand-500/50 focus:border-brand-500/50"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300 transition-colors"
                  tabIndex={-1}>
                  {showPassword ? <EyeOff size={15} /> : <Eye size={15} />}
                </button>
              </div>
            </div>

            {error && (
              <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                <AlertCircle size={14} className="flex-shrink-0" />
                {error}
              </div>
            )}

            <button type="submit" disabled={loading} className="btn-primary w-full justify-center">
              {loading ? <Loader2 size={16} className="animate-spin" /> : null}
              {loading ? 'Signing in...' : 'Sign in'}
            </button>
          </form>

          <p className="text-xs text-slate-600 text-center mt-5">
            Demo credentials: <span className="text-slate-400">demo@company.com</span> /{' '}
            <span className="text-slate-400">password123</span>
          </p>
        </div>

        <p className="text-center text-xs text-slate-600 mt-6">
          <Link to="/" className="hover:text-slate-400">Back to home</Link>
        </p>
      </div>
    </div>
  )
}
