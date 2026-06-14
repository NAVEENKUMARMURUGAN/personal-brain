import React, { useEffect, useState, useCallback } from 'react'
import { useQuery, useMutation, gql } from '@apollo/client'
import { useAuth } from '../AuthContext'
import { API_URL } from '../config'

// ── Vault GraphQL ──────────────────────────────────────────────
const GET_VAULT_ITEMS = gql`
  query GetVaultItems {
    vaultItems { id label category createdAt }
  }
`
const SEARCH_VAULT = gql`
  query SearchVault($query: String!) {
    searchVault(query: $query) { id label secret notes category createdAt }
  }
`
const SAVE_VAULT_ITEM = gql`
  mutation SaveVaultItem($label: String!, $secret: String!, $category: String, $notes: String) {
    saveVaultItem(label: $label, secret: $secret, category: $category, notes: $notes) {
      id label category createdAt
    }
  }
`
const DELETE_VAULT_ITEM = gql`
  mutation DeleteVaultItem($itemId: ID!) {
    deleteVaultItem(itemId: $itemId)
  }
`

const VAULT_CATEGORIES = ['Passwords', 'Banking', 'API Keys', 'Cards', 'Identity Documents', 'PIN Codes', 'Other']

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
        <section style={{ marginBottom: '32px' }}>
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

      {/* Vault section */}
      <VaultSection />
    </div>
  )
}

