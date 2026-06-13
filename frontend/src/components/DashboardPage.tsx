import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import {
  GET_DASHBOARD,
  COMPLETE_TASK,
  SAVE_TO_BRAIN,
  TRIAGE_INBOX_ITEM,
  REFRESH_BRIEFING,
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
}
interface Repo { fullName: string; description: string; language?: string | null; starsGained7d: number; whyItMatters: string }
interface LearningCardData { id: string; term: string; explanation: string; usageLine?: string | null; codeExample?: string | null; pathwayNode: string; ease: number; timesSeen: number; mastered: boolean }
interface WeeklyStats { tasksDone7d: number; articlesSaved: number; cardsMastered: number; dayStreak: number }

interface DashboardData {
  briefing?: { id?: string; text: string; generatedAt: string; cycleDate: string } | null
  weather?: { tempC: number; rainProbability: number; condition: string; hourly: HourlyWeather[] } | null
  transit: { overallSeverity: string; alerts: TransitAlert[] }
  specialToday: SpecialItem[]
  today: { due: DueTask[]; overdue: OverdueTask[]; inbox: InboxItem[] }
  news: { refreshedAt?: string | null; items: FeedItem[] }
  learningPicks: { refreshedAt?: string | null; items: FeedItem[] }
  localToday: { alerts: TransitAlert[]; advisories: { title: string; detail: string }[] }
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
  children: React.ReactNode
}

function SectionCard({ id, icon, title, meta, defaultOpen = true, children }: SectionCardProps) {
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

// ── Feed Row (news + learning) ─────────────────────────────────

interface FeedRowProps {
  item: FeedItem
  openId: string | null
  onToggle: (id: string) => void
  onDiscuss: (title: string) => void
  rank: number
}

function FeedRow({ item, openId, onToggle, onDiscuss, rank }: FeedRowProps) {
  const isOpen = openId === item.id
  const [bookmarked, setBookmarked] = useState(item.bookmarked)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(false)

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

  const mediaIcon = item.mediaType === 'video' ? '▶' : '◻'

  const tagClass = `feed-row__tag feed-row__tag--${item.tag.toLowerCase().replace(/[^a-z]/g, '-')}`

  return (
    <div className="feed-row">
      <div className="feed-row__header" onClick={() => onToggle(item.id)}>
        <span className="feed-row__rank">{String(rank).padStart(2, '0')}</span>
        <span className="feed-row__media-icon">{mediaIcon}</span>
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
          <div className="feed-row__digest">{item.summaryDetail || item.summaryShort}</div>
          <div className="feed-row__acc-actions">
            <a
              className="feed-row__acc-btn"
              href={item.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={e => e.stopPropagation()}
            >
              ↗ Read original
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

  const { data, loading } = useQuery(GET_DASHBOARD, {
    fetchPolicy: 'cache-and-network',
    notifyOnNetworkStatusChange: true,
  })

  const dashboard: DashboardData | undefined = data?.dashboard

  const [refreshBriefing, { loading: refreshingBriefing }] = useMutation(REFRESH_BRIEFING)
  const [completeTask] = useMutation(COMPLETE_TASK)
  const [triageInbox] = useMutation(TRIAGE_INBOX_ITEM)

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
            {dashboard?.weather && (
              <div
                className={`chip${weatherExpanded ? ' chip--expanded' : ''}`}
                onClick={() => setWeatherExpanded(e => !e)}
              >
                <span className="chip__icon">
                  {(dashboard.weather.rainProbability || 0) > 60 ? '🌧' : '☁'}
                </span>
                <span className="chip__label">
                  {dashboard.weather.tempC}°C · {dashboard.weather.rainProbability}% rain
                </span>
              </div>
            )}

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
              <div className="hourly-strip">
                {dashboard.weather.hourly.map((h, i) => (
                  <div key={i} className="hourly-item">
                    <div className="hourly-item__time">{h.hour}</div>
                    <div className="hourly-item__temp">{h.tempC}°</div>
                    {h.rainMm > 0 && <div className="hourly-item__rain">{h.rainMm}mm</div>}
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
          {loading && !dashboard ? (
            <SkeletonRows count={5} />
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
          ) : (
            <div className="today-empty">News pipeline hasn't run yet. Check back soon.</div>
          )}
        </SectionCard>

        {/* ── 5. Learning Picks ── */}
        <SectionCard
          id="learning"
          icon="◈"
          title="Learning Picks"
          meta="Personalized for you"
        >
          {loading && !dashboard ? (
            <SkeletonRows count={5} />
          ) : (dashboard?.learningPicks?.items || []).length > 0 ? (
            (dashboard?.learningPicks?.items || []).map((item, i) => (
              <FeedRow
                key={item.id}
                item={item}
                rank={i + 1}
                openId={openLearnId}
                onToggle={toggleLearn}
                onDiscuss={title => navigateToChat(`Tell me more about: ${title}`)}
              />
            ))
          ) : (
            <div className="today-empty">Learning pipeline hasn't run yet. Check back in 48h.</div>
          )}
        </SectionCard>

        {/* ── 6. Local Today ── */}
        <SectionCard id="local" icon="⊙" title="Local Today" defaultOpen={false}>
          {(() => {
            const alerts = dashboard?.localToday?.alerts || []
            const advisories = dashboard?.localToday?.advisories || []
            if (!loading && alerts.length === 0 && advisories.length === 0) {
              return <div className="local-empty">No local alerts or advisories.</div>
            }
            return (
              <>
                {alerts.map(alert => (
                  <div key={alert.id} className="local-row">
                    <span className="local-row__icon">
                      {alert.severity === 'major' ? '▲' : alert.severity === 'minor' ? '△' : '◻'}
                    </span>
                    <div className="local-row__body">
                      <div className="local-row__title">{alert.line}: {alert.title}</div>
                      {alert.detail && <div className="local-row__detail">{alert.detail}</div>}
                    </div>
                    <span className="local-row__tag">Transit</span>
                  </div>
                ))}
                {advisories.map((adv, i) => (
                  <div key={i} className="local-row">
                    <span className="local-row__icon">☁</span>
                    <div className="local-row__body">
                      <div className="local-row__title">{adv.title}</div>
                      <div className="local-row__detail">{adv.detail}</div>
                    </div>
                    <span className="local-row__tag">Weather</span>
                  </div>
                ))}
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
