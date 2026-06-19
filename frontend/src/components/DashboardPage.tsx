import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import {
  GET_DASHBOARD,
  COMPLETE_TASK,
  SAVE_TO_BRAIN,
  TRIAGE_INBOX_ITEM,
  REFRESH_BRIEFING,
  REACT_FEED_ITEM,
  REFRESH_LEARNING_PICKS,
} from '../graphql/queries'
import { useAuth } from '../AuthContext'
import ConceptCard from './cards/ConceptCard'
import './DashboardPage.css'

// ── TypeScript interfaces ──────────────────────────────────────

interface HourlyWeather { hour: string; tempC: number; rainMm: number }
interface TransitAlert { id: string; line: string; severity: string; title: string; detail?: string | null }
interface SpecialItem { emoji: string; label: string; kind: string; note?: string | null }
interface DueTask { id: string; content: string; status: string; createdDate: string; carriedOver: boolean }
interface OverdueTask { id: string; content: string; createdDate: string; daysOverdue: number }
interface InboxItem { id: string; content: string; createdDate: string; source: string }
interface FeedItem {
  id: string; rank: number; title: string; sourceName: string; tag: string
  summaryShort: string; summaryDetail: string; sourceUrl: string
  mediaType: string; durationMin?: number | null; bookmarked: boolean
  videoId?: string | null
  reaction?: string | null   // 'like' | 'dislike' | null
}
interface Repo { fullName: string; description: string; language?: string | null; starsGained7d: number; whyItMatters: string }
interface LearningCardData { id: string; term: string; explanation: string; usageLine?: string | null; codeExample?: string | null; pathwayNode: string; ease: number; timesSeen: number; mastered: boolean }
interface WeeklyStats { tasksDone7d: number; articlesSaved: number; cardsMastered: number; dayStreak: number }

interface Advisory {
  title: string
  detail?: string | null
  icon?: string | null
  severity?: string | null
}

interface DashboardData {
  briefing?: { id?: string; text: string; generatedAt: string; cycleDate: string } | null
  weather?: {
    tempC: number; feelsLikeC?: number; rainProbability: number; condition: string
    windKmh?: number; uvIndex?: number; uvMax?: number; precipSumMm?: number
    windMaxKmh?: number; sunrise?: string; sunset?: string; hourly: HourlyWeather[]
  } | null
  transit: { overallSeverity: string; alerts: TransitAlert[] }
  specialToday: SpecialItem[]
  today: { due: DueTask[]; overdue: OverdueTask[]; inbox: InboxItem[] }
  news: { refreshedAt?: string | null; items: FeedItem[] }
  learningPicks: { refreshedAt?: string | null; items: FeedItem[] }
  localToday: { alerts: TransitAlert[]; advisories: Advisory[] }
  trendingRepos: Repo[]
  conceptOfTheDay?: LearningCardData | null
  weeklyStats: WeeklyStats
}

type NavPage = 'dashboard' | 'chat' | 'knowledge' | 'tasks' | 'settings'

interface DashboardPageProps {
  setPage: (page: NavPage) => void
}

// ── Helpers ────────────────────────────────────────────────────

function timeAgo(iso?: string | null): string {
  if (!iso) return ''
  try {
    const diff = Date.now() - new Date(iso).getTime()
    const m = Math.floor(diff / 60000)
    if (m < 1) return 'just now'
    if (m < 60) return `${m}m ago`
    return `${Math.floor(m / 60)}h ago`
  } catch { return '' }
}

/** Map weather data → a single expressive emoji */
function weatherIcon(tempC: number, rainProb: number, condition: string): string {
  const c = condition.toLowerCase()
  if (c.includes('thunder'))                         return '⛈'
  if (c.includes('snow') || c.includes('blizzard'))  return '❄️'
  if (c.includes('heavy rain') || rainProb > 75)     return '🌧'
  if (c.includes('rain') || c.includes('shower') || rainProb > 50) return '🌦'
  if (c.includes('drizzle'))                         return '🌧'
  if (c.includes('fog'))                             return '🌫'
  if (c.includes('overcast') || c.includes('cloudy')) return '☁️'
  if (c.includes('partly'))                          return '⛅'
  if (c.includes('mainly clear') || c.includes('mostly clear')) return '🌤'
  if (c.includes('clear') && tempC >= 30)            return '☀️'
  if (c.includes('clear'))                           return '🌤'
  if (tempC >= 35)                                   return '🥵'
  if (tempC >= 30)                                   return '☀️'
  if (tempC >= 22)                                   return '🌤'
  if (tempC <= 0)                                    return '🥶'
  if (tempC <= 8)                                    return '🧊'
  return '⛅'
}

