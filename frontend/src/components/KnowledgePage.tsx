import React, { useState, useEffect, useRef, useCallback } from 'react'
import { useQuery } from '@apollo/client'
import { GET_MEMORIES, GET_CATEGORIES, GET_TASKS } from '../graphql/queries'
import UploadModal from './UploadModal'
import { API_URL } from '../config'
import './KnowledgePage.css'

function todayISO() { return new Date().toISOString().slice(0, 10) }

function timeAgo(isoStr: string): string {
  try {
    const diff = Date.now() - new Date(isoStr).getTime()
    const m = Math.floor(diff / 60000)
    if (m < 1)  return 'just now'
    if (m < 60) return `${m}m ago`
    const h = Math.floor(m / 60)
    if (h < 24) return `${h}h ago`
    const d = Math.floor(h / 24)
    if (d < 30) return `${d}d ago`
    return new Date(isoStr).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  } catch { return '' }
}

// Derive card type from category name
function cardType(category: string): 'NOTE' | 'LINK' | 'CREDENTIAL' | 'SYNTHESIS' | 'TAG' {
  const c = category.toLowerCase()
  if (/link|url|web|bookmark|reference/.test(c)) return 'LINK'
  if (/credential|password|key|secret|access|token/.test(c)) return 'CREDENTIAL'
  if (/synthesis|insight|summary|consolidat/.test(c)) return 'SYNTHESIS'
  return 'NOTE'
}

const TYPE_COLORS: Record<string, string> = {
  NOTE:        'var(--text-faint)',
  LINK:        'var(--accent)',
  CREDENTIAL:  'var(--accent-amber)',
  SYNTHESIS:   '#5eead4',
  TAG:         '#818cf8',
}

// Neural Node canvas — animated floating dot
function NeuralNode() {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    let x = canvas.width / 2, y = canvas.height / 2
    let vx = 0.4, vy = 0.3
    let raf: number
    const draw = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      x += vx; y += vy
      if (x < 4 || x > canvas.width - 4) vx *= -1
      if (y < 4 || y > canvas.height - 4) vy *= -1
      ctx.beginPath()
      ctx.arc(x, y, 3, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(74,222,128,0.8)'
      ctx.fill()
      raf = requestAnimationFrame(draw)
    }
    draw()
    return () => cancelAnimationFrame(raf)
  }, [])
  return <canvas ref={canvasRef} width={220} height={80} className="neural-canvas" />
}

interface Memory {
  id: string
  content: string
  category: string
  createdAt: string
}

interface KnowledgeCardProps {
  memory: Memory
  size?: 'small' | 'large'
  onDelete: (id: string) => void
  deleting: boolean
}

function KnowledgeCard({ memory, size = 'small', onDelete, deleting }: KnowledgeCardProps) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const type  = cardType(memory.category)
  const color = TYPE_COLORS[type]
  const isCredential = type === 'CREDENTIAL'
  const isSynthesis  = type === 'SYNTHESIS'

  return (
    <div className={`kcard kcard--${size} kcard--${type.toLowerCase()} ${confirmDelete ? 'kcard--confirm' : ''}`}>
      <div className="kcard__header">
        <span className="kcard__type" style={{ color, borderColor: color + '33', background: color + '18' }}>
          {type}
        </span>
        <div className="kcard__header-right">
          <span className="kcard__time">{timeAgo(memory.createdAt).toUpperCase()}</span>
          {confirmDelete ? (
            <div className="kcard__delete-confirm">
              <button
                className="kcard__delete-yes"
                onClick={() => { setConfirmDelete(false); onDelete(memory.id) }}
                disabled={deleting}
              >
                {deleting ? '…' : 'Delete'}
              </button>
              <button className="kcard__delete-cancel" onClick={() => setConfirmDelete(false)}>
                Cancel
              </button>
            </div>
          ) : (
            <button
              className="kcard__delete-btn"
              onClick={() => setConfirmDelete(true)}
              title="Delete this memory"
              aria-label="Delete"
            >
              ✕
            </button>
          )}
        </div>
      </div>

      {isCredential ? (
        <div className="kcard__credential">
          <div className="kcard__key-icon">🔑</div>
          <div>
            <div className="kcard__title">{memory.category}</div>
            <div className="kcard__snippet kcard__snippet--muted">
              Last updated {timeAgo(memory.createdAt)}
            </div>
          </div>
        </div>
      ) : (
        <>
          <div className="kcard__title">{
            memory.content.length > 60
              ? memory.content.slice(0, 60).trimEnd() + '…'
              : memory.content
          }</div>
          {!isSynthesis && memory.content.length > 60 && (
            <div className="kcard__snippet">
              {memory.content.slice(60, 160).trimEnd()}
              {memory.content.length > 160 ? '…' : ''}
            </div>
          )}
          {isSynthesis && (
            <>
              <div className="kcard__snippet">{memory.content.slice(0, 180)}…</div>
              <div className="kcard__synthesis-footer">
                <div className="kcard__avatars">
                  <span className="kcard__avatar">v1</span>
                  <span className="kcard__avatar kcard__avatar--offset">⊕</span>
                </div>
                <span className="kcard__linked">+ 4 linked files</span>
              </div>
            </>
          )}
        </>
      )}
    </div>
  )
}

