import React from 'react'

interface Task {
  id: string
  content: string
  status: string
  createdDate?: string
  completedDate?: string | null
  carriedOver?: boolean
}

interface TaskCardProps {
  payload: {
    pending?: Task[]
    completed?: Task[]
    tasks?: Task[]
    date?: string
  }
  onCompleteTask?: (taskId: string) => void
}

function formatDate(dateStr?: string): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr + 'T00:00:00')
    return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
  } catch { return dateStr }
}

export default function TaskCard({ payload, onCompleteTask }: TaskCardProps) {
  const [completing, setCompleting] = React.useState<string | null>(null)
  const pending   = payload.pending ?? payload.tasks ?? []
  const completed = payload.completed ?? []
  const date      = payload.date

  const handleComplete = async (taskId: string) => {
    if (!onCompleteTask || completing) return
    setCompleting(taskId)
    try { await onCompleteTask(taskId) }
    finally { setCompleting(null) }
  }

  return (
    <div className="task-card">
      <div className="task-card__header">
        <span className="task-card__title">TASKS</span>
        {date && <span className="task-card__date">{formatDate(date)}</span>}
      </div>

      <div className="task-card__list">
        {pending.map((task) => (
          <div key={task.id} className="task-card__item">
            <button
              className={`task-card__checkbox${completing === task.id ? ' task-card__checkbox--spinning' : ''}`}
              onClick={() => handleComplete(task.id)}
              disabled={!onCompleteTask || completing !== null}
              title="Mark complete"
            />
            <div className="task-card__item-body">
              <div className="task-card__item-text">{task.content}</div>
              <div className="task-card__item-meta">
                <span className="task-card__due">Due {date ? formatDate(date) : 'Today'}</span>
                {task.carriedOver && <span className="task-card__priority task-card__priority--medium">CARRIED</span>}
              </div>
            </div>
            <button className="task-card__item-action" title="Open">↗</button>
          </div>
        ))}

        {completed.map((task) => (
          <div key={task.id} className="task-card__item task-card__item--done">
            <button className="task-card__checkbox task-card__checkbox--done" disabled />
            <div className="task-card__item-body">
              <div className="task-card__item-text">{task.content}</div>
            </div>
          </div>
        ))}

        {pending.length === 0 && completed.length === 0 && (
          <div style={{ padding: '12px', fontSize: '12px', color: 'var(--text-faint)', fontFamily: 'var(--mono-font)' }}>
            No tasks found.
          </div>
        )}
      </div>

      {(pending.length > 0 || completed.length > 0) && (
        <div className="task-card__footer">
          {pending.length > 0 && <span>{pending.length} remaining</span>}
          {completed.length > 0 && <span>{completed.length} done</span>}
        </div>
      )}
    </div>
  )
}
