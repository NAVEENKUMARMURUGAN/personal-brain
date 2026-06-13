import React, { useState, useRef, useEffect, useCallback } from 'react'
import { useMutation, useQuery, useApolloClient } from '@apollo/client'
import { SEND_MESSAGE, GET_MESSAGES, GET_MEMORIES, COMPLETE_TASK } from '../graphql/queries'
import MessageRenderer, { ChatMessage } from './MessageRenderer'
import UploadModal from './UploadModal'
import { API_URL } from '../config'
import './Chat.css'

let localIdCounter = 0
function localId() {
  return `local-${++localIdCounter}-${Date.now()}`
}

type RecordingState = 'idle' | 'recording' | 'transcribing'

interface ChatProps {
  onDrillCategory: (category: string) => void
  onAgentAction?: (action: string, detail?: string) => void
  initialMessage?: string
  onInitialMessageConsumed?: () => void
}

export default function Chat({ onDrillCategory, onAgentAction, initialMessage, onInitialMessageConsumed }: ChatProps) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  // Persist cleared state in sessionStorage so refresh doesn't reload history
  const [clearedAt, setClearedAt] = useState<string | null>(
    () => sessionStorage.getItem('pb-cleared-at')
  )
  const [sessionOnly, setSessionOnly] = useState(
    () => sessionStorage.getItem('pb-session-only') === '1'
  )
  const [recordingState, setRecordingState] = useState<RecordingState>('idle')
  const [showUpload, setShowUpload] = useState(false)

  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const initialMessageHandled = useRef(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const apolloClient = useApolloClient()

  // Skip the history query once the user clears — never restore old messages
  const { data: historyData } = useQuery(GET_MESSAGES, {
    variables: { limit: 50 },
    fetchPolicy: 'network-only',
    skip: sessionOnly,
  })

  useEffect(() => {
    if (sessionOnly) return
    if (historyData?.messages?.messages) {
      const loaded: ChatMessage[] = [...historyData.messages.messages].reverse()
      setMessages(loaded)
    }
  }, [historyData, sessionOnly])

  const handleClear = useCallback(() => {
    const ts = new Date().toISOString()
    setMessages([])
    setSessionOnly(true)
    setClearedAt(ts)
    // Persist so refresh keeps the cleared state
    sessionStorage.setItem('pb-session-only', '1')
    sessionStorage.setItem('pb-cleared-at', ts)
    // Evict Apollo cache so stale data can't re-trigger the effect
    apolloClient.cache.evict({ fieldName: 'messages' })
    apolloClient.cache.gc()
  }, [apolloClient])

  const [sendMutation] = useMutation(SEND_MESSAGE)
  const [completeTaskMutation] = useMutation(COMPLETE_TASK)

  const scrollToBottom = useCallback(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  // Pre-fill input when navigating from dashboard
  useEffect(() => {
    if (initialMessage && !initialMessageHandled.current) {
      initialMessageHandled.current = true
      setInput(initialMessage)
      setTimeout(() => textareaRef.current?.focus(), 50)
      onInitialMessageConsumed?.()
    }
  }, [initialMessage, onInitialMessageConsumed])

  const handleDrill = useCallback(async (category: string) => {
    try {
      const result = await apolloClient.query({
        query: GET_MEMORIES,
        variables: { category, limit: 20 },
        fetchPolicy: 'network-only',
      })

      const memoriesPage = result.data?.memories
      if (!memoriesPage) return

      const assistantMsg: ChatMessage = {
        id: localId(),
        content: '',
        type: 'memory_list',
        role: 'assistant',
        payload: { category, memories: memoriesPage.memories },
        createdAt: new Date().toISOString(),
      }
      setMessages((prev) => [...prev, assistantMsg])
    } catch {
      setMessages((prev) => [...prev, {
        id: localId(),
        content: 'Could not load memories right now.',
        type: 'text',
        role: 'assistant',
        payload: null,
        createdAt: new Date().toISOString(),
      }])
    }
  }, [apolloClient])

  useEffect(() => { onDrillCategory }, [onDrillCategory])

  const handleCompleteTask = useCallback(async (taskId: string) => {
    try {
      await completeTaskMutation({ variables: { taskId } })
      setMessages((prev) => prev.map((msg) => {
        if (msg.type !== 'task_list') return msg
        const p = typeof msg.payload === 'string' ? JSON.parse(msg.payload) : msg.payload
        if (!p) return msg
        const taskInPending = (p.pending ?? p.tasks ?? []).find((t: { id: string }) => t.id === taskId)
        if (!taskInPending) return msg
        const newPending = (p.pending ?? p.tasks ?? []).filter((t: { id: string }) => t.id !== taskId)
        const newCompleted = [...(p.completed ?? []), { ...taskInPending, status: 'complete' }]
        return { ...msg, payload: { ...p, pending: newPending, completed: newCompleted } }
      }))
    } catch (err) {
      console.error('Failed to complete task:', err)
    }
  }, [completeTaskMutation])

  const handleSend = useCallback(async (overrideContent?: string) => {
    const content = (overrideContent ?? input).trim()
    if (!content || loading) return

    setMessages((prev) => [...prev, {
      id: localId(), content, type: 'text', role: 'user',
      payload: null, createdAt: new Date().toISOString(),
    }])
    setInput('')
    setLoading(true)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }

    try {
      const result = await sendMutation({ variables: { content, clearedAt } })
      const resp = result.data?.send
      if (resp) {
        setMessages((prev) => [...prev, {
          id: localId(), content: resp.answer, type: resp.type,
          role: 'assistant', payload: resp.payload, sources: resp.sources ?? [],
          createdAt: new Date().toISOString(),
        }])
        onAgentAction?.(resp.action, resp.answer?.slice(0, 40))
      }
    } catch {
      setMessages((prev) => [...prev, {
        id: localId(), content: 'Something went wrong. Please try again.',
        type: 'text', role: 'assistant', payload: null,
        createdAt: new Date().toISOString(),
      }])
    } finally {
      setLoading(false)
    }
  }, [input, loading, sendMutation, clearedAt])

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }, [handleSend])

  const handleInputChange = useCallback((e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value)
    const el = e.target
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 160) + 'px'
  }, [])

  // ── Voice recording ────────────────────────────────────────

  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      audioChunksRef.current = []

      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : 'audio/webm'

      const recorder = new MediaRecorder(stream, { mimeType })
      mediaRecorderRef.current = recorder

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data)
      }

      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setRecordingState('transcribing')

        const blob = new Blob(audioChunksRef.current, { type: mimeType })
        const formData = new FormData()
        formData.append('audio', blob, 'voice.webm')

        try {
          const token = localStorage.getItem('pb-token')
          const res = await fetch(`${API_URL}/transcribe`, {
            method: 'POST',
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            body: formData,
          })
          const data = await res.json()
          if (data.text) {
            setInput(data.text)
            textareaRef.current?.focus()
          } else {
            console.warn('Transcription empty or failed:', data.error)
          }
        } catch (err) {
          console.error('Transcription request failed:', err)
        } finally {
          setRecordingState('idle')
        }
      }

      recorder.start()
      setRecordingState('recording')
    } catch (err) {
      console.error('Microphone access denied:', err)
      setRecordingState('idle')
    }
  }, [])

  const stopRecording = useCallback(() => {
    mediaRecorderRef.current?.stop()
  }, [])

  const handleUploadSuccess = useCallback((filename: string, category: string, saved: number) => {
    const msg = saved === 0
      ? `Already have all content from **${filename}** saved.`
      : `Saved **${saved} chunk${saved !== 1 ? 's' : ''}** from **${filename}** under **${category}**.`
    setMessages((prev) => [...prev, {
      id: localId(), content: msg, type: 'text', role: 'assistant',
      payload: null, createdAt: new Date().toISOString(),
    }])
  }, [])

  const handleMicClick = useCallback(() => {
    if (recordingState === 'idle') {
      startRecording()
    } else if (recordingState === 'recording') {
      stopRecording()
    }
  }, [recordingState, startRecording, stopRecording])

  // ── Render ─────────────────────────────────────────────────

  const micLabel =
    recordingState === 'recording' ? '⏹' :
    recordingState === 'transcribing' ? '…' : '🎙'

  const micTitle =
    recordingState === 'recording' ? 'Stop recording' :
    recordingState === 'transcribing' ? 'Transcribing…' : 'Record voice note'

  return (
    <div className="chat">
      {showUpload && (
        <UploadModal
          onClose={() => setShowUpload(false)}
          onSuccess={handleUploadSuccess}
        />
      )}
      {messages.length > 0 && (
        <div className="chat__toolbar">
          <button className="chat__clear-btn" onClick={handleClear} title="Clear chat view">
            Clear
          </button>
        </div>
      )}

      <div className="chat__messages">
        {messages.length === 0 && !loading && (
          <div className="chat__empty">
            <div className="chat__empty-icon">◈</div>
            <div className="chat__empty-title">What's on your mind?</div>
            <div className="chat__empty-hint">
              Paste anything to remember it · ask questions · manage your day
            </div>
            <div className="chat__suggestions">
              {['Show my tasks', "What did I save about Kafka?", 'Add tasks for today', 'Show my knowledge base'].map((s) => (
                <button key={s} className="chat__suggestion-chip" onClick={() => handleSend(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg) => (
          <MessageRenderer
            key={msg.id}
            message={msg}
            onDrill={handleDrill}
            onCompleteTask={handleCompleteTask}
          />
        ))}

        {loading && (
          <div className="chat__loading">
            <span className="chat__loading-dot" />
            <span className="chat__loading-dot" />
            <span className="chat__loading-dot" />
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      <div className="chat__input-bar">
        <div className="chat__input-inner">
          <button
            className="chat__input-icon-btn"
            title="Chat mode"
            aria-label="Chat mode"
          >
            ⬡
          </button>
          <button
            className="chat__input-icon-btn"
            onClick={() => setShowUpload(true)}
            disabled={loading}
            title="Upload file (PDF, DOCX, XLSX, TXT)"
            aria-label="Upload file"
          >
            +
          </button>
          <textarea
            ref={textareaRef}
            className="chat__textarea"
            value={input}
            onChange={handleInputChange}
            onKeyDown={handleKeyDown}
            placeholder={
              recordingState === 'recording' ? 'Recording… click ⏹ to stop' :
              recordingState === 'transcribing' ? 'Transcribing…' :
              'Type a command or ask anything...'
            }
            rows={1}
            disabled={loading || recordingState !== 'idle'}
          />
          <button
            className={`chat__mic-btn ${recordingState !== 'idle' ? `chat__mic-btn--${recordingState}` : ''}`}
            onClick={handleMicClick}
            disabled={loading || recordingState === 'transcribing'}
            title={micTitle}
            aria-label={micTitle}
          >
            {micLabel === '🎙' ? '🎙' : micLabel}
          </button>
          <button
            className="chat__kbd-btn"
            onClick={() => handleSend()}
            disabled={loading || !input.trim() || recordingState !== 'idle'}
            title="Send (Enter)"
          >
            ⌘K
          </button>
        </div>
      </div>
    </div>
  )
}
