import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Brain, Loader2, AlertCircle, CheckCircle2 } from 'lucide-react'
import { useAuth } from '../context/AuthContext'
import { api } from '../services/api'

export default function ChangePassword() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [currentPwd, setCurrentPwd] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [confirmPwd, setConfirmPwd] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError(null)
    if (newPwd.length < 8) { setError('New password must be at least 8 characters'); return }
    if (newPwd !== confirmPwd) { setError('New passwords do not match'); return }
    if (newPwd === currentPwd) { setError('New password must differ from current password'); return }

    setLoading(true)
    try {
      await api.changePassword({ current_password: currentPwd, new_password: newPwd })
      setSuccess(true)
      setTimeout(() => navigate('/dashboard', { replace: true }), 2000)
    } catch (e) {
      setError(e.message || 'Failed to change password')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-[#0d1117] px-4">
      <div className="w-full max-w-md">
        <div className="flex flex-col items-center mb-8">
          <div className="w-12 h-12 rounded-xl bg-brand-600 flex items-center justify-center mb-3">
            <Brain size={24} className="text-white" />
          </div>
          <div className="text-xl font-semibold text-slate-100">Change Password</div>
          <div className="text-sm text-slate-500 mt-1">
            Logged in as {user?.email}
          </div>
        </div>

        <div className="card p-6">
          {success ? (
            <div className="flex flex-col items-center gap-3 py-4">
              <CheckCircle2 size={40} className="text-emerald-400" />
              <div className="text-slate-100 font-medium">Password changed successfully</div>
              <div className="text-sm text-slate-500">Redirecting to dashboard...</div>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">Current password</label>
                <input type="password" required value={currentPwd} onChange={e => setCurrentPwd(e.target.value)}
                  placeholder="Your current password"
                  className="w-full bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500/50" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">New password</label>
                <input type="password" required value={newPwd} onChange={e => setNewPwd(e.target.value)}
                  placeholder="At least 8 characters"
                  className="w-full bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500/50" />
              </div>
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">Confirm new password</label>
                <input type="password" required value={confirmPwd} onChange={e => setConfirmPwd(e.target.value)}
                  placeholder="Repeat new password"
                  className="w-full bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2.5 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-brand-500/50" />
              </div>

              {error && (
                <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2">
                  <AlertCircle size={14} className="flex-shrink-0" /> {error}
                </div>
              )}

              <button type="submit" disabled={loading} className="btn-primary w-full justify-center">
                {loading ? <Loader2 size={16} className="animate-spin" /> : null}
                {loading ? 'Saving...' : 'Set new password'}
              </button>

              <button type="button" onClick={() => navigate(-1)}
                className="btn-ghost w-full justify-center text-sm">
                Cancel
              </button>
            </form>
          )}
        </div>

      </div>
    </div>
  )
}