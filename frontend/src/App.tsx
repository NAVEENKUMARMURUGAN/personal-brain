import React, { useCallback, useEffect, useRef, useState } from 'react'
import Chat from './components/Chat'
import RightPanel from './components/RightPanel'
import TasksPage from './components/TasksPage'
import KnowledgePage from './components/KnowledgePage'
import LoginPage from './components/LoginPage'
import SettingsPage from './components/SettingsPage'
import DashboardPage from './components/DashboardPage'
import ExplorePage from './components/ExplorePage'
import { AuthProvider, useAuth } from './AuthContext'
import { API_URL } from './config'
import './App.css'

type Theme = 'dark' | 'light'

type NavPage = 'dashboard' | 'chat' | 'knowledge' | 'tasks' | 'settings' | 'explore'

interface SystemEvent {
  level: 'OK' | 'INFO' | 'WARN'
  message: string
}

interface Notification {
  id: string
  type: string
  title: string
  body: string
  source: string
  read: number
  created_at: string
}

function getInitialTheme(): Theme {
  try {
    const saved = localStorage.getItem('pb-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch {}
  return 'dark'
}

const NAV_ITEMS: { id: NavPage; icon: string; label: string }[] = [
  { id: 'dashboard', icon: '⬡', label: 'Dashboard'  },
  { id: 'chat',      icon: '◈', label: 'Chat'       },
  { id: 'knowledge', icon: '◫', label: 'Knowledge'  },
  { id: 'tasks',     icon: '◻', label: 'Tasks'      },
  { id: 'explore',   icon: '✦', label: 'Explore'    },
  { id: 'settings',  icon: '⚙', label: 'Settings'   },
]

function AppShell() {
  const { user, loading, logout } = useAuth()

  if (loading) {
    return (
      <div style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'var(--bg-page)', color: 'var(--text-faint)', fontFamily: 'var(--mono-font)', fontSize: '13px' }}>
        Loading…
      </div>
    )
  }

  if (!user) {
    return <LoginPage />
  }

  return <AppMain onLogout={logout} user={user} />
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  )
}

