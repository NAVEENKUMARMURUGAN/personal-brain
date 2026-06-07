import React from 'react'

interface Memory {
  id: string
  content: string
  category: string
  createdAt: string
  score?: number | null
}

interface MemoryCardProps {
  payload: {
    category?: string
    memories: Memory[]
  }
}

function timeAgo(isoStr: string): string {
  try {
    const diff = Date.now() - new Date(isoStr).getTime()
    const h = Math.floor(diff / 3600000)
    if (h < 1) return 'just now'
    if (h < 24) return `${h}h ago`
    const d = Math.floor(h / 24)
    return `${d}d ago`
  } catch { return '' }
}

export default function MemoryCard({ payload }: MemoryCardProps) {
  const memories = payload.memories ?? []
  const category = payload.category ?? (memories[0]?.category ?? 'Memory')

  return (
    <div className="memory-card">
      <div className="memory-card__header">
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span className="memory-card__category-dot" />
          <span className="memory-card__label">{category.toUpperCase()} NOTE</span>
        </div>
        {memories[0]?.createdAt && (
          <span className="memory-card__time">{timeAgo(memories[0].createdAt)}</span>
        )}
      </div>

      {memories.map((mem) => (
        <div key={mem.id}>
          {memories.length > 1 && (
            <div className="memory-card__title">{mem.category}</div>
          )}
          <div className="memory-card__content">{mem.content}</div>
        </div>
      ))}

      {memories.length === 0 && (
        <div style={{ padding: '12px', fontSize: '12px', color: 'var(--text-faint)', fontFamily: 'var(--mono-font)' }}>
          No memories saved.
        </div>
      )}
    </div>
  )
}
