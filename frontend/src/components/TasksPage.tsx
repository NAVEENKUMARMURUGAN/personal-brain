import React, { useState, useEffect, useCallback, useRef } from 'react'
import { useQuery, useMutation } from '@apollo/client'
import { GET_TASKS, COMPLETE_TASK, ADD_TASK, EDIT_TASK, DELETE_TASK } from '../graphql/queries'
import { API_URL } from '../config'
import './TasksPage.css'

// ── Date helpers ──────────────────────────────────────────────
function todayISO()     { return new Date().toISOString().slice(0, 10) }
function yesterdayISO() { const d = new Date(); d.setDate(d.getDate() - 1); return d.toISOString().slice(0, 10) }
function nDaysAgoISO(n: number) { const d = new Date(); d.setDate(d.getDate() - n); return d.toISOString().slice(0, 10) }

function formatGroupDate(iso: string): string {
  const today = todayISO(), yesterday = yesterdayISO()
  if (iso === today)     return 'Today'
  if (iso === yesterday) return 'Yesterday'
  try {
    return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })
  } catch { return iso }
}

// ── Countdown ─────────────────────────────────────────────────
function useMidnightCountdown() {
  const [label, setLabel] = useState('')
  useEffect(() => {
    const tick = () => {
      const now = new Date(), midnight = new Date(now)
      midnight.setHours(24, 0, 0, 0)
      const diff = Math.max(0, Math.floor((midnight.getTime() - now.getTime()) / 1000))
      const h = String(Math.floor(diff / 3600)).padStart(2, '0')
      const m = String(Math.floor((diff % 3600) / 60)).padStart(2, '0')
      const s = String(diff % 60).padStart(2, '0')
      setLabel(`${h}:${m}:${s}`)
    }
    tick(); const id = setInterval(tick, 1000); return () => clearInterval(id)
  }, [])
  return label
}

// ── Donut ─────────────────────────────────────────────────────
function DonutRing({ pct, size = 100 }: { pct: number; size?: number }) {
  const r = (size - 12) / 2, circ = 2 * Math.PI * r, dash = (pct / 100) * circ
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="donut-ring">
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="var(--border-base)" strokeWidth="8" />
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="var(--accent)" strokeWidth="8"
        strokeDasharray={`${dash} ${circ}`} strokeLinecap="round"
        transform={`rotate(-90 ${size/2} ${size/2})`}
        style={{ transition: 'stroke-dasharray 0.6s ease' }} />
      <text x="50%" y="50%" textAnchor="middle" dominantBaseline="central"
        fill="var(--text-primary)" fontSize="16" fontWeight="700" fontFamily="var(--mono-font)">{pct}%</text>
      <text x="50%" y="62%" textAnchor="middle" dominantBaseline="central"
        fill="var(--text-faint)" fontSize="7" fontFamily="var(--mono-font)">Quota</text>
    </svg>
  )
}

// ── Velocity chart ────────────────────────────────────────────
function VelocityChart({ data }: { data: number[] }) {
  const max = Math.max(...data, 1)
  const days = ['MON', 'TUE', 'WED', 'THU', 'FRI']
  return (
    <div className="velocity-chart">
      {data.map((v, i) => (
        <div key={i} className="velocity-chart__col">
          <div className={`velocity-chart__bar ${i === 2 ? 'velocity-chart__bar--peak' : i === 4 ? 'velocity-chart__bar--accent' : ''}`}
            style={{ height: `${Math.max(6, (v / max) * 56)}px` }} />
          <div className="velocity-chart__label">{days[i]}</div>
        </div>
      ))}
    </div>
  )
}

// ── Types ─────────────────────────────────────────────────────
interface Task {
  id: string; content: string; status: string
  createdDate?: string; completedDate?: string | null; carriedOver?: boolean
}

interface DateGroup { date: string; pending: Task[]; completed: Task[] }

function inferCategory(c: string) {
  const l = c.toLowerCase()
  if (/terraform|deploy|infra|aws|docker/.test(l)) return 'Infrastructure'
  if (/test|bug|fix|refactor|pr|review/.test(l))   return 'Engineering'
  if (/meeting|sync|standup|call/.test(l))          return 'Meetings'
  if (/write|doc|readme|design/.test(l))            return 'Documentation'
  return 'General'
}

// ── Inline editable task row ──────────────────────────────────
interface TaskRowProps {
  task: Task
  onComplete: (id: string) => void
  onEdit: (id: string, content: string) => void
  onDelete: (id: string) => void
  completing: string | null
  deleting: string | null
}