function NotificationBell({ token }: { token: string | null }) {
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)

  const fetchNotifications = useCallback(async () => {
    if (!token) return
    try {
      const res = await fetch(`${API_URL}/notifications?unread_only=true&limit=20`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        setNotifications(data.notifications ?? [])
      }
    } catch { /* silent */ }
  }, [token])

  // Poll every 30 seconds
  useEffect(() => {
    fetchNotifications()
    const interval = setInterval(fetchNotifications, 30_000)
    return () => clearInterval(interval)
  }, [fetchNotifications])

  // Close dropdown on outside click
  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const handleOpen = async () => {
    setOpen(o => !o)
    if (!open && notifications.length > 0 && token) {
      // Mark all as read when opening
      await fetch(`${API_URL}/notifications/read`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      })
      // Clear after a short delay so user sees them first
      setTimeout(() => setNotifications([]), 3000)
    }
  }

  const unreadCount = notifications.length

  const formatTime = (iso: string) => {
    try {
      const d = new Date(iso)
      const now = new Date()
      const diffMs = now.getTime() - d.getTime()
      const diffMins = Math.floor(diffMs / 60000)
      if (diffMins < 1) return 'just now'
      if (diffMins < 60) return `${diffMins}m ago`
      if (diffMins < 1440) return `${Math.floor(diffMins / 60)}h ago`
      return d.toLocaleDateString()
    } catch { return '' }
  }

  return (
    <div ref={panelRef} style={{ position: 'relative' }}>
      <button
        className="app__session-icon"
        title="Notifications"
        onClick={handleOpen}
        style={{ position: 'relative' }}
      >
        🔔
        {unreadCount > 0 && (
          <span style={{
            position: 'absolute', top: '-4px', right: '-4px',
            background: '#f87171', color: '#fff', borderRadius: '50%',
            fontSize: '9px', fontWeight: 700, lineHeight: 1,
            minWidth: '16px', height: '16px', display: 'flex',
            alignItems: 'center', justifyContent: 'center',
            padding: '0 3px', fontFamily: 'var(--mono-font)',
          }}>
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div style={{
          position: 'absolute', top: '32px', right: 0, zIndex: 100,
          width: '300px', background: 'var(--bg-card)',
          border: '1px solid var(--border)', borderRadius: '8px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.3)',
          fontFamily: 'var(--mono-font)',
        }}>
          <div style={{
            padding: '10px 14px', borderBottom: '1px solid var(--border)',
            fontSize: '11px', fontWeight: 600, color: 'var(--text-faint)',
            textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>
            Notifications
          </div>

          {notifications.length === 0 ? (
            <div style={{ padding: '20px 14px', fontSize: '12px', color: 'var(--text-faint)', textAlign: 'center' }}>
              No new notifications
            </div>
          ) : (
            <div style={{ maxHeight: '320px', overflowY: 'auto' }}>
              {notifications.map(n => (
                <div key={n.id} style={{
                  padding: '10px 14px', borderBottom: '1px solid var(--border)',
                  display: 'flex', gap: '10px', alignItems: 'flex-start',
                }}>
                  <span style={{ fontSize: '14px', marginTop: '1px' }}>
                    {n.type === 'task_added' ? '◻' : '◈'}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text-primary)', marginBottom: '2px' }}>
                      {n.title}
                    </div>
                    <div style={{ fontSize: '11px', color: 'var(--text-faint)', lineHeight: '1.4',
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {n.body}
                    </div>
                    <div style={{ fontSize: '10px', color: 'var(--text-faint)', marginTop: '4px' }}>
                      via Telegram · {formatTime(n.created_at)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function AppMain({ onLogout, user }: { onLogout: () => void; user: { name: string; email: string; avatar_url: string } }) {
  const { token } = useAuth()
  const [theme, setTheme] = useState<Theme>(getInitialTheme)
  const [page, setPage] = useState<NavPage>('dashboard')
  const [pendingChatMessage, setPendingChatMessage] = useState<string>('')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [systemEvents, setSystemEvents] = useState<SystemEvent[]>([
    { level: 'OK',   message: 'Index update complete' },
    { level: 'INFO', message: '24 new references synced' },
  ])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('pb-theme', theme) } catch {}
  }, [theme])

  // Listen for dashboard → chat navigation events
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent<{ message: string }>).detail
      if (detail?.message) {
        setPendingChatMessage(detail.message)
      }
      setPage('chat')
    }
    window.addEventListener('pb:navigate-chat', handler)
    return () => window.removeEventListener('pb:navigate-chat', handler)
  }, [])

  const addEvent = useCallback((level: SystemEvent['level'], message: string) => {
    setSystemEvents((prev) => [...prev.slice(-19), { level, message }])
  }, [])

  const handleDrill = useCallback((_category: string) => {}, [])

  const handleAgentAction = useCallback((action: string, detail?: string) => {
    const msgs: Record<string, SystemEvent> = {
      save_info:      { level: 'OK',   message: `Memory saved${detail ? ' → ' + detail : ''}` },
      question:       { level: 'INFO', message: `Knowledge search complete` },
      show_tasks:     { level: 'INFO', message: 'Task list retrieved' },
      add_tasks:      { level: 'OK',   message: `Tasks added${detail ? ': ' + detail : ''}` },
      task_completed: { level: 'OK',   message: `Task marked complete` },
      show_categories:{ level: 'INFO', message: 'Categories loaded' },
      error:          { level: 'WARN', message: detail ?? 'Agent error' },
    }
    const ev = msgs[action]
    if (ev) addEvent(ev.level, ev.message)
  }, [addEvent])

  const handleNavClick = (id: NavPage) => {
    setPage(id)
    setSidebarOpen(false)
  }

  return (
    <div className="app">

      {/* ── Mobile sidebar overlay backdrop ── */}
      {sidebarOpen && (
        <div
          className="sidebar__backdrop"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* ── Left Sidebar ── */}
      <aside className={`sidebar${sidebarOpen ? ' sidebar--open' : ''}`}>
        <div className="sidebar__brand">
          <div className="sidebar__logo">◈</div>
          <div>
            <div className="sidebar__title">Personal Brain</div>
            <div className="sidebar__version">v1.0.4-stable</div>
          </div>
          <button
            className="sidebar__close-btn"
            onClick={() => setSidebarOpen(false)}
            aria-label="Close menu"
          >✕</button>
        </div>

        <nav className="sidebar__nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`sidebar__nav-item ${page === item.id ? 'sidebar__nav-item--active' : ''}`}
              onClick={() => handleNavClick(item.id)}
            >
              <span className="sidebar__nav-icon">{item.icon}</span>
              {item.label}
            </button>
          ))}
        </nav>

        <div className="sidebar__theme-toggle">
          <span className="theme-toggle-label">theme</span>
          <div className="theme-toggle-btn">
            <button
              className={`theme-toggle-opt${theme === 'dark' ? ' theme-toggle-opt--active' : ''}`}
              onClick={() => setTheme('dark')}
            >dark</button>
            <button
              className={`theme-toggle-opt${theme === 'light' ? ' theme-toggle-opt--active' : ''}`}
              onClick={() => setTheme('light')}
            >light</button>
          </div>
        </div>

        <div className="sidebar__user" style={{ cursor: 'pointer' }} onClick={onLogout} title="Click to sign out">
          {user.avatar_url ? (
            <img src={user.avatar_url} alt={user.name} className="sidebar__avatar-img" />
          ) : (
            <div className="sidebar__avatar">{(user.name || user.email).charAt(0).toUpperCase()}</div>
          )}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="sidebar__user-name" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {user.name || user.email}
            </div>
            <div className="sidebar__user-status">● Sign out</div>
          </div>
        </div>
      </aside>

      {/* ── Center: session bar + chat OR full-width pages ── */}
      {page === 'dashboard' ? (
        <div className="app__content app__content--full">
          <div className="app__mobile-bar">
            <button className="app__hamburger" onClick={() => setSidebarOpen(true)} aria-label="Open menu">☰</button>
          </div>
          <DashboardPage setPage={setPage} />
        </div>
      ) : page === 'tasks' ? (
        <div className="app__content app__content--full">
          <div className="app__mobile-bar">
            <button className="app__hamburger" onClick={() => setSidebarOpen(true)} aria-label="Open menu">☰</button>
          </div>
          <TasksPage />
        </div>
      ) : page === 'knowledge' ? (
        <div className="app__content app__content--full">
          <div className="app__mobile-bar">
            <button className="app__hamburger" onClick={() => setSidebarOpen(true)} aria-label="Open menu">☰</button>
          </div>
          <KnowledgePage />
        </div>
      ) : page === 'explore' ? (
        <div className="app__content app__content--full">
          <div className="app__mobile-bar">
            <button className="app__hamburger" onClick={() => setSidebarOpen(true)} aria-label="Open menu">☰</button>
          </div>
          <ExplorePage />
        </div>
      ) : page === 'settings' ? (
        <div className="app__content app__content--full app__content--scroll">
          <div className="app__mobile-bar">
            <button className="app__hamburger" onClick={() => setSidebarOpen(true)} aria-label="Open menu">☰</button>
          </div>
          <SettingsPage />
        </div>
      ) : (
        <>
          <main className="app__main">
            <div className="app__session-bar">
              <button className="app__hamburger app__hamburger--inline" onClick={() => setSidebarOpen(true)} aria-label="Open menu">☰</button>
              <span className="app__session-label">
                Session: <span className="app__session-name">brain_session.log</span>
              </span>
              <div className="app__session-icons">
                <button className="app__session-icon" title="Sync">☁</button>
                <NotificationBell token={token} />
              </div>
            </div>
            <Chat
              onDrillCategory={handleDrill}
              onAgentAction={handleAgentAction}
              initialMessage={pendingChatMessage}
              onInitialMessageConsumed={() => setPendingChatMessage('')}
            />
          </main>
          <div className="app__right-panel-wrapper">
            <RightPanel systemEvents={systemEvents} />
          </div>
        </>
      )}
    </div>
  )
}
