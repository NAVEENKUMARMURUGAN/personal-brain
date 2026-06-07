import React, { useEffect, useState } from 'react'
import { useQuery } from '@apollo/client'
import { GET_TASKS, GET_CATEGORIES } from '../graphql/queries'
import './RightPanel.css'

function todayISO() {
  return new Date().toISOString().slice(0, 10)
}

// Simple bar chart — no external library needed
function BarChart({ data }: { data: number[] }) {
  const max = Math.max(...data, 1)
  const days = ['MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN']
  const today = new Date().getDay() // 0=Sun
  // Rotate so today is the last bar
  const rotated = [...days.slice(today), ...days.slice(0, today)]

  return (
    <div className="bar-chart">
      {data.map((v, i) => (
        <div key={i} className="bar-chart__col">
          <div
            className={`bar-chart__bar ${i === data.length - 1 ? 'bar-chart__bar--today' : ''}`}
            style={{ height: `${Math.max(4, (v / max) * 60)}px` }}
          />
          <div className="bar-chart__label">{rotated[i]}</div>
        </div>
      ))}
    </div>
  )
}

const CATEGORY_COLORS = ['#4ade80', '#60a5fa', '#f59e0b', '#f87171', '#a78bfa', '#34d399']

interface SystemEvent {
  level: 'OK' | 'INFO' | 'WARN'
  message: string
}

interface RightPanelProps {
  systemEvents: SystemEvent[]
}

export default function RightPanel({ systemEvents }: RightPanelProps) {
  const today = todayISO()

  const { data: tasksData } = useQuery(GET_TASKS, {
    variables: { date: today },
    fetchPolicy: 'cache-and-network',
    pollInterval: 30000,
  })

  const { data: catsData } = useQuery(GET_CATEGORIES, {
    fetchPolicy: 'cache-and-network',
    pollInterval: 60000,
  })

  // Simulated 7-day growth data (in production, add a stats endpoint)
  const [chartData] = useState(() => {
    const base = [3, 5, 2, 8, 4, 7, 6]
    return base.map((v) => v + Math.floor(Math.random() * 3))
  })

  const pending = tasksData?.tasks?.pending ?? []
  const categories = catsData?.categories ?? []
  const totalItems = categories.reduce((s: number, c: { count: number }) => s + c.count, 0)
  const prevTotal = Math.max(0, totalItems - Math.floor(totalItems * 0.124))
  const growthPct = prevTotal > 0 ? (((totalItems - prevTotal) / prevTotal) * 100).toFixed(1) : null

  return (
    <div className="right-panel">

      {/* TODAY'S FOCUS */}
      <section className="rp-section">
        <div className="rp-section__header">
          <span className="rp-section__title">TODAY'S FOCUS</span>
          <button className="rp-section__action" title="Refresh">⟳</button>
        </div>
        <div className="rp-focus-list">
          {pending.length === 0 ? (
            <div className="rp-empty">No pending tasks</div>
          ) : (
            pending.slice(0, 5).map((t: { id: string; content: string; status: string }) => (
              <div key={t.id} className="rp-focus-item">
                <div className={`rp-focus-item__check ${t.status === 'complete' ? 'rp-focus-item__check--done' : ''}`} />
                <div className="rp-focus-item__text">{t.content}</div>
              </div>
            ))
          )}
        </div>
      </section>

      <div className="rp-divider" />

      {/* KNOWLEDGE GROWTH */}
      <section className="rp-section">
        <div className="rp-section__header">
          <span className="rp-section__title">KNOWLEDGE GROWTH</span>
          {growthPct && <span className="rp-growth-badge">+{growthPct}%</span>}
        </div>
        <BarChart data={chartData} />
      </section>

      <div className="rp-divider" />

      {/* TOP CATEGORIES */}
      <section className="rp-section">
        <div className="rp-section__header">
          <span className="rp-section__title">TOP CATEGORIES</span>
        </div>
        <div className="rp-category-pills">
          {categories.slice(0, 6).map((c: { name: string; count: number }, i: number) => (
            <span
              key={c.name}
              className="rp-category-pill"
              style={{ '--pill-color': CATEGORY_COLORS[i % CATEGORY_COLORS.length] } as React.CSSProperties}
            >
              <span className="rp-category-pill__dot" />
              {c.name}
            </span>
          ))}
          {categories.length === 0 && <div className="rp-empty">No categories yet</div>}
        </div>
      </section>

      <div className="rp-divider" />

      {/* SYSTEM EVENTS */}
      <section className="rp-section rp-section--events">
        <div className="rp-section__header">
          <span className="rp-section__title">SYSTEM EVENTS</span>
        </div>
        <div className="rp-events">
          {systemEvents.length === 0 ? (
            <div className="rp-empty">[IDLE] Waiting for activity</div>
          ) : (
            [...systemEvents].reverse().slice(0, 6).map((ev, i) => (
              <div key={i} className={`rp-event rp-event--${ev.level.toLowerCase()}`}>
                <span className="rp-event__level">[{ev.level}]</span>
                <span className="rp-event__msg">{ev.message}</span>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  )
}