function TaskRow({ task, onComplete, onEdit, onDelete, completing, deleting }: TaskRowProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft]     = useState(task.content)
  const inputRef              = useRef<HTMLInputElement>(null)
  const isDone = task.status === 'complete'

  useEffect(() => { if (editing) inputRef.current?.focus() }, [editing])

  const commitEdit = () => {
    const trimmed = draft.trim()
    if (trimmed && trimmed !== task.content) onEdit(task.id, trimmed)
    setEditing(false)
  }

  return (
    <div className={`task-row ${isDone ? 'task-row--done' : ''}`}>
      <span className="task-row__drag">⠿</span>

      {isDone ? (
        <div className="task-row__checkbox task-row__checkbox--done">✓</div>
      ) : (
        <button
          className={`task-row__checkbox ${completing === task.id ? 'task-row__checkbox--spinning' : ''}`}
          onClick={() => onComplete(task.id)}
          disabled={!!completing || !!deleting}
          title="Mark complete"
        />
      )}

      <div className="task-row__body">
        {editing ? (
          <input
            ref={inputRef}
            className="task-row__edit-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onBlur={commitEdit}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitEdit()
              if (e.key === 'Escape') { setDraft(task.content); setEditing(false) }
            }}
          />
        ) : (
          <div
            className={`task-row__content ${isDone ? 'task-row__content--done' : ''}`}
            onDoubleClick={() => !isDone && setEditing(true)}
            title={isDone ? '' : 'Double-click to edit'}
          >
            {task.content}
          </div>
        )}
        <div className="task-row__meta">
          {isDone ? (
            <>
              <span className="task-row__completed-label">Completed</span>
              {task.completedDate && (
                <>
                  <span className="task-row__dot">·</span>
                  <span className="task-row__time">
                    {new Date(task.completedDate).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                  </span>
                </>
              )}
            </>
          ) : (
            <>
              <span className="task-row__category">{inferCategory(task.content)}</span>
              {task.carriedOver && <><span className="task-row__dot">·</span><span className="task-row__carried">carried</span></>}
            </>
          )}
        </div>
      </div>

      {/* Row actions — visible on hover */}
      {!isDone && (
        <div className="task-row__actions">
          <button className="task-row__action-btn" onClick={() => setEditing(true)} title="Edit">✎</button>
          <button
            className="task-row__action-btn task-row__action-btn--delete"
            onClick={() => onDelete(task.id)}
            disabled={!!deleting}
            title="Delete"
          >
            {deleting === task.id ? '…' : '✕'}
          </button>
        </div>
      )}
    </div>
  )
}

// ── Inline add-task input ────────────────────────────────────
function AddTaskRow({ date, onAdd }: { date: string; onAdd: (content: string, date: string) => void }) {
  const [open,  setOpen]  = useState(false)
  const [value, setValue] = useState('')
  const inputRef          = useRef<HTMLInputElement>(null)
  const committedRef      = useRef(false)

  useEffect(() => { if (open) { committedRef.current = false; inputRef.current?.focus() } }, [open])

  const commit = () => {
    if (committedRef.current) return
    committedRef.current = true
    const t = value.trim()
    if (t) { onAdd(t, date); setValue('') }
    setOpen(false)
  }

  if (!open) {
    return (
      <button className="add-task-row" onClick={() => setOpen(true)}>
        + Add task
      </button>
    )
  }

  return (
    <div className="add-task-row add-task-row--open">
      <input
        ref={inputRef}
        className="add-task-row__input"
        placeholder="Task description…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commit()
          if (e.key === 'Escape') { setValue(''); setOpen(false) }
        }}
      />
      <button className="add-task-row__ok" onClick={commit}>Add</button>
      <button className="add-task-row__cancel" onClick={() => { setValue(''); setOpen(false) }}>✕</button>
    </div>
  )
}