/** Describe temp in plain words with colour class */
function tempFeel(tempC: number): { label: string; cls: string } {
  if (tempC >= 35) return { label: 'scorching', cls: 'weather-feel--hot' }
  if (tempC >= 30) return { label: 'hot',        cls: 'weather-feel--hot' }
  if (tempC >= 24) return { label: 'warm',       cls: 'weather-feel--warm' }
  if (tempC >= 18) return { label: 'mild',       cls: 'weather-feel--mild' }
  if (tempC >= 12) return { label: 'cool',       cls: 'weather-feel--cool' }
  if (tempC >= 5)  return { label: 'cold',       cls: 'weather-feel--cold' }
  return               { label: 'freezing',   cls: 'weather-feel--cold' }
}

/** Emoji for each hourly slot based on rain amount */
function hourlyIcon(rainMm: number, tempC: number): string {
  if (rainMm > 3)  return '🌧'
  if (rainMm > 0.5) return '🌦'
  if (tempC >= 28) return '☀️'
  return '🌤'
}

function greeting(name: string): string {
  const h = new Date().getHours()
  if (h < 12) return `Good morning, ${name}.`
  if (h < 17) return `Good afternoon, ${name}.`
  return `Good evening, ${name}.`
}

// Highlight numbers and key tokens in briefing text
function HighlightedText({ text }: { text: string }) {
  // Bold numbers and model names with accent color
  const parts = text.split(/(\b\d+\s+task[s]?\b|\bClaude\b|\bGPT[-\w]*\b|\bLlama[-\s\d]*\b|\bGemini\b|\bMistral\b)/gi)
  return (
    <>
      {parts.map((part, i) => {
        if (/^\d+\s+tasks?$/i.test(part)) {
          return <span key={i} className="briefing__highlight">{part}</span>
        }
        if (/claude|gpt|llama|gemini|mistral/i.test(part)) {
          return <span key={i} className="briefing__highlight--amber">{part}</span>
        }
        return <React.Fragment key={i}>{part}</React.Fragment>
      })}
    </>
  )
}

// ── SectionCard wrapper ────────────────────────────────────────

interface SectionCardProps {
  id: string
  icon: string
  title: string
  meta?: string
  defaultOpen?: boolean
  headerAction?: React.ReactNode
  children: React.ReactNode
}

function SectionCard({ id, icon, title, meta, defaultOpen = true, headerAction, children }: SectionCardProps) {
  const storageKey = `pb-dash-open-${id}`
  const [open, setOpen] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem(storageKey)
      return saved === null ? defaultOpen : saved === 'true'
    } catch { return defaultOpen }
  })

  const toggle = useCallback(() => {
    setOpen(o => {
      const next = !o
      try { localStorage.setItem(storageKey, String(next)) } catch {}
      return next
    })
  }, [storageKey])

  return (
    <div className="section-card">
      <div className="section-card__header" onClick={toggle}>
        <span className="section-card__icon">{icon}</span>
        <span className="section-card__title">{title}</span>
        {meta && <span className="section-card__meta">{meta}</span>}
        {headerAction && (
          <span onClick={e => e.stopPropagation()} className="section-card__header-action">
            {headerAction}
          </span>
        )}
        <span className={`section-card__chevron${open ? ' section-card__chevron--open' : ''}`}>▼</span>
      </div>
      {open && <div className="section-card__body">{children}</div>}
    </div>
  )
}

// ── Skeleton rows ──────────────────────────────────────────────

function SkeletonRows({ count = 5 }: { count?: number }) {
  return (
    <>
      {Array.from({ length: count }).map((_, i) => (
        <div key={i} className="skeleton-row">
          <div className="skeleton-block skeleton-block--short" />
          <div className="skeleton-block skeleton-block--long" />
          <div className="skeleton-block skeleton-block--tag" />
        </div>
      ))}
    </>
  )
}

// ── Pipeline Loading state (pipelines still running in background) ─────────
function PipelineLoading({ label }: { label: string }) {
  return (
    <div className="pipeline-loading">
      <span className="pipeline-loading__spinner" />
      <span className="pipeline-loading__label">{label}</span>
    </div>
  )
}

// ── Feed Row (news + learning) ─────────────────────────────────

interface FeedRowProps {
  item: FeedItem
  openId: string | null
  onToggle: (id: string) => void
  onDiscuss: (title: string) => void
  onReact?: (id: string, reaction: 'like' | 'dislike' | 'none') => void
  rank: number
}