export default function KnowledgePage() {
  const [search, setSearch] = useState('')
  const [filterCategory, setFilterCategory] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid')
  const [page, setPage] = useState(1)
  const [showUpload, setShowUpload] = useState(false)
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const PAGE_SIZE = 12

  const { data: memoriesData, loading } = useQuery(GET_MEMORIES, {
    variables: { category: filterCategory ?? undefined, limit: page * PAGE_SIZE },
    fetchPolicy: 'cache-and-network',
  })

  const { data: catsData } = useQuery(GET_CATEGORIES, { fetchPolicy: 'cache-and-network' })
  const { data: tasksData } = useQuery(GET_TASKS, { variables: { date: todayISO() }, fetchPolicy: 'cache-and-network' })

  const allMemories: Memory[] = memoriesData?.memories?.memories ?? []
  const categories = catsData?.categories ?? []
  const totalItems = categories.reduce((s: number, c: { count: number }) => s + c.count, 0)
  const pendingTasks = tasksData?.tasks?.pending ?? []
  const completedTasks = tasksData?.tasks?.completed ?? []

  const [deletedIds, setDeletedIds] = useState<Set<string>>(new Set())

  const handleDelete = useCallback(async (id: string) => {
    setDeletingId(id)
    try {
      const res = await fetch(`${API_URL}/memory/${id}`, { method: 'DELETE' })
      if (res.ok) {
        setDeletedIds((prev) => new Set([...prev, id]))
        setUploadSuccess('Memory removed from knowledge base.')
        setTimeout(() => setUploadSuccess(null), 3000)
      }
    } catch (e) {
      console.error('Delete failed:', e)
    } finally {
      setDeletingId(null)
    }
  }, [])

  const handleUploadSuccess = useCallback((filename: string, category: string, saved: number) => {
    setUploadSuccess(
      saved === 0
        ? `All content from "${filename}" already saved.`
        : `Saved ${saved} chunk${saved !== 1 ? 's' : ''} from "${filename}" → ${category}`
    )
    setTimeout(() => setUploadSuccess(null), 4000)
  }, [])

  const filtered = allMemories
    .filter(m => !deletedIds.has(m.id))
    .filter(m =>
      !search.trim() ||
      m.content.toLowerCase().includes(search.toLowerCase()) ||
      m.category.toLowerCase().includes(search.toLowerCase())
    )

  // Masonry: distribute into 3 columns
  const cols = viewMode === 'grid' ? [0, 1, 2] : [0]
  const columns: Memory[][] = cols.map(() => [])
  filtered.forEach((m, i) => columns[i % columns.length].push(m))

  return (
    <div className="knowledge-page">

      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onSuccess={handleUploadSuccess}
        />
      )}

      {/* ── Top bar ── */}
      <div className="knowledge-page__topbar">
        <div className="knowledge-page__search-wrap">
          <span className="knowledge-page__search-icon">⌕</span>
          <input
            className="knowledge-page__search-input"
            placeholder="Search semantic memories (Cmd + K)..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <div className="knowledge-page__search-kbd">
            <span>CMD</span>
            <span>K</span>
          </div>
        </div>
        <button className="knowledge-page__filter-btn">
          ≡ Filter Category
        </button>
        <button
          className="knowledge-page__upload-btn"
          onClick={() => setShowUpload(true)}
          title="Upload file to knowledge base (PDF, DOCX, XLSX, TXT)"
        >
          ⬆ Upload
        </button>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '8px' }}>
          <button className="knowledge-page__icon-btn">☁</button>
          <button className="knowledge-page__icon-btn">🔔</button>
        </div>
      </div>

      <div className="knowledge-page__body">

        {/* ── Main content ── */}
        <div className="knowledge-page__main">

          {/* Header */}
          <div className="knowledge-page__explorer-header">
            <div>
              <h1 className="knowledge-page__explorer-title">Knowledge Explorer</h1>
              <p className="knowledge-page__explorer-sub">
                {totalItems} item{totalItems !== 1 ? 's' : ''} indexed from {categories.length} connected node{categories.length !== 1 ? 's' : ''}
              </p>
            </div>
            <div className="knowledge-page__header-actions">
              <button
                className="knowledge-page__ingest-btn"
                onClick={() => setShowUpload(true)}
              >
                ⬆ Ingest File
              </button>
              <div className="knowledge-page__view-toggle">
                <button
                  className={`knowledge-page__view-btn ${viewMode === 'grid' ? 'knowledge-page__view-btn--active' : ''}`}
                  onClick={() => setViewMode('grid')}
                  title="Grid view"
                >⊞</button>
                <button
                  className={`knowledge-page__view-btn ${viewMode === 'list' ? 'knowledge-page__view-btn--active' : ''}`}
                  onClick={() => setViewMode('list')}
                  title="List view"
                >☰</button>
              </div>
            </div>
          </div>

          {/* Upload success toast */}
          {uploadSuccess && (
            <div className="knowledge-page__toast">
              <span className="knowledge-page__toast-icon">✓</span>
              {uploadSuccess}
            </div>
          )}

          {/* Category filter pills */}
          {categories.length > 0 && (
            <div className="knowledge-page__cats">
              <button
                className={`knowledge-page__cat-pill ${!filterCategory ? 'knowledge-page__cat-pill--active' : ''}`}
                onClick={() => setFilterCategory(null)}
              >All</button>
              {categories.map((c: { name: string }) => (
                <button
                  key={c.name}
                  className={`knowledge-page__cat-pill ${filterCategory === c.name ? 'knowledge-page__cat-pill--active' : ''}`}
                  onClick={() => setFilterCategory(filterCategory === c.name ? null : c.name)}
                >
                  {c.name}
                </button>
              ))}
            </div>
          )}

          {/* Masonry grid */}
          {loading && allMemories.length === 0 ? (
            <div className="knowledge-page__loading">Indexing memories…</div>
          ) : filtered.length === 0 ? (
            <div className="knowledge-page__empty">
              <div>No memories found{search ? ` for "${search}"` : ''}.</div>
            </div>
          ) : (
            <div className={`knowledge-page__grid knowledge-page__grid--${viewMode}`}>
              {columns.map((col, ci) => (
                <div key={ci} className="knowledge-page__col">
                  {col.map((mem, mi) => (
                    <KnowledgeCard
                      key={mem.id}
                      memory={mem}
                      size={ci === 0 && mi === 0 && viewMode === 'grid' ? 'large' : 'small'}
                      onDelete={handleDelete}
                      deleting={deletingId === mem.id}
                    />
                  ))}
                </div>
              ))}
            </div>
          )}

          {/* Load more */}
          {memoriesData?.memories?.pageInfo?.hasNextPage && (
            <button className="knowledge-page__load-more" onClick={() => setPage(p => p + 1)}>
              LOAD MORE RECORDS ∨
            </button>
          )}
        </div>

        {/* ── Right: Daily Dashboard ── */}
        <div className="knowledge-page__panel">
          <div className="knowledge-page__panel-title">DAILY DASHBOARD</div>

          {/* Knowledge Growth */}
          <div className="kdash-section">
            <div className="kdash-growth">
              <span className="kdash-growth__label">Knowledge Growth</span>
              <span className="kdash-growth__pct">+12.4%</span>
            </div>
            <div className="kdash-growth__bar">
              <div className="kdash-growth__fill" style={{ width: '74%' }} />
            </div>
          </div>

          {/* Active Tasks */}
          <div className="kdash-section">
            <div className="kdash-section__header">
              <span className="kdash-section__title">Active Tasks</span>
              {pendingTasks.length > 0 && (
                <span className="kdash-section__badge">{pendingTasks.length} NEW</span>
              )}
            </div>
            <div className="kdash-tasks">
              {pendingTasks.slice(0, 3).map((t: { id: string; content: string }) => (
                <div key={t.id} className="kdash-task kdash-task--pending">
                  <div className="kdash-task__circle" />
                  <div className="kdash-task__body">
                    <div className="kdash-task__text">{t.content.slice(0, 50)}{t.content.length > 50 ? '…' : ''}</div>
                    <div className="kdash-task__meta">General · Today</div>
                  </div>
                </div>
              ))}
              {completedTasks.slice(0, 1).map((t: { id: string; content: string }) => (
                <div key={t.id} className="kdash-task kdash-task--done">
                  <div className="kdash-task__circle kdash-task__circle--done">✓</div>
                  <div className="kdash-task__body">
                    <div className="kdash-task__text kdash-task__text--done">{t.content.slice(0, 50)}</div>
                    <div className="kdash-task__meta">Completed 2h ago</div>
                  </div>
                </div>
              ))}
              {pendingTasks.length === 0 && completedTasks.length === 0 && (
                <div className="kdash-empty">No active tasks</div>
              )}
            </div>
          </div>

          {/* Recall Capacity */}
          <div className="kdash-section">
            <div className="kdash-section__title" style={{ marginBottom: '10px' }}>Recall Capacity</div>
            <div className="kdash-recall">
              <div className="kdash-recall__tile">
                <div className="kdash-recall__stat-label">LATENCY</div>
                <div className="kdash-recall__stat-value kdash-recall__stat-value--green">14ms</div>
              </div>
              <div className="kdash-recall__tile">
                <div className="kdash-recall__stat-label">ACCURACY</div>
                <div className="kdash-recall__stat-value kdash-recall__stat-value--green">99.8%</div>
              </div>
            </div>
          </div>

          {/* Neural Node */}
          <div className="kdash-section kdash-section--neural">
            <NeuralNode />
            <div className="kdash-neural-label">NEURAL NODE: L-62</div>
          </div>
        </div>
      </div>
    </div>
  )
}
