import { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { api } from '../services/api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem('ekc_token'))
  const [user, setUser] = useState(() => {
    const raw = localStorage.getItem('ekc_user')
    return raw ? JSON.parse(raw) : null
  })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (token) localStorage.setItem('ekc_token', token)
    else localStorage.removeItem('ekc_token')
  }, [token])

  useEffect(() => {
    if (user) localStorage.setItem('ekc_user', JSON.stringify(user))
    else localStorage.removeItem('ekc_user')
  }, [user])

  const login = useCallback(async (email, password) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.login({ email, password })
      setToken(res.access_token)
      setUser({ ...res.user, must_change_password: res.must_change_password })
      return { user: res.user, must_change_password: res.must_change_password }
    } catch (e) {
      setError(e.message || 'Login failed')
      throw e
    } finally {
      setLoading(false)
    }
  }, [])

  const logout = useCallback(() => {
    setToken(null)
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ token, user, login, logout, loading, error, isAuthenticated: !!token }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}

// Lets api.js read the current token without importing the React context
export function getStoredToken() {
  return localStorage.getItem('ekc_token')
}