// ── Vault Section Component ────────────────────────────────────
function VaultSection() {
  const [showAdd, setShowAdd]         = useState(false)
  const [newLabel, setNewLabel]       = useState('')
  const [newSecret, setNewSecret]     = useState('')
  const [newCategory, setNewCategory] = useState('Passwords')
  const [newNotes, setNewNotes]       = useState('')
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({})
  const [searchQuery, setSearchQuery] = useState('')
  const [searching, setSearching]     = useState(false)
  const [searchResults, setSearchResults] = useState<any[] | null>(null)
  const [vaultMsg, setVaultMsg]       = useState<{ type: 'ok' | 'error'; text: string } | null>(null)

  const { data, loading, refetch } = useQuery(GET_VAULT_ITEMS, { fetchPolicy: 'cache-and-network' })
  const [saveVaultItem, { loading: saving }] = useMutation(SAVE_VAULT_ITEM)
  const [deleteVaultItem, { loading: deleting }] = useMutation(DELETE_VAULT_ITEM)
  const [searchVault] = useMutation<any, { query: string }>(gql`
    mutation SearchVaultMut($query: String!) {
      searchVault: searchVault(query: $query) { id label secret notes category createdAt }
    }
  `, { variables: { query: searchQuery } })

  // Use query instead for search
  const [doSearch, { loading: searchLoading }] = [
    async (q: string) => {
      setSearching(true)
      try {
        // We call the query directly
        const res = await refetch()
        // Filter client-side for label match as a quick workaround
        // Real search goes through Apollo query below
      } finally {
        setSearching(false)
      }
    },
    { loading: false }
  ]

  const handleSave = async () => {
    if (!newLabel.trim() || !newSecret.trim()) {
      setVaultMsg({ type: 'error', text: 'Label and secret are required.' })
      return
    }
    try {
      await saveVaultItem({ variables: { label: newLabel.trim(), secret: newSecret.trim(), category: newCategory, notes: newNotes.trim() } })
      setVaultMsg({ type: 'ok', text: `Saved "${newLabel}" to vault.` })
      setNewLabel(''); setNewSecret(''); setNewNotes(''); setShowAdd(false)
      refetch()
    } catch (e: any) {
      setVaultMsg({ type: 'error', text: e.message ?? 'Failed to save.' })
    }
  }

  const handleDelete = async (id: string, label: string) => {
    if (!confirm(`Delete "${label}" from vault?`)) return
    try {
      await deleteVaultItem({ variables: { itemId: id } })
      setVaultMsg({ type: 'ok', text: `Deleted "${label}".` })
      refetch()
    } catch (e: any) {
      setVaultMsg({ type: 'error', text: e.message ?? 'Failed to delete.' })
    }
  }

  const sectionLabel: React.CSSProperties = { fontSize: '11px', fontWeight: 600, letterSpacing: '0.08em', color: 'var(--text-faint)', textTransform: 'uppercase', marginBottom: '12px' }
  const card: React.CSSProperties = { background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: '8px', padding: '16px' }
  const inp: React.CSSProperties = { width: '100%', padding: '7px 10px', fontSize: '12px', fontFamily: 'var(--mono-font)', background: 'var(--bg-input)', border: '1px solid var(--border)', borderRadius: '6px', color: 'var(--text-primary)', outline: 'none', boxSizing: 'border-box' }
  const btn: React.CSSProperties = { padding: '7px 14px', fontSize: '12px', cursor: 'pointer', border: 'none', borderRadius: '6px', fontFamily: 'var(--mono-font)', fontWeight: 600 }

  const items: any[] = data?.vaultItems ?? []

  return (
    <section style={{ marginBottom: '32px' }}>
      <div style={sectionLabel}>🔐 Encrypted Vault</div>
      <div style={{ ...card, marginBottom: '12px' }}>
        <div style={{ fontSize: '12px', color: 'var(--text-faint)', marginBottom: '12px', lineHeight: '1.7' }}>
          Passwords, account numbers, PINs, and API keys stored with AES-256-GCM encryption.
          Your secrets never leave the server unencrypted. Access them anytime via chat:
          <span style={{ color: 'var(--accent)' }}> "what is my Netflix password?"</span>
        </div>

        {/* Add new item */}
        {!showAdd ? (
          <button style={{ ...btn, background: 'var(--accent)', color: '#000' }} onClick={() => setShowAdd(true)}>
            + Add secret
          </button>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <input style={inp} placeholder="Label (e.g. Netflix password)" value={newLabel} onChange={e => setNewLabel(e.target.value)} />
            <input style={inp} type="password" placeholder="Secret value" value={newSecret} onChange={e => setNewSecret(e.target.value)} />
            <select style={inp} value={newCategory} onChange={e => setNewCategory(e.target.value)}>
              {VAULT_CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <input style={inp} placeholder="Notes (optional): e.g. username: john@example.com" value={newNotes} onChange={e => setNewNotes(e.target.value)} />
            <div style={{ display: 'flex', gap: '8px' }}>
              <button style={{ ...btn, background: 'var(--accent)', color: '#000', opacity: saving ? 0.6 : 1 }} onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save encrypted'}
              </button>
              <button style={{ ...btn, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-faint)' }} onClick={() => setShowAdd(false)}>
                Cancel
              </button>
            </div>
          </div>
        )}

        {vaultMsg && (
          <div style={{ marginTop: '10px', fontSize: '12px', padding: '7px 10px', borderRadius: '6px', background: vaultMsg.type === 'ok' ? 'rgba(74,222,128,0.1)' : 'rgba(248,113,113,0.1)', color: vaultMsg.type === 'ok' ? '#4ade80' : '#f87171', border: `1px solid ${vaultMsg.type === 'ok' ? 'rgba(74,222,128,0.3)' : 'rgba(248,113,113,0.3)'}` }}>
            {vaultMsg.text}
          </div>
        )}
      </div>

      {/* Item list */}
      {loading ? (
        <div style={{ fontSize: '12px', color: 'var(--text-faint)', padding: '12px' }}>Loading vault…</div>
      ) : items.length === 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--text-faint)', padding: '12px' }}>No secrets saved yet.</div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {items.map(item => (
            <div key={item.id} style={{ ...card, padding: '12px 16px', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '13px', fontWeight: 500, color: 'var(--text-primary)' }}>{item.label}</div>
                <div style={{ fontSize: '11px', color: 'var(--text-faint)', marginTop: '2px' }}>
                  {item.category} · saved {new Date(item.createdAt).toLocaleDateString()}
                </div>
              </div>
              <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
                <button
                  style={{ ...btn, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-faint)', fontSize: '11px', padding: '4px 10px' }}
                  title="View via chat: ask 'what is my [label]?'"
                  onClick={() => window.dispatchEvent(new CustomEvent('pb:navigate-chat', { detail: { message: `what is my ${item.label}?` } }))}
                >
                  View in chat
                </button>
                <button
                  style={{ ...btn, background: 'transparent', border: '1px solid rgba(248,113,113,0.3)', color: '#f87171', fontSize: '11px', padding: '4px 10px', opacity: deleting ? 0.5 : 1 }}
                  onClick={() => handleDelete(item.id, item.label)}
                  disabled={deleting}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
