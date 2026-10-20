import { createContext, useContext, useState, useCallback } from 'react'
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

  const login = useCallback(async (email, password) => {
    setLoading(true)
    setError(null)
    try {
      const res = await api.login({ email, password })
      const nextUser = { ...res.user, must_change_password: res.must_change_password }
      // Dashboard child effects can fetch before parent effects run. Commit
      // credentials before changing auth state or allowing navigation.
      localStorage.setItem('ekc_user', JSON.stringify(nextUser))
      localStorage.setItem('ekc_token', res.access_token)
      setToken(res.access_token)
      setUser(nextUser)
      return { user: res.user, must_change_password: res.must_change_password }
    } catch (e) {
      setError(e.message || 'Login failed')
      throw e
    } finally {
      setLoading(false)
    }
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem('ekc_token')
    localStorage.removeItem('ekc_user')
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