// ── Date group ────────────────────────────────────────────────
function DateGroupSection({
  group, onComplete, onEdit, onDelete, onAdd, completing, deleting, collapsed, onToggle
}: {
  group: DateGroup
  onComplete: (id: string) => void
  onEdit: (id: string, content: string) => void
  onDelete: (id: string) => void
  onAdd: (content: string, date: string) => void
  completing: string | null
  deleting: string | null
  collapsed: boolean
  onToggle: () => void
}) {
  const total = group.pending.length + group.completed.length
  const donePct = total === 0 ? 0 : Math.round((group.completed.length / total) * 100)
  const label = formatGroupDate(group.date)
  const isToday = group.date === todayISO()

  return (
    <div className={`date-group ${isToday ? 'date-group--today' : ''}`}>
      <div className="date-group__header" onClick={onToggle}>
        <div className="date-group__header-left">
          <span className="date-group__chevron">{collapsed ? '▶' : '▼'}</span>
          <span className={`date-group__label ${isToday ? 'date-group__label--today' : ''}`}>{label}</span>
          {isToday && <span className="date-group__today-badge">TODAY</span>}
        </div>
        <div className="date-group__header-right">
          <span className="date-group__counts">
            {group.pending.length} pending · {group.completed.length} done
          </span>
          <div className="date-group__progress-bar">
            <div className="date-group__progress-fill" style={{ width: `${donePct}%` }} />
          </div>
        </div>
      </div>

      {!collapsed && (
        <div className="date-group__body">
          {group.pending.map((t) => (
            <TaskRow key={t.id} task={t}
              onComplete={onComplete} onEdit={onEdit} onDelete={onDelete}
              completing={completing} deleting={deleting} />
          ))}
          {group.completed.map((t) => (
            <TaskRow key={t.id} task={t}
              onComplete={onComplete} onEdit={onEdit} onDelete={onDelete}
              completing={completing} deleting={deleting} />
          ))}
          {group.pending.length === 0 && group.completed.length === 0 && (
            <div className="tasks-col__empty">No tasks for this day</div>
          )}
          <AddTaskRow date={group.date} onAdd={onAdd} />
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────
const DATES_TO_SHOW = [todayISO(), yesterdayISO(), nDaysAgoISO(2), nDaysAgoISO(3), nDaysAgoISO(4)]

export default function TasksPage() {
  const today = todayISO(), yesterday = yesterdayISO()
  const countdown = useMidnightCountdown()
  const [search, setSearch]       = useState('')
  const [completing, setCompleting] = useState<string | null>(null)
  const [deleting,   setDeleting]   = useState<string | null>(null)
  const [collapsed,  setCollapsed]  = useState<Record<string, boolean>>({})

  // Local optimistic state per date
  const [localTasks, setLocalTasks] = useState<Record<string, { pending: Task[]; completed: Task[] }>>({})

  const { data: d0, refetch: r0 } = useQuery(GET_TASKS, { variables: { date: DATES_TO_SHOW[0] }, fetchPolicy: 'cache-and-network' })
  const { data: d1, refetch: r1 } = useQuery(GET_TASKS, { variables: { date: DATES_TO_SHOW[1] }, fetchPolicy: 'cache-and-network' })
  const { data: d2, refetch: r2 } = useQuery(GET_TASKS, { variables: { date: DATES_TO_SHOW[2] }, fetchPolicy: 'cache-and-network' })
  const { data: d3, refetch: r3 } = useQuery(GET_TASKS, { variables: { date: DATES_TO_SHOW[3] }, fetchPolicy: 'cache-and-network' })
  const { data: d4, refetch: r4 } = useQuery(GET_TASKS, { variables: { date: DATES_TO_SHOW[4] }, fetchPolicy: 'cache-and-network' })

  const refetches = [r0, r1, r2, r3, r4]
  const rawData   = [d0, d1, d2, d3, d4]

  const [completeTaskMutation] = useMutation(COMPLETE_TASK)
  const [addTaskMutation]      = useMutation(ADD_TASK)
  const [editTaskMutation]     = useMutation(EDIT_TASK)
  const [deleteTaskMutation]   = useMutation(DELETE_TASK)

  // Merge server data + local optimistic state into groups
  const groups: DateGroup[] = DATES_TO_SHOW.map((date, i) => {
    const server  = rawData[i]?.tasks
    const local   = localTasks[date]
    const pending   = local?.pending   ?? server?.pending   ?? []
    const completed = local?.completed ?? server?.completed ?? []
    return { date, pending, completed }
  })

  const matchSearch = (t: Task) =>
    !search.trim() || t.content.toLowerCase().includes(search.toLowerCase())

  const filteredGroups = groups.map(g => ({
    ...g,
    pending:   g.pending.filter(matchSearch),
    completed: g.completed.filter(matchSearch),
  }))

  const todayGroup    = groups[0]
  const totalToday    = todayGroup.pending.length + todayGroup.completed.length
  const quotaPct      = totalToday === 0 ? 0 : Math.round((todayGroup.completed.length / totalToday) * 100)
  const yesterdayPendingRaw = groups[1].pending

  const handleComplete = useCallback(async (taskId: string) => {
    if (completing) return
    setCompleting(taskId)
    let foundIdx = -1
    setLocalTasks(prev => {
      const next = { ...prev }
      for (let i = 0; i < DATES_TO_SHOW.length; i++) {
        const date = DATES_TO_SHOW[i]
        const serverData = rawData[i]?.tasks
        const g = prev[date] ?? { pending: serverData?.pending ?? [], completed: serverData?.completed ?? [] }
        const task = g.pending.find(t => t.id === taskId)
        if (task) {
          foundIdx = i
          next[date] = {
            pending:   g.pending.filter(t => t.id !== taskId),
            completed: [...g.completed, { ...task, status: 'complete' }],
          }
          break
        }
      }
      return next
    })
    try {
      await completeTaskMutation({ variables: { taskId } })
      if (foundIdx >= 0) await refetches[foundIdx]()
    } finally {
      setCompleting(null)
    }
  }, [completing, completeTaskMutation, rawData, refetches])

  const handleEdit = useCallback(async (taskId: string, content: string) => {
    setLocalTasks(prev => {
      const next = { ...prev }
      for (let i = 0; i < DATES_TO_SHOW.length; i++) {
        const date = DATES_TO_SHOW[i]
        const serverData = rawData[i]?.tasks
        const g = prev[date] ?? { pending: serverData?.pending ?? [], completed: serverData?.completed ?? [] }
        const inPending   = g.pending.some(t => t.id === taskId)
        const inCompleted = g.completed.some(t => t.id === taskId)
        if (inPending || inCompleted) {
          next[date] = {
            pending:   g.pending.map(t => t.id === taskId ? { ...t, content } : t),
            completed: g.completed.map(t => t.id === taskId ? { ...t, content } : t),
          }
          break
        }
      }
      return next
    })
    await editTaskMutation({ variables: { taskId, content } })
  }, [editTaskMutation, rawData])

  const handleDelete = useCallback(async (taskId: string) => {
    setDeleting(taskId)
    // Find which date group owns this task and update only that group
    setLocalTasks(prev => {
      const next = { ...prev }
      for (let i = 0; i < DATES_TO_SHOW.length; i++) {
        const date = DATES_TO_SHOW[i]
        // Use current local state if present, otherwise fall back to server data
        const serverData = rawData[i]?.tasks
        const g = prev[date] ?? { pending: serverData?.pending ?? [], completed: serverData?.completed ?? [] }
        const inPending   = g.pending.some(t => t.id === taskId)
        const inCompleted = g.completed.some(t => t.id === taskId)
        if (inPending || inCompleted) {
          next[date] = {
            pending:   g.pending.filter(t => t.id !== taskId),
            completed: g.completed.filter(t => t.id !== taskId),
          }
          break // task can only be in one group
        }
      }
      return next
    })
    try { await deleteTaskMutation({ variables: { taskId } }) }
    finally { setDeleting(null) }
  }, [deleteTaskMutation, rawData])

  const handleAdd = useCallback(async (content: string, date: string) => {
    const tempId = `temp-${Date.now()}`
    const tempTask: Task = { id: tempId, content, status: 'pending', createdDate: date }
    setLocalTasks(prev => {
      const g = prev[date] ?? { pending: [], completed: [] }
      return { ...prev, [date]: { ...g, pending: [...g.pending, tempTask] } }
    })
    try {
      const res = await addTaskMutation({ variables: { content, date } })
      const newTask = res.data?.addTask
      if (newTask) {
        setLocalTasks(prev => {
          const g = prev[date] ?? { pending: [], completed: [] }
          return { ...prev, [date]: { ...g, pending: g.pending.map(t => t.id === tempId ? newTask : t) } }
        })
      }
      const idx = DATES_TO_SHOW.indexOf(date)
      if (idx >= 0) await refetches[idx]()
    } catch {
      setLocalTasks(prev => {
        const g = prev[date] ?? { pending: [], completed: [] }
        return { ...prev, [date]: { ...g, pending: g.pending.filter(t => t.id !== tempId) } }
      })
    }
  }, [addTaskMutation])

  const handleCarryForward = useCallback(async () => {
    const token = localStorage.getItem('pb-token')
    await fetch(`${API_URL}/graphql`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify({ query: 'mutation Send($content: String!) { send(content: $content) { answer } }', variables: { content: 'carry forward all pending tasks' } })
    })
    await r0(); await r1()
    setLocalTasks({})
  }, [r0, r1])

  const toggleCollapse = (date: string) =>
    setCollapsed(prev => ({ ...prev, [date]: !prev[date] }))

  return (
    <div className="tasks-page">
      {/* ── Top bar ── */}
      <div className="tasks-page__topbar">
        <h1 className="tasks-page__title">Tasks</h1>
        <div className="tasks-page__search">
          <span className="tasks-page__search-icon">⌕</span>
          <input className="tasks-page__search-input" placeholder="Search tasks..."
            value={search} onChange={(e) => setSearch(e.target.value)} autoComplete="off" />
          {search ? (
            <button className="tasks-page__search-clear" onClick={() => setSearch('')}>✕</button>
          ) : (
            <span className="tasks-page__search-kbd">⌘K</span>
          )}
        </div>
        <div className="tasks-page__topbar-icons">
          <button className="tasks-page__icon-btn" title="Notifications">🔔</button>
          <button className="tasks-page__icon-btn" title="Sync">☁</button>
        </div>
      </div>

      <div className="tasks-page__body">
        {/* ── Left: grouped task list ── */}
        <div className="tasks-page__main">

          {/* Planning Protocol banner */}
          {yesterdayPendingRaw.length > 0 && !search && (
            <div className="planning-banner">
              <div className="planning-banner__left">
                <div className="planning-banner__row">
                  <span className="planning-banner__title">Planning Protocol</span>
                  <span className="planning-banner__urgent">URGENT</span>
                </div>
                <div className="planning-banner__desc">
                  {yesterdayPendingRaw.length} unfinished item{yesterdayPendingRaw.length !== 1 ? 's' : ''} from yesterday. Transfer to today's agenda?
                </div>
              </div>
              <div className="planning-banner__center">
                <div className="planning-banner__reset-label">SYSTEM RESET IN</div>
                <div className="planning-banner__countdown">{countdown}</div>
              </div>
              <button className="planning-banner__carry-btn" onClick={handleCarryForward}>
                <span>⬡</span><span>Carry Forward All</span>
              </button>
            </div>
          )}

          {/* Date groups */}
          <div className="date-groups">
            {filteredGroups.map((group) => (
              <DateGroupSection
                key={group.date}
                group={group}
                onComplete={handleComplete}
                onEdit={handleEdit}
                onDelete={handleDelete}
                onAdd={handleAdd}
                completing={completing}
                deleting={deleting}
                collapsed={!!collapsed[group.date]}
                onToggle={() => toggleCollapse(group.date)}
              />
            ))}
          </div>
        </div>

        {/* ── Right: Productivity Engine ── */}
        <div className="productivity-panel">
          <div className="productivity-panel__title">PRODUCTIVITY ENGINE</div>
          <div className="productivity-panel__card">
            <DonutRing pct={quotaPct} size={110} />
            <div className="productivity-panel__stats">
              <div className="productivity-panel__resolved">
                {todayGroup.completed.length} / {totalToday} Tasks Resolved
              </div>
              <div className="productivity-panel__trend">+12% vs Yesterday</div>
            </div>
          </div>
          <div className="productivity-panel__card productivity-panel__card--chart">
            <VelocityChart data={[6, 7, 10, 5, 8]} />
            <div className="productivity-panel__velocity-label">Velocity: 8.4 t/day</div>
          </div>
          <div className="productivity-panel__card productivity-panel__card--focus">
            <div className="productivity-panel__focus-header">
              <span className="productivity-panel__focus-icon">⚡</span>
              <span className="productivity-panel__focus-title">Peak Focus Detected</span>
            </div>
            <div className="productivity-panel__focus-body">
              You are 2.4x more efficient between 09:00 – 11:30.{' '}
              {todayGroup.pending.length} task{todayGroup.pending.length !== 1 ? 's' : ''} remaining for this window.
            </div>
          </div>
        </div>
      </div>

      {/* FAB */}
      <button className="tasks-page__fab" title="Add task for today"
        onClick={() => {
          setCollapsed(prev => ({ ...prev, [today]: false }))
          setTimeout(() => document.querySelector<HTMLButtonElement>('.date-group--today .add-task-row')?.click(), 50)
        }}>+</button>
    </div>
  )
}