function FeedRow({ item, openId, onToggle, onDiscuss, onReact, rank }: FeedRowProps) {
  const isOpen = openId === item.id
  const [bookmarked, setBookmarked] = useState(item.bookmarked)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(false)
  const [reaction, setReaction] = useState<string | null>(item.reaction ?? null)

  const [saveToBrain] = useMutation(SAVE_TO_BRAIN)

  const handleSave = useCallback(async (e: React.MouseEvent) => {
    e.stopPropagation()
    if (bookmarked || saving) return
    setSaving(true)
    try {
      await saveToBrain({ variables: { feedItemId: item.id } })
      setBookmarked(true)
    } catch {
      setSaveError(true)
      setTimeout(() => setSaveError(false), 2000)
    } finally {
      setSaving(false)
    }
  }, [bookmarked, saving, saveToBrain, item.id])

  const isVideo = item.mediaType === 'video' && !!item.videoId
  const mediaIcon = isVideo ? '▶' : '◻'
  const tagClass = `feed-row__tag feed-row__tag--${item.tag.toLowerCase().replace(/[^a-z]/g, '-')}`

  return (
    <div className={`feed-row${isVideo ? ' feed-row--video' : ''}`}>
      <div className="feed-row__header" onClick={() => onToggle(item.id)}>
        <span className="feed-row__rank">{String(rank).padStart(2, '0')}</span>

        {/* Thumbnail for video items, icon for articles */}
        {isVideo ? (
          <div className="feed-row__thumb-wrap">
            <img
              className="feed-row__thumb"
              src={`https://img.youtube.com/vi/${item.videoId}/mqdefault.jpg`}
              alt=""
              loading="lazy"
            />
            <span className="feed-row__thumb-play">▶</span>
          </div>
        ) : (
          <span className="feed-row__media-icon">{mediaIcon}</span>
        )}

        <div className="feed-row__title-block">
          <div className="feed-row__title" title={item.title}>{item.title}</div>
          <div className="feed-row__source">
            {item.sourceName}
            {item.durationMin ? (
              <span className="duration-badge" style={{ marginLeft: 8 }}>{item.durationMin} min</span>
            ) : null}
          </div>
        </div>
        <div className="feed-row__meta">
          <span className={tagClass}>{item.tag}</span>
          {onReact && (
            <>
              <button
                className={`feed-row__react-btn${reaction === 'like' ? ' feed-row__react-btn--liked' : ''}`}
                onClick={e => {
                  e.stopPropagation()
                  const next = reaction === 'like' ? 'none' : 'like'
                  setReaction(next === 'none' ? null : next)
                  onReact(item.id, next as 'like' | 'dislike' | 'none')
                }}
                title={reaction === 'like' ? 'Unlike' : 'Like this'}
              >👍</button>
              <button
                className={`feed-row__react-btn${reaction === 'dislike' ? ' feed-row__react-btn--disliked' : ''}`}
                onClick={e => {
                  e.stopPropagation()
                  const next = reaction === 'dislike' ? 'none' : 'dislike'
                  setReaction(next === 'none' ? null : next)
                  onReact(item.id, next as 'like' | 'dislike' | 'none')
                }}
                title={reaction === 'dislike' ? 'Remove dislike' : 'Not interested'}
              >👎</button>
            </>
          )}
          <button
            className={`feed-row__bookmark${bookmarked ? ' feed-row__bookmark--saved' : ''}`}
            onClick={handleSave}
            title={bookmarked ? 'Saved to brain' : 'Save to brain'}
          >
            {saving ? '…' : saveError ? '✗' : bookmarked ? '★' : '☆'}
          </button>
        </div>
      </div>

      {isOpen && (
        <div className="feed-row__accordion">
          {/* Inline YouTube embed */}
          {isVideo && (
            <div className="video-embed">
              <iframe
                src={`https://www.youtube.com/embed/${item.videoId}?rel=0&modestbranding=1`}
                title={item.title}
                allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                allowFullScreen
              />
            </div>
          )}

          <div className="feed-row__digest">{item.summaryDetail || item.summaryShort}</div>
          <div className="feed-row__acc-actions">
            <a
              className={`feed-row__acc-btn${isVideo ? ' feed-row__acc-btn--watch' : ''}`}
              href={item.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={e => e.stopPropagation()}
            >
              {isVideo ? '▶ Watch on YouTube' : '↗ Read original'}
            </a>
            <button
              className={`feed-row__acc-btn${bookmarked ? ' feed-row__acc-btn--saved' : ''}`}
              onClick={handleSave}
            >
              {bookmarked ? '★ Saved to brain' : '☆ Save to brain'}
            </button>
            <button
              className="feed-row__acc-btn"
              onClick={() => onDiscuss(item.title)}
            >
              ◈ Discuss in chat
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

// ── Language colour map (GitHub-ish) ──────────────────────────

const LANG_COLORS: Record<string, string> = {
  Python: '#3572A5', TypeScript: '#2b7489', JavaScript: '#f1e05a',
  Rust: '#dea584', Go: '#00ADD8', C: '#555555', 'C++': '#f34b7d',
  Java: '#b07219', Kotlin: '#F18E33', Swift: '#ffac45',
  Julia: '#a270ba', R: '#198CE7', Scala: '#c22d40',
}

// ── Main DashboardPage ─────────────────────────────────────────

export default function DashboardPage({ setPage }: DashboardPageProps) {
  const { user } = useAuth()

  // Poll every 6 s while any pipeline section is still empty (pipelines run in
  // the background after login and may take 10-30 s to finish).
  // Once all sections have data, polling stops automatically.
  const [pollInterval, setPollInterval] = useState(6000)

  const { data, loading, refetch } = useQuery(GET_DASHBOARD, {
    fetchPolicy: 'cache-and-network',
    notifyOnNetworkStatusChange: true,
    pollInterval,
  })

  const dashboard: DashboardData | undefined = data?.dashboard

  // Stop polling once all pipeline-backed sections are populated
  useEffect(() => {
    if (!dashboard) return
    const stillLoading =
      !dashboard.news?.items?.length ||
      !dashboard.learningPicks?.items?.length ||
      !dashboard.trendingRepos?.length
    setPollInterval(stillLoading ? 6000 : 0)
  }, [dashboard])

  const [refreshBriefing, { loading: refreshingBriefing }] = useMutation(REFRESH_BRIEFING)
  const [reactFeedItem]   = useMutation(REACT_FEED_ITEM)
  const [refreshLearning, { loading: refreshingLearning }] = useMutation(REFRESH_LEARNING_PICKS)
  const [completeTask] = useMutation(COMPLETE_TASK)
  const [triageInbox] = useMutation(TRIAGE_INBOX_ITEM)

  // Local learning picks state (overrides query data after manual refresh)
  const [localLearningItems, setLocalLearningItems] = useState<FeedItem[] | null>(null)
  const [localLearningRefreshedAt, setLocalLearningRefreshedAt] = useState<string | null>(null)

  // Local task states for inline completion feedback
  const [completedTaskIds, setCompletedTaskIds] = useState<Set<string>>(new Set())
  const [removedInboxIds, setRemovedInboxIds] = useState<Set<string>>(new Set())

  // Briefing text — may be updated by refresh mutation
  const [briefingText, setBriefingText] = useState<string | null>(null)
  const [briefingTime, setBriefingTime] = useState<string | null>(null)

  // Context strip expand states
  const [weatherExpanded, setWeatherExpanded] = useState(false)
  const [transitExpanded, setTransitExpanded] = useState(false)
  const [expandedSpecial, setExpandedSpecial] = useState<number | null>(null)

  // Feed accordion states (one open at a time per section)
  const [openNewsId, setOpenNewsId] = useState<string | null>(null)
  const [openLearnId, setOpenLearnId] = useState<string | null>(null)

  // Navigate to chat with pre-filled message via custom event
  const navigateToChat = useCallback((message: string) => {
    window.dispatchEvent(new CustomEvent('pb:navigate-chat', { detail: { message } }))
    setPage('chat')
  }, [setPage])

  // Sync briefing text from query data
  useEffect(() => {
    if (dashboard?.briefing?.text && !briefingText) {
      setBriefingText(dashboard.briefing.text)
      setBriefingTime(dashboard.briefing.generatedAt)
    }
  }, [dashboard?.briefing, briefingText])

  const handleRefreshBriefing = useCallback(async () => {
    try {
      const result = await refreshBriefing()
      const b = result.data?.refreshBriefing
      if (b) {
        setBriefingText(b.text)
        setBriefingTime(b.generatedAt)
      }
    } catch { /* silent */ }
  }, [refreshBriefing])

  const handleCompleteTask = useCallback(async (taskId: string) => {
    setCompletedTaskIds(prev => new Set([...prev, taskId]))
    try {
      await completeTask({ variables: { taskId } })
    } catch {
      setCompletedTaskIds(prev => { const n = new Set(prev); n.delete(taskId); return n })
    }
  }, [completeTask])

  const handleTriage = useCallback(async (itemId: string, action: 'today' | 'later' | 'archive') => {
    setRemovedInboxIds(prev => new Set([...prev, itemId]))
    try {
      await triageInbox({ variables: { itemId, action } })
    } catch {
      setRemovedInboxIds(prev => { const n = new Set(prev); n.delete(itemId); return n })
    }
  }, [triageInbox])

  const handleRefreshLearning = useCallback(async () => {
    try {
      const result = await refreshLearning()
      const section = result.data?.refreshLearningPicks
      if (section) {
        setLocalLearningItems(section.items)
        setLocalLearningRefreshedAt(section.refreshedAt)
      }
    } catch { /* silent */ }
  }, [refreshLearning])

  const handleReact = useCallback(async (feedItemId: string, reaction: 'like' | 'dislike' | 'none') => {
    try {
      await reactFeedItem({ variables: { feedItemId, reaction } })
    } catch { /* silent — UI already updated optimistically */ }
  }, [reactFeedItem])

  const toggleNews = useCallback((id: string) => {
    setOpenNewsId(prev => prev === id ? null : id)
  }, [])

  const toggleLearn = useCallback((id: string) => {
    setOpenLearnId(prev => prev === id ? null : id)
  }, [])

  const name = user?.name?.split(' ')[0] || 'there'

  // ── Render ────────────────────────────────────────────────────

  return (
    <div className="dashboard">
      <div className="dashboard__container">

        {/* ── 1. Briefing Hero ── */}
        <div className="briefing">
          <div className="briefing__greeting">{greeting(name)}</div>

          {loading && !briefingText ? (
            <div className="briefing__text briefing__text--skeleton" />
          ) : (
            <div className="briefing__text">
              {briefingText ? <HighlightedText text={briefingText} /> : (
                <span style={{ color: 'var(--text-faint)' }}>
                  Generating your briefing…
                </span>
              )}
            </div>
          )}

          <div className="briefing__actions">
            <button
              className="briefing__plan-btn"
              onClick={() => navigateToChat('Plan my day using my tasks, calendar, the weather and transport status.')}
            >
              ⟳ Plan my day
            </button>
            <span className="briefing__timestamp">
              {briefingTime ? `Generated ${timeAgo(briefingTime)}` : ''}
            </span>
            <button
              className={`briefing__refresh-btn${refreshingBriefing ? ' briefing__refresh-btn--spinning' : ''}`}
              onClick={handleRefreshBriefing}
              title="Refresh briefing"
            >
              ↺
            </button>
          </div>
        </div>

        {/* ── 2. Context Strip ── */}
        <div>
          <div className="context-strip">
            {/* Weather chip */}
            {dashboard?.weather && (() => {
              const { tempC, rainProbability, condition } = dashboard.weather
              const icon = weatherIcon(tempC, rainProbability, condition)
              const feel = tempFeel(tempC)
              return (
                <div
                  className={`chip chip--weather${weatherExpanded ? ' chip--expanded' : ''}`}
                  onClick={() => setWeatherExpanded(e => !e)}
                  title={condition}
                >
                  <span className="chip__icon chip__icon--weather">{icon}</span>
                  <span className="chip__label">
                    <span className={`weather-temp ${feel.cls}`}>{tempC}°C</span>
                    <span className="weather-sep">·</span>
                    {rainProbability > 0
                      ? <span className="weather-rain">{rainProbability > 60 ? '🌧' : '💧'} {rainProbability}%</span>
                      : <span className="weather-clear">☀️ dry</span>
                    }
                    <span className={`weather-feel ${feel.cls}`}>{feel.label}</span>
                  </span>
                </div>
              )
            })()}

            {/* Transit chip */}
            {dashboard?.transit && (
              <div
                className={`chip${transitExpanded ? ' chip--expanded' : ''}`}
                onClick={() => setTransitExpanded(e => !e)}
              >
                <span
                  className={`chip__dot chip__dot--${
                    dashboard.transit.overallSeverity === 'major' ? 'red' :
                    dashboard.transit.overallSeverity === 'minor' ? 'amber' : 'green'
                  }`}
                />
                <span className="chip__label">
                  {dashboard.transit.overallSeverity === 'normal'
                    ? 'Normal Service'
                    : dashboard.transit.alerts[0]?.title || 'Service alert'}
                </span>
              </div>
            )}

            {/* Special Today strip */}
            <div className="special-strip">
              {(dashboard?.specialToday || []).map((item, idx) => (
                <div key={idx}>
                  <div
                    className={`special-item${item.kind === 'personal' ? ' special-item--personal' : ''}`}
                    onClick={() => setExpandedSpecial(expandedSpecial === idx ? null : idx)}
                  >
                    <span className="special-item__emoji">{item.emoji}</span>
                    <span className="special-item__label">{item.label}</span>
                  </div>
                  {expandedSpecial === idx && item.note && (
                    <div className="special-item__note">{item.note}</div>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Inline weather expand */}
          {weatherExpanded && dashboard?.weather?.hourly && (
            <div className="chip-expand" style={{ marginTop: 8 }}>
              <div className="chip-expand__label">{dashboard.weather.condition}</div>
              <div className="hourly-strip">
                {dashboard.weather.hourly.map((h, i) => (
                  <div key={i} className="hourly-item">
                    <div className="hourly-item__time">{h.hour}</div>
                    <div className="hourly-item__icon">{hourlyIcon(h.rainMm, h.tempC)}</div>
                    <div className={`hourly-item__temp ${tempFeel(h.tempC).cls}`}>{h.tempC}°</div>
                    {h.rainMm > 0.1 && <div className="hourly-item__rain">{h.rainMm}mm</div>}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Inline transit expand */}
          {transitExpanded && dashboard?.transit?.alerts && (
            <div className="chip-expand" style={{ marginTop: 8 }}>
              {dashboard.transit.alerts.map(alert => (
                <div key={alert.id} style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 4 }}>
                    {alert.line}: {alert.title}
                  </div>
                  {alert.detail && (
                    <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>{alert.detail}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── 3 & 9. Today + Concept (side-by-side on wide screens) ── */}
        <div className="dashboard__today-concept-row">
          {/* Today Card */}
          <SectionCard id="today" icon="◻" title="Today">
            {loading && !dashboard ? (
              <SkeletonRows count={3} />
            ) : (
              <>
                {/* Due tasks */}
                {(dashboard?.today.due || []).map(task => (
                  <div key={task.id} className="task-row">
                    <input
                      type="checkbox"
                      className="task-row__check"
                      checked={completedTaskIds.has(task.id)}
                      onChange={() => handleCompleteTask(task.id)}
                      disabled={completedTaskIds.has(task.id)}
                    />
                    <div className={`task-row__content${completedTaskIds.has(task.id) ? ' task-row__content--done' : ''}`}>
                      {task.content}
                    </div>
                    {completedTaskIds.has(task.id) && (
                      <span className="task-row__confirm">✓ done</span>
                    )}
                  </div>
                ))}

                {/* Overdue tasks */}
                {(dashboard?.today.overdue || []).map(task => (
                  <div key={task.id} className="task-row">
                    <input
                      type="checkbox"
                      className="task-row__check"
                      checked={completedTaskIds.has(task.id)}
                      onChange={() => handleCompleteTask(task.id)}
                      disabled={completedTaskIds.has(task.id)}
                    />
                    <div className={`task-row__content${completedTaskIds.has(task.id) ? ' task-row__content--done' : ''}`}>
                      {task.content}
                    </div>
                    <span className="task-row__badge task-row__badge--overdue">
                      overdue {task.daysOverdue}d
                    </span>
                  </div>
                ))}

                {/* Inbox */}
                {(dashboard?.today.inbox || []).filter(i => !removedInboxIds.has(i.id)).length > 0 && (
                  <>
                    <div className="today-subsection">Inbox</div>
                    {(dashboard?.today.inbox || [])
                      .filter(i => !removedInboxIds.has(i.id))
                      .map(item => (
                        <div key={item.id} className="task-row">
                          <div className="task-row__content">{item.content}</div>
                          <div className="inbox-actions">
                            <button className="inbox-btn inbox-btn--today" onClick={() => handleTriage(item.id, 'today')}>Today</button>
                            <button className="inbox-btn" onClick={() => handleTriage(item.id, 'later')}>Later</button>
                            <button className="inbox-btn inbox-btn--archive" onClick={() => handleTriage(item.id, 'archive')}>Archive</button>
                          </div>
                        </div>
                      ))}
                  </>
                )}

                {/* Empty state */}
                {!loading && !dashboard?.today.due.length &&
                  !dashboard?.today.overdue.length &&
                  !dashboard?.today.inbox.length && (
                  <div className="today-empty">Clear runway — nothing due.</div>
                )}
              </>
            )}
          </SectionCard>

          {/* Concept of the Day */}
          {dashboard?.conceptOfTheDay && (
            <SectionCard id="concept" icon="◈" title="Concept of the Day">
              <div style={{ padding: 16 }}>
                <ConceptCard card={dashboard.conceptOfTheDay} />
              </div>
            </SectionCard>
          )}
        </div>

        {/* ── 4. Top AI News ── */}
        <SectionCard
          id="news"
          icon="◫"
          title="Top AI News"
          meta={dashboard?.news?.refreshedAt ? `Refreshed ${timeAgo(dashboard.news.refreshedAt)}` : undefined}
        >
          {(loading && !dashboard) || (!loading && !(dashboard?.news?.items || []).length) ? (
            <PipelineLoading label="Fetching today's AI news…" />
          ) : (dashboard?.news?.items || []).length > 0 ? (
            <>
              {(dashboard?.news?.items || []).map((item, i) => (
                <FeedRow
                  key={item.id}
                  item={item}
                  rank={i + 1}
                  openId={openNewsId}
                  onToggle={toggleNews}
                  onDiscuss={title => navigateToChat(`Tell me more about: ${title}`)}
                />
              ))}
              <div
                style={{ padding: '12px 18px', textAlign: 'center', fontSize: 12, color: 'var(--text-faint)', cursor: 'pointer', fontFamily: 'var(--mono-font)' }}
                onClick={() => navigateToChat("Let's discuss today's top AI news.")}
              >
                Discuss today's news in chat →
              </div>
            </>
          ) : null}
        </SectionCard>

        {/* ── 5. Learning Picks ── */}
        {(() => {
          const learnItems = localLearningItems ?? dashboard?.learningPicks?.items ?? []
          const learnRefreshedAt = localLearningRefreshedAt ?? dashboard?.learningPicks?.refreshedAt
          return (
            <SectionCard
              id="learning"
              icon="◈"
              title="Learning Picks"
              meta={learnRefreshedAt ? `Updated ${timeAgo(learnRefreshedAt)}` : 'Personalized for you'}
              headerAction={
                <button
                  className="section-card__refresh-btn"
                  onClick={handleRefreshLearning}
                  disabled={refreshingLearning}
                  title="Refresh learning picks"
                >
                  {refreshingLearning ? '…' : '↺'}
                </button>
              }
            >
              {(loading && !dashboard) || (!loading && !learnItems.length) ? (
                <PipelineLoading label="Curating your learning picks…" />
              ) : learnItems.length > 0 ? (
                learnItems.map((item, i) => (
                  <FeedRow
                    key={item.id}
                    item={item}
                    rank={i + 1}
                    openId={openLearnId}
                    onToggle={toggleLearn}
                    onDiscuss={title => navigateToChat(`Tell me more about: ${title}`)}
                    onReact={handleReact}
                  />
                ))
              ) : null}
            </SectionCard>
          )
        })()}

        {/* ── 6. Local Today ── */}
        <SectionCard id="local" icon="⊙" title="Local Today" defaultOpen>
          {(() => {
            const alerts    = dashboard?.localToday?.alerts || []
            const advisories = dashboard?.localToday?.advisories || []
            const disrupted  = alerts.filter(a => a.severity !== 'normal')
            const normalLines = alerts.filter(a => a.severity === 'normal')

            const severityIcon = (s: string) =>
              s === 'major' ? '🔴' : s === 'minor' ? '🟡' : s === 'personal' ? '⭐' : 'ℹ️'

            const tagClass = (s?: string | null) =>
              s === 'major' ? 'local-row__tag--major'
              : s === 'minor' ? 'local-row__tag--minor'
              : s === 'personal' ? 'local-row__tag--personal'
              : 'local-row__tag--info'

            if (!loading && alerts.length === 0 && advisories.length === 0) {
              return <div className="local-empty">All clear — no alerts today.</div>
            }

            return (
              <>
                {/* Weather advisories first */}
                {advisories.map((adv, i) => (
                  <div key={`adv-${i}`} className={`local-row${adv.severity === 'major' ? ' local-row--major' : adv.severity === 'personal' ? ' local-row--personal' : ''}`}>
                    <span className="local-row__icon">{adv.icon || severityIcon(adv.severity || 'info')}</span>
                    <div className="local-row__body">
                      <div className="local-row__title">{adv.title}</div>
                      {adv.detail && <div className="local-row__detail">{adv.detail}</div>}
                    </div>
                    <span className={`local-row__tag ${tagClass(adv.severity)}`}>
                      {adv.severity === 'personal' ? 'Personal' : adv.severity === 'info' ? 'Info' : adv.severity === 'major' ? 'Alert' : 'Notice'}
                    </span>
                  </div>
                ))}

                {/* Disrupted transit lines */}
                {disrupted.map(alert => (
                  <div key={alert.id} className={`local-row${alert.severity === 'major' ? ' local-row--major' : ' local-row--minor'}`}>
                    <span className="local-row__icon">🚆</span>
                    <div className="local-row__body">
                      <div className="local-row__title"><strong>{alert.line}</strong> · {alert.title}</div>
                      {alert.detail && <div className="local-row__detail">{alert.detail}</div>}
                    </div>
                    <span className={`local-row__tag ${tagClass(alert.severity)}`}>Transit</span>
                  </div>
                ))}

                {/* Normal transit lines — collapsed into one row */}
                {normalLines.length > 0 && (
                  <div className="local-row local-row--normal">
                    <span className="local-row__icon">🟢</span>
                    <div className="local-row__body">
                      <div className="local-row__title">
                        Normal service · {normalLines.map(a => a.line).join(', ')}
                      </div>
                    </div>
                    <span className="local-row__tag local-row__tag--info">Transit</span>
                  </div>
                )}
              </>
            )
          })()}
        </SectionCard>

        {/* ── 7. Trending Repos ── */}
        <SectionCard id="repos" icon="↑" title="Trending Repos" defaultOpen={false}>
          <div className="repos-grid">
            {loading && !dashboard ? (
              Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="repo-card" style={{ minHeight: 100 }}>
                  <div className="skeleton-block" style={{ height: 14, width: '80%', marginBottom: 8 }} />
                  <div className="skeleton-block" style={{ height: 10, width: '50%' }} />
                </div>
              ))
            ) : (dashboard?.trendingRepos || []).length > 0 ? (
              (dashboard?.trendingRepos || []).map(repo => (
                <a
                  key={repo.fullName}
                  className="repo-card"
                  href={`https://github.com/${repo.fullName}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <div className="repo-card__header">
                    <div className="repo-card__name">{repo.fullName}</div>
                    {repo.starsGained7d > 0 && (
                      <div className="repo-card__velocity">
                        +{repo.starsGained7d >= 1000
                          ? `${(repo.starsGained7d / 1000).toFixed(1)}k`
                          : repo.starsGained7d}
                      </div>
                    )}
                  </div>
                  {repo.language && (
                    <div className="repo-card__lang">
                      <div
                        className="repo-card__lang-dot"
                        style={{ background: LANG_COLORS[repo.language] || 'var(--text-faint)' }}
                      />
                      <span className="repo-card__lang-name">{repo.language}</span>
                    </div>
                  )}
                  <div className="repo-card__desc">{repo.whyItMatters || repo.description}</div>
                </a>
              ))
            ) : (
              <div style={{ gridColumn: '1 / -1' }} className="local-empty">
                Repos pipeline hasn't run yet.
              </div>
            )}
          </div>
          <div
            style={{ padding: '8px 16px 12px', textAlign: 'right', fontSize: 11, color: 'var(--text-faint)', fontFamily: 'var(--mono-font)' }}
          >
            <a href="https://github.com/trending" target="_blank" rel="noopener noreferrer" style={{ color: 'inherit', textDecoration: 'none' }}>
              View GitHub Trending ↗
            </a>
          </div>
        </SectionCard>

        {/* ── 8. Footer Stats Strip ── */}
        {dashboard?.weeklyStats && (
          <div className="stats-strip">
            <div className="stats-item">
              <div className="stats-item__value">{dashboard.weeklyStats.tasksDone7d}</div>
              <div className="stats-item__label">Tasks Done (7d)</div>
            </div>
            <div className="stats-item">
              <div className="stats-item__value">{dashboard.weeklyStats.articlesSaved}</div>
              <div className="stats-item__label">Articles Saved</div>
            </div>
            <div className="stats-item">
              <div className="stats-item__value">{dashboard.weeklyStats.cardsMastered}</div>
              <div className="stats-item__label">Cards Mastered</div>
            </div>
            <div className="stats-item">
              <div className={`stats-item__value${dashboard.weeklyStats.dayStreak > 0 ? ' stats-item__value--streak' : ''}`}>
                {dashboard.weeklyStats.dayStreak}
              </div>
              <div className="stats-item__label">Day Streak</div>
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
