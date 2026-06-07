import React from 'react'
import ReactMarkdown from 'react-markdown'
import TaskCard from './cards/TaskCard'
import CategoryCard from './cards/CategoryCard'
import MemoryCard from './cards/MemoryCard'
import './MessageRenderer.css'

export interface MemorySource {
  id: string
  content: string
  category: string
  score: number | null
  createdAt: string
}

export interface ChatMessage {
  id: string
  content: string
  type: string
  role: string
  payload?: string | Record<string, unknown> | null
  sources?: MemorySource[] | null
  createdAt: string
}

interface MessageRendererProps {
  message: ChatMessage
  onDrill: (category: string) => void
  onCompleteTask?: (taskId: string) => void
}

function parsePayload(raw: string | Record<string, unknown> | null | undefined): Record<string, unknown> | null {
  if (!raw) return null
  if (typeof raw === 'string') {
    try {
      return JSON.parse(raw)
    } catch {
      return null
    }
  }
  return raw as Record<string, unknown>
}

// Markdown components — open links in new tab, style inline code
const mdComponents = {
  a: ({ href, children }: React.AnchorHTMLAttributes<HTMLAnchorElement>) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="msg__link"
    >
      {children}
    </a>
  ),
  code: ({ children }: React.HTMLAttributes<HTMLElement>) => (
    <code className="msg__code">{children}</code>
  ),
  pre: ({ children }: React.HTMLAttributes<HTMLPreElement>) => (
    <pre className="msg__pre">{children}</pre>
  ),
  p: ({ children }: React.HTMLAttributes<HTMLParagraphElement>) => (
    <p className="msg__p">{children}</p>
  ),
  ul: ({ children }: React.HTMLAttributes<HTMLUListElement>) => (
    <ul className="msg__ul">{children}</ul>
  ),
  ol: ({ children }: React.OlHTMLAttributes<HTMLOListElement>) => (
    <ol className="msg__ol">{children}</ol>
  ),
  li: ({ children }: React.LiHTMLAttributes<HTMLLIElement>) => (
    <li className="msg__li">{children}</li>
  ),
  strong: ({ children }: React.HTMLAttributes<HTMLElement>) => (
    <strong className="msg__strong">{children}</strong>
  ),
}

function AssistantText({ text }: { text: string }) {
  return (
    <div className="msg__bubble msg__bubble--assistant msg__markdown">
      <ReactMarkdown components={mdComponents as Parameters<typeof ReactMarkdown>[0]['components']}>
        {text}
      </ReactMarkdown>
    </div>
  )
}

function SourcesList({ sources }: { sources: MemorySource[] }) {
  if (!sources.length) return null
  return (
    <div className="msg__sources">
      <div className="msg__sources-label">Sources</div>
      {sources.map((s) => (
        <div key={s.id} className="msg__source-item">
          <span className="msg__source-category">{s.category}</span>
          <span className="msg__source-snippet">
            {s.content.length > 100 ? s.content.slice(0, 100) + '…' : s.content}
          </span>
          {s.score != null && (
            <span className="msg__source-score">{Math.round(s.score * 100)}%</span>
          )}
        </div>
      ))}
    </div>
  )
}

const Avatar = () => (
  <div className="msg__avatar" title="Personal Brain">⚡</div>
)

export default function MessageRenderer({ message, onDrill, onCompleteTask }: MessageRendererProps) {
  const isUser = message.role === 'user'

  if (isUser) {
    return (
      <div className="msg msg--user">
        <div className="msg__bubble msg__bubble--user">{message.content}</div>
      </div>
    )
  }

  const payload = parsePayload(message.payload)
  const sources = message.sources ?? []

  if (message.type === 'task_list' && payload) {
    return (
      <div className="msg msg--assistant">
        <Avatar />
        <div className="msg__body">
          {message.content && <div className="msg__text">{message.content}</div>}
          <TaskCard
            payload={payload as Parameters<typeof TaskCard>[0]['payload']}
            onCompleteTask={onCompleteTask}
          />
        </div>
      </div>
    )
  }

  if (message.type === 'category_list' && payload) {
    return (
      <div className="msg msg--assistant">
        <Avatar />
        <div className="msg__body">
          {message.content && <div className="msg__text">{message.content}</div>}
          <CategoryCard
            payload={payload as Parameters<typeof CategoryCard>[0]['payload']}
            onDrill={onDrill}
          />
        </div>
      </div>
    )
  }

  if (message.type === 'memory_list' && payload) {
    return (
      <div className="msg msg--assistant">
        <Avatar />
        <div className="msg__body">
          {message.content && <div className="msg__text">{message.content}</div>}
          <MemoryCard payload={payload as Parameters<typeof MemoryCard>[0]['payload']} />
        </div>
      </div>
    )
  }

  // Compound: text answer + one or more cards
  if (message.type === 'compound' && payload) {
    const cards = (payload as { cards?: Array<{ type: string; payload: Record<string, unknown> }> }).cards ?? []
    return (
      <div className="msg msg--assistant">
        <Avatar />
        <div className="msg__body">
          <AssistantText text={message.content} />
          {cards.map((card, i) => {
            if (card.type === 'task_list') {
              return (
                <TaskCard
                  key={i}
                  payload={card.payload as Parameters<typeof TaskCard>[0]['payload']}
                  onCompleteTask={onCompleteTask}
                />
              )
            }
            return null
          })}
          {sources.length > 0 && <SourcesList sources={sources} />}
        </div>
      </div>
    )
  }

  // Default: markdown text with optional sources
  return (
    <div className="msg msg--assistant">
      <Avatar />
      <div className="msg__body">
        <AssistantText text={message.content} />
        {sources.length > 0 && <SourcesList sources={sources} />}
      </div>
    </div>
  )
}
