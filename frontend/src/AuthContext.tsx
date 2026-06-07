import React, { createContext, useContext, useState, useEffect, useCallback } from 'react'
import { API_URL } from './config'

interface User {
  id: string
  email: string
  name: string
  avatar_url: string
}

interface AuthContextValue {
  user: User | null
  token: string | null
  loading: boolean
  login: () => void
  logout: () => void
}

const AuthContext = createContext<AuthContextValue>({
  user: null, token: null, loading: true,
  login: () => {}, logout: () => {},
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user,    setUser]    = useState<User | null>(null)
  const [token,   setToken]   = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  // On mount: check URL for ?token= (after OAuth redirect) or load from localStorage
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const urlToken = params.get('token')

    if (urlToken) {
      // Coming back from Google OAuth — store and clean URL
      localStorage.setItem('pb-token', urlToken)
      window.history.replaceState({}, '', window.location.pathname)
      loadUser(urlToken)
    } else {
      const saved = localStorage.getItem('pb-token')
      if (saved) {
        loadUser(saved)
      } else {
        setLoading(false)
      }
    }
  }, [])

  const loadUser = useCallback(async (t: string) => {
    try {
      const res = await fetch(`${API_URL}/auth/me`, {
        headers: { Authorization: `Bearer ${t}` },
      })
      if (res.ok) {
        const data = await res.json()
        setUser(data)
        setToken(t)
      } else {
        // Token invalid or expired — clear it
        localStorage.removeItem('pb-token')
        sessionStorage.removeItem('pb-session-only')
        sessionStorage.removeItem('pb-cleared-at')
      }
    } catch {
      // Network error — keep token, will retry on next action
      setToken(t)
    } finally {
      setLoading(false)
    }
  }, [])

  const login = useCallback(() => {
    window.location.href = `${API_URL}/auth/google`
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem('pb-token')
    sessionStorage.removeItem('pb-session-only')
    sessionStorage.removeItem('pb-cleared-at')
    setUser(null)
    setToken(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, token, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}
