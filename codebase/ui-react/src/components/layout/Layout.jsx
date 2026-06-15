import React from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import { LayoutDashboard, FolderOpen, FileText, Ticket, MessageSquare, Settings, Brain, LogOut, KeyRound, ShieldCheck } from 'lucide-react'
import clsx from 'clsx'
import { useAuth } from '../../context/AuthContext'
import logo from './logo.png'



const NAV = [
  { to: '/dashboard',    icon: LayoutDashboard, label: 'Home' },
  { to: '/repositories', icon: FolderOpen,       label: 'Repositories' },
  { to: '/documents',    icon: FileText,          label: 'Documents' },
  { to: '/tickets',      icon: Ticket,            label: 'Tickets' },
  { to: '/chat',         icon: MessageSquare,     label: 'Chat Assistant' },
]

const ADMIN_ROLES = ['ADMIN', 'IT_ADMIN', 'EXECUTIVE']

export default function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const [showPwdReminder, setShowPwdReminder] = React.useState(
    () => sessionStorage.getItem('show_pwd_reminder') === '1'
  )

  function dismissReminder() {
    sessionStorage.removeItem('show_pwd_reminder')
    setShowPwdReminder(false)
  }

  const [showLogoutConfirm, setShowLogoutConfirm] = React.useState(false)

  function handleLogout() {
    logout()
    navigate('/')
  }

  const userRoles = new Set([
    (user?.role || '').toUpperCase(),
    ...(user?.access_roles || []).map(r => r.toUpperCase()),
  ])
  const isAdmin = ADMIN_ROLES.some(r => userRoles.has(r))

  const navItems = isAdmin
    ? [...NAV, { to: '/admin', icon: Settings, label: 'Admin' }]
    : [...NAV, { to: '/access-requests', icon: ShieldCheck, label: 'Request Access' }]

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[#0d1117]">
      {/* Top bar */}
      <header className="flex-shrink-0 border-b border-slate-700/50 bg-[#0f1520]">
        <div className="flex items-center justify-between px-5 h-16">
          {/* Logo */}
          <div className="flex items-center gap-3 flex-shrink-0">
            <div className="w-8 h-8 rounded-lg  flex items-center justify-center">
              {/* <Brain size={16} className="text-white" /> */}
                <img src={logo} alt="AI Assistant"/>
            </div>
            <div>
              <div className="text-sm font-semibold text-slate-100 leading-tight">Core I7 Enterprise Knowledge Copilot</div>
              <div className="text-xs text-brand-400 font-medium leading-tight"></div>
            </div>
          </div>

          {/* Nav */}
          <nav className="flex items-center gap-1 overflow-x-auto">
            {navItems.map(({ to, icon: Icon, label }) => (
              <NavLink key={to} to={to} className={({ isActive }) =>
                clsx('topnav-item', isActive && 'active')
              }>
                <Icon size={15} />
                {label}
              </NavLink>
            ))}
          </nav>

          {/* User menu */}
          <div className="flex items-center gap-3 flex-shrink-0">
            {user && (
              <div className="text-right hidden sm:block">
                <div className="text-xs font-medium text-slate-300 leading-tight">{user.username}</div>
                <div className="text-xs text-slate-600 leading-tight">{user.role}</div>
              </div>
            )}
            <NavLink to="/change-password" title="Change password"
              className="text-slate-500 hover:text-slate-200 p-1.5 rounded-md hover:bg-slate-700/50 transition-colors">
              <KeyRound size={15} />
            </NavLink>
            <button onClick={() => setShowLogoutConfirm(true)} title="Sign out"
              className="text-slate-500 hover:text-slate-200 p-1.5 rounded-md hover:bg-slate-700/50 transition-colors">
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </header>

      {/* Logout confirmation */}
      {showLogoutConfirm && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4" onClick={() => setShowLogoutConfirm(false)}>
          <div className="card p-6 max-w-sm w-full" onClick={e => e.stopPropagation()}>
            <div className="text-base font-semibold text-slate-100 mb-2">Sign out?</div>
            <p className="text-sm text-slate-400 mb-5">You'll need to sign in again to access the Copilot.</p>
            <div className="flex gap-2">
              <button onClick={handleLogout} className="flex-1 py-2 rounded-lg text-sm font-medium bg-red-600 hover:bg-red-700 text-white transition-colors">
                Sign out
              </button>
              <button onClick={() => setShowLogoutConfirm(false)} className="btn-ghost text-sm">Cancel</button>
            </div>
          </div>
        </div>
      )}

      {/* Main content */}
      <main className="flex-1 overflow-y-auto">
        {showPwdReminder && (
          <div className="bg-amber-500/10 border-b border-amber-500/30 px-6 py-2.5 flex items-center justify-between">
            <span className="text-sm text-amber-300">
              Your password was set by an admin. Consider changing it —{' '}
              <NavLink to="/change-password" className="underline hover:text-amber-200" onClick={dismissReminder}>
                change now
              </NavLink>
            </span>
            <button onClick={dismissReminder} className="text-amber-400 hover:text-amber-200 text-xs ml-4">
              Dismiss
            </button>
          </div>
        )}
        <Outlet />
      </main>
    </div>
  )
}