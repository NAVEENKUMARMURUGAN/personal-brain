import React, { useEffect, useState, useCallback } from 'react'
import { useAuth } from '../AuthContext'
import { API_URL } from '../config'

interface TelegramStatus {
  linked: boolean
  telegram_id?: string
  username?: string
  first_name?: string
}

export default function SettingsPage() {
  const { token, user } = useAuth()
  const [telegramStatus, setTelegramStatus] = useState<TelegramStatus | null>(null)
  const [telegramInput, setTelegramInput] = useState('')
  const [linking, setLinking] = useState(false)
  const [unlinking, setUnlinking] = useState(false)
  const [message, setMessage] = useState<{ type: 'ok' | 'error'; text: string } | null>(null)

  const loadStatus = useCallback(async () => {
    if (!token) return
    try {
      const res = await fetch(`${API_URL}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        const data = await res.json()
        setTelegramStatus(data.telegram ?? { linked: false })
      }
    } catch {
      // ignore
    }
  }, [token])

  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  const handleLink = async () => {
    const id = telegramInput.trim()
    if (!id) {
      setMessage({ type: 'error', text: 'Enter your Telegram user ID first.' })
      return
    }
    if (!/^\d+$/.test(id)) {
      setMessage({ type: 'error', text: 'Telegram user ID must be a number.' })
      return
    }
    setLinking(true)
    setMessage(null)
    try {
      const res = await fetch(`${API_URL}/telegram/link`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ telegram_id: id }),
      })
      const data = await res.json()
      if (res.ok && data.linked) {
        setMessage({ type: 'ok', text: 'Telegram account linked successfully.' })
        setTelegramInput('')
        await loadStatus()
      } else {
        setMessage({ type: 'error', text: data.error ?? 'Failed to link.' })
      }
    } catch {
      setMessage({ type: 'error', text: 'Network error. Please try again.' })
    } finally {
      setLinking(false)
    }
  }

  const handleUnlink = async () => {
    setUnlinking(true)
    setMessage(null)
    try {
      const res = await fetch(`${API_URL}/telegram/link`, {
        method: 'DELETE',
        headers: { Authorization: `Bearer ${token}` },
      })
      if (res.ok) {
        setMessage({ type: 'ok', text: 'Telegram account unlinked.' })
        await loadStatus()
      } else {
        const data = await res.json()
        setMessage({ type: 'error', text: data.error ?? 'Failed to unlink.' })
      }
    } catch {
      setMessage({ type: 'error', text: 'Network error. Please try again.' })
    } finally {
      setUnlinking(false)
    }
  }

  return (
    <div style={{ padding: '32px', maxWidth: '600px', fontFamily: 'var(--mono-font)', color: 'var(--text-primary)' }}>
      <h2 style={{ fontSize: '16px', fontWeight: 600, marginBottom: '24px', color: 'var(--text-primary)' }}>
        Settings
      </h2>

      {/* Account section */}
      <section style={{ marginBottom: '32px' }}>
        <div style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', color: 'var(--text-faint)', textTransform: 'uppercase', marginBottom: '12px' }}>
          Account
        </div>
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '8px', padding: '16px', display: 'flex', alignItems: 'center', gap: '12px' }}>
          {user?.avatar_url ? (
            <img src={user.avatar_url} alt={user.name} style={{ width: '36px', height: '36px', borderRadius: '50%' }} />
          ) : (
            <div style={{ width: '36px', height: '36px', borderRadius: '50%', background: 'var(--accent)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '14px', fontWeight: 600 }}>
              {(user?.name || user?.email || '?').charAt(0).toUpperCase()}
            </div>
          )}
          <div>
            <div style={{ fontSize: '13px', fontWeight: 500 }}>{user?.name}</div>
            <div style={{ fontSize: '11px', color: 'var(--text-faint)' }}>{user?.email}</div>
          </div>
        </div>
      </section>

      {/* Telegram section */}
      <section style={{ marginBottom: '32px' }}>
        <div style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', color: 'var(--text-faint)', textTransform: 'uppercase', marginBottom: '12px' }}>
          Telegram Integration
        </div>
        <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '8px', padding: '16px' }}>

          {telegramStatus === null ? (
            <div style={{ fontSize: '12px', color: 'var(--text-faint)' }}>Loading…</div>
          ) : telegramStatus.linked ? (
            /* ── Linked state ── */
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                <span style={{ color: '#4ade80', fontSize: '12px' }}>●</span>
                <span style={{ fontSize: '13px', fontWeight: 500 }}>Connected</span>
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-faint)', marginBottom: '16px' }}>
                Telegram ID: <span style={{ color: 'var(--text-primary)' }}>{telegramStatus.telegram_id}</span>
                {telegramStatus.first_name && (
                  <span> ({telegramStatus.first_name}{telegramStatus.username ? ` · @${telegramStatus.username}` : ''})</span>
                )}
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-faint)', marginBottom: '16px' }}>
                Messages sent to your Telegram bot will appear in your tasks and knowledge board.
              </div>
              <button
                onClick={handleUnlink}
                disabled={unlinking}
                style={{
                  padding: '6px 14px', fontSize: '12px', cursor: unlinking ? 'not-allowed' : 'pointer',
                  background: 'transparent', border: '1px solid var(--border)', borderRadius: '6px',
                  color: 'var(--text-faint)', opacity: unlinking ? 0.5 : 1,
                }}
              >
                {unlinking ? 'Unlinking…' : 'Unlink Telegram'}
              </button>
            </div>
          ) : (
            /* ── Not linked state ── */
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
                <span style={{ color: 'var(--text-faint)', fontSize: '12px' }}>○</span>
                <span style={{ fontSize: '13px', fontWeight: 500 }}>Not connected</span>
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text-faint)', marginBottom: '16px', lineHeight: '1.6' }}>
                Link your Telegram account so messages sent to your bot are saved to your brain.
                <br />
                To find your Telegram user ID: message <span style={{ color: 'var(--text-primary)' }}>@userinfobot</span> on Telegram.
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  placeholder="Your Telegram user ID (e.g. 8842935233)"
                  value={telegramInput}
                  onChange={e => setTelegramInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleLink()}
                  style={{
                    flex: 1, padding: '7px 10px', fontSize: '12px', fontFamily: 'var(--mono-font)',
                    background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: '6px',
                    color: 'var(--text-primary)', outline: 'none',
                  }}
                />
                <button
                  onClick={handleLink}
                  disabled={linking}
                  style={{
                    padding: '7px 14px', fontSize: '12px', cursor: linking ? 'not-allowed' : 'pointer',
                    background: 'var(--accent)', border: 'none', borderRadius: '6px',
                    color: '#fff', fontWeight: 600, opacity: linking ? 0.6 : 1,
                    fontFamily: 'var(--mono-font)',
                  }}
                >
                  {linking ? 'Linking…' : 'Link'}
                </button>
              </div>
            </div>
          )}

          {/* Status message */}
          {message && (
            <div style={{
              marginTop: '12px', fontSize: '12px', padding: '8px 12px', borderRadius: '6px',
              background: message.type === 'ok' ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)',
              color: message.type === 'ok' ? '#4ade80' : '#f87171',
              border: `1px solid ${message.type === 'ok' ? 'rgba(74,222,128,0.3)' : 'rgba(248,113,113,0.3)'}`,
            }}>
              {message.text}
            </div>
          )}
        </div>
      </section>

      {/* How to use Telegram section */}
      {telegramStatus?.linked && (
        <section>
          <div style={{ fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', color: 'var(--text-faint)', textTransform: 'uppercase', marginBottom: '12px' }}>
            Using Telegram
          </div>
          <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '8px', padding: '16px', fontSize: '12px', color: 'var(--text-faint)', lineHeight: '1.8' }}>
            <div>● Send any text → saved to your brain</div>
            <div>● "add task: buy groceries" → adds a task</div>
            <div>● "what tasks do I have today?" → retrieves tasks</div>
            <div>● "remember: dentist is Dr. Smith" → saves to memory</div>
            <div>● Send a voice note → transcribed and saved</div>
          </div>
        </section>
      )}
    </div>
  )
}
