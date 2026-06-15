import { useState, useEffect } from 'react'
import { api } from '../services/api'
import { Users, Edit2, Key, Plus, Check, X, AlertCircle, Eye, EyeOff } from 'lucide-react'

const ALL_ROLES = ['EMPLOYEE', 'MANAGER', 'HR', 'FINANCE', 'IT_ADMIN', 'EXECUTIVE']
const PRIMARY_ROLES = ['employee', 'manager', 'admin']
const DEPARTMENTS = [
  'Engineering', 'Human Resources', 'Finance', 'Information Technology',
  'Operations', 'Sales', 'Marketing', 'Legal', 'Product', 'Executive', 'Other',
]

// Password must be ≥8 chars, contain uppercase, lowercase, digit, special char
const PWD_REGEX = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$/
const PWD_HINT = 'Min 8 chars, with uppercase, lowercase, number & special character.'

function Badge({ text, color = 'indigo' }) {
  const colors = {
    indigo: 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20',
    green: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20',
    amber: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
    red: 'bg-red-500/10 text-red-300 border-red-500/20',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full border font-medium ${colors[color]}`}>
      {text}
    </span>
  )
}

function PasswordInput({ value, onChange, placeholder = 'Password', minLength = 8, showHint = false }) {
  const [show, setShow] = useState(false)
  const valid = !value || PWD_REGEX.test(value)
  return (
    <div className="space-y-1">
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          placeholder={placeholder}
          value={value}
          onChange={onChange}
          minLength={minLength}
          className={`w-full bg-[#0f1520] border rounded-lg px-3 py-2 pr-9 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-brand-500/50
            ${!valid && value ? 'border-red-500/50' : 'border-slate-700/50'}`}
        />
        <button
          type="button"
          onClick={() => setShow(v => !v)}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
          tabIndex={-1}>
          {show ? <EyeOff size={13} /> : <Eye size={13} />}
        </button>
      </div>
      {showHint && <div className="text-xs text-slate-600">{PWD_HINT}</div>}
      {!valid && value && <div className="text-xs text-red-400">{PWD_HINT}</div>}
    </div>
  )
}

export default function UsersAdmin() {
  const [users, setUsers] = useState([])
  const [loading, setLoading] = useState(true)
  const [editingId, setEditingId] = useState(null)
  const [editRole, setEditRole] = useState('')
  const [editAccessRoles, setEditAccessRoles] = useState([])
  const [resetUserId, setResetUserId] = useState(null)
  const [newPassword, setNewPassword] = useState('')
  const [showCreate, setShowCreate] = useState(false)
  const [createForm, setCreateForm] = useState({
    email: '', username: '', department: '', role: 'employee', access_roles: ['EMPLOYEE'], password: ''
  })
  const [msg, setMsg] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => { fetchUsers() }, [])

  async function fetchUsers() {
    setLoading(true)
    try { setUsers(await api.listUsers()) }
    catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  function flash(m, isError = false) {
    if (isError) setError(m); else setMsg(m)
    setTimeout(() => { setMsg(null); setError(null) }, 4000)
  }

  function startEdit(u) {
    setEditingId(u.user_id)
    setEditRole(u.role)
    setEditAccessRoles(u.access_roles || [])
  }

  async function saveRoles(userId) {
    try {
      await api.updateUserRoles(userId, { role: editRole, access_roles: editAccessRoles })
      flash('Roles updated successfully')
      setEditingId(null)
      fetchUsers()
    } catch (e) { flash(e.message, true) }
  }

  async function handleResetPassword(userId) {
    if (!newPassword) { flash('Password required', true); return }
    if (!PWD_REGEX.test(newPassword)) { flash(PWD_HINT, true); return }
    try {
      const res = await api.adminResetPassword(userId, newPassword)
      flash(`Temp password set. Share with user: ${res.temp_password}`)
      setResetUserId(null)
      setNewPassword('')
    } catch (e) { flash(e.message, true) }
  }

  async function handleCreateUser() {
    if (!createForm.email || !createForm.username || !createForm.password) {
      flash('Email, username and password are required', true); return
    }
    if (!PWD_REGEX.test(createForm.password)) {
      flash(PWD_HINT, true); return
    }
    try {
      await api.createUser(createForm)
      flash(`User ${createForm.email} created`)
      setShowCreate(false)
      setCreateForm({ email: '', username: '', department: '', role: 'employee', access_roles: ['EMPLOYEE'], password: '' })
      fetchUsers()
    } catch (e) { flash(e.message, true) }
  }

  function toggleAccessRole(role) {
    setEditAccessRoles(prev => prev.includes(role) ? prev.filter(r => r !== role) : [...prev, role])
  }

  if (loading) return <div className="text-slate-500 text-sm p-4">Loading users...</div>

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-slate-300 font-medium">
          <Users size={16} /> {users.length} Users
        </div>
        <button onClick={() => setShowCreate(!showCreate)} className="btn-primary text-sm">
          <Plus size={14} /> Add User
        </button>
      </div>

      {msg && <div className="flex items-center gap-2 text-sm text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-3 py-2"><Check size={14} />{msg}</div>}
      {error && <div className="flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2"><AlertCircle size={14} />{error}</div>}

      {/* Create user form */}
      {showCreate && (
        <div className="card p-4 space-y-3 border border-indigo-500/30">
          <div className="text-sm font-medium text-slate-200">New User</div>
          <div className="grid grid-cols-2 gap-3">
            <input type="email" placeholder="Work email" value={createForm.email}
              onChange={e => setCreateForm(p => ({...p, email: e.target.value}))}
              className="bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-brand-500/50" />
            <input type="text" placeholder="Full name" value={createForm.username}
              onChange={e => setCreateForm(p => ({...p, username: e.target.value}))}
              className="bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-brand-500/50" />
            {/* Department dropdown */}
            <select value={createForm.department}
              onChange={e => setCreateForm(p => ({...p, department: e.target.value}))}
              className="bg-[#0f1520] border border-slate-700/50 rounded-lg px-3 py-2 text-sm text-slate-100 focus:outline-none focus:ring-1 focus:ring-brand-500/50">
              <option value="">Department (optional)</option>
              {DEPARTMENTS.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            {/* Password with eye + hints */}
            <PasswordInput
              value={createForm.password}
              onChange={e => setCreateForm(p => ({...p, password: e.target.value}))}
              placeholder="Temp password"
              showHint
            />
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-400">Primary role:</span>
            {PRIMARY_ROLES.map(r => (
              <button key={r} onClick={() => setCreateForm(p => ({...p, role: r}))}
                className={`text-xs px-2.5 py-1 rounded-full border transition-colors ${createForm.role === r ? 'bg-indigo-500/20 border-indigo-500 text-indigo-300' : 'border-slate-700/50 text-slate-500 hover:border-slate-500'}`}>
                {r}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-slate-400">Access roles:</span>
            {ALL_ROLES.map(r => (
              <button key={r} onClick={() => setCreateForm(p => ({...p, access_roles: p.access_roles.includes(r) ? p.access_roles.filter(x => x !== r) : [...p.access_roles, r]}))}
                className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${createForm.access_roles.includes(r) ? 'bg-indigo-500/20 border-indigo-500 text-indigo-300' : 'border-slate-700/50 text-slate-500 hover:border-slate-500'}`}>
                {r}
              </button>
            ))}
          </div>
          <div className="flex gap-2">
            <button onClick={handleCreateUser} className="btn-primary text-sm"><Check size={14} /> Create</button>
            <button onClick={() => setShowCreate(false)} className="btn-ghost text-sm"><X size={14} /> Cancel</button>
          </div>
        </div>
      )}

      {/* Users table */}
      <div className="card overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-700/50">
              {['User', 'Role', 'Access Roles', 'Status', 'Actions'].map(h => (
                <th key={h} className="text-left text-xs text-slate-500 font-medium px-4 py-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <tr key={u.user_id} className="border-b border-slate-700/30 hover:bg-slate-800/20">
                <td className="px-4 py-3">
                  <div className="font-medium text-slate-200">{u.username}</div>
                  <div className="text-xs text-slate-500">{u.email}</div>
                  {u.department && <div className="text-xs text-slate-600">{u.department}</div>}
                </td>
                <td className="px-4 py-3">
                  {editingId === u.user_id ? (
                    <select value={editRole} onChange={e => setEditRole(e.target.value)}
                      className="bg-[#0f1520] border border-slate-700/50 rounded px-2 py-1 text-xs text-slate-200">
                      {PRIMARY_ROLES.map(r => <option key={r} value={r}>{r}</option>)}
                    </select>
                  ) : (
                    <Badge text={u.role} color={u.role === 'admin' ? 'amber' : 'indigo'} />
                  )}
                </td>
                <td className="px-4 py-3">
                  {editingId === u.user_id ? (
                    <div className="flex flex-wrap gap-1">
                      {ALL_ROLES.map(r => (
                        <button key={r} onClick={() => toggleAccessRole(r)}
                          className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${editAccessRoles.includes(r) ? 'bg-indigo-500/20 border-indigo-500 text-indigo-300' : 'border-slate-700/50 text-slate-500'}`}>
                          {r}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {(u.access_roles || []).map(r => <Badge key={r} text={r} color="indigo" />)}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3">
                  <Badge text={u.is_active ? 'Active' : 'Inactive'} color={u.is_active ? 'green' : 'red'} />
                  {u.must_change_password && <div className="text-xs text-amber-400 mt-1">Must change pwd</div>}
                </td>
                <td className="px-4 py-3">
                  <div className="flex items-center gap-2">
                    {editingId === u.user_id ? (
                      <>
                        <button onClick={() => saveRoles(u.user_id)} className="text-emerald-400 hover:text-emerald-300 p-1"><Check size={14} /></button>
                        <button onClick={() => setEditingId(null)} className="text-slate-500 hover:text-slate-300 p-1"><X size={14} /></button>
                      </>
                    ) : (
                      <>
                        <button onClick={() => startEdit(u)} title="Edit roles" className="text-slate-400 hover:text-slate-200 p-1"><Edit2 size={14} /></button>
                        <button onClick={() => { setResetUserId(u.user_id); setNewPassword('') }} title="Reset password" className="text-slate-400 hover:text-slate-200 p-1"><Key size={14} /></button>
                      </>
                    )}
                  </div>
                  {resetUserId === u.user_id && (
                    <div className="mt-2 space-y-1">
                      <PasswordInput
                        value={newPassword}
                        onChange={e => setNewPassword(e.target.value)}
                        placeholder="New temp password"
                      />
                      <div className="flex items-center gap-1">
                        <button onClick={() => handleResetPassword(u.user_id)} className="text-emerald-400 hover:text-emerald-300 p-1"><Check size={14} /></button>
                        <button onClick={() => setResetUserId(null)} className="text-slate-500 hover:text-slate-300 p-1"><X size={14} /></button>
                      </div>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
