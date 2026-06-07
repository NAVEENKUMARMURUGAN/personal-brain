import React, { useCallback, useEffect, useState } from 'react'
import Chat from './components/Chat'
import RightPanel from './components/RightPanel'
import TasksPage from './components/TasksPage'
import KnowledgePage from './components/KnowledgePage'
import LoginPage from './components/LoginPage'
import { AuthProvider, useAuth } from './AuthContext'
import './App.css'

type Theme = 'dark' | 'light'

type NavPage = 'chat' | 'knowledge' | 'tasks' | 'settings'

interface SystemEvent {
  level: 'OK' | 'INFO' | 'WARN'
  message: string
}

function getInitialTheme(): Theme {
  try {
    const saved = localStorage.getItem('pb-theme')
    if (saved === 'light' || saved === 'dark') return saved
  } catch {}
  return 'dark'
}

const NAV_ITEMS: { id: NavPage; icon: string; label: string }[] = [
  { id: 'chat',      icon: '⬡', label: 'Chat'      },
  { id: 'knowledge', icon: '◫', label: 'Knowledge'  },
  { id: 'tasks',     icon: '◻', label: 'Tasks'      },
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

function AppMain({ onLogout, user }: { onLogout: () => void; user: { name: string; email: string; avatar_url: string } }) {
  const [theme, setTheme] = useState<Theme>(getInitialTheme)
  const [page, setPage] = useState<NavPage>('chat')
  const [systemEvents, setSystemEvents] = useState<SystemEvent[]>([
    { level: 'OK',   message: 'Index update complete' },
    { level: 'INFO', message: '24 new references synced' },
  ])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    try { localStorage.setItem('pb-theme', theme) } catch {}
  }, [theme])

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

  return (
    <div className="app">

      {/* ── Left Sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar__brand">
          <div className="sidebar__logo">◈</div>
          <div>
            <div className="sidebar__title">Personal Brain</div>
            <div className="sidebar__version">v1.0.4-stable</div>
          </div>
        </div>

        <nav className="sidebar__nav">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.id}
              className={`sidebar__nav-item ${page === item.id ? 'sidebar__nav-item--active' : ''}`}
              onClick={() => setPage(item.id)}
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
      {page === 'tasks' ? (
        <div style={{ gridColumn: '2 / 4', overflow: 'hidden' }}>
          <TasksPage />
        </div>
      ) : page === 'knowledge' ? (
        <div style={{ gridColumn: '2 / 4', overflow: 'hidden' }}>
          <KnowledgePage />
        </div>
      ) : (
        <>
          <main className="app__main">
            <div className="app__session-bar">
              <span className="app__session-label">
                Session: <span className="app__session-name">brain_session.log</span>
              </span>
              <div className="app__session-icons">
                <button className="app__session-icon" title="Sync">☁</button>
                <button className="app__session-icon" title="Notifications">🔔</button>
              </div>
            </div>
            <Chat
              onDrillCategory={handleDrill}
              onAgentAction={handleAgentAction}
            />
          </main>
          <RightPanel systemEvents={systemEvents} />
        </>
      )}
    </div>
  )
}
