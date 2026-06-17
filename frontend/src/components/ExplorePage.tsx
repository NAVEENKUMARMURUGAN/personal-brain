/**
 * ExplorePage — Topic Explorer / Surprise Me
 *
 * Tabs: Overview | Mind Map | Flashcards | Quiz
 *
 * Dependencies:
 *   npm install mermaid   ← needed for mind map rendering
 */

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation } from '@apollo/client'
import {
  EXPLORE_TOPIC,
  SURPRISE_ME,
  SAVE_EXPLORATION_SECTION,
} from '../graphql/queries'
import './ExplorePage.css'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface Overview {
  eli5: string
  key_concepts: { term: string; definition: string }[]
  why_it_matters: string
  misconceptions: string[]
}

interface Flashcard {
  question: string
  answer: string
}

interface QuizQuestion {
  question: string
  options: string[]
  correct_index: number
  explanation: string
}

interface RelatedMemory {
  content: string
  category: string
}

interface ExplorationResult {
  topic: string
  overview: Overview
  mindmap_mermaid: string
  flashcards: Flashcard[]
  quiz: QuizQuestion[]
  related_memories: RelatedMemory[]
  cached: boolean
}

type Tab = 'overview' | 'mindmap' | 'flashcards' | 'quiz'

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ExplorePage() {
  const [topicInput, setTopicInput] = useState('')
  const [exploration, setExploration] = useState<ExplorationResult | null>(null)
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const [savedSections, setSavedSections] = useState<Set<string>>(new Set())
  const [saveMessage, setSaveMessage] = useState('')

  const [exploreTopic, { loading: exploringLoading, error: exploringError }] = useMutation(EXPLORE_TOPIC)
  const [surpriseMe, { loading: surpriseLoading }] = useMutation(SURPRISE_ME)
  const [saveSection] = useMutation(SAVE_EXPLORATION_SECTION)

  const loading = exploringLoading || surpriseLoading

  const handleExplore = useCallback(async (topic: string, regenerate = false) => {
    const t = topic.trim()
    if (!t) return
    setTopicInput(t)
    setExploration(null)
    setActiveTab('overview')
    setSavedSections(new Set())

    try {
      const { data } = await exploreTopic({ variables: { topic: t, regenerate } })
      const raw = data?.exploreTopic
      if (!raw) return

      const result: ExplorationResult = {
        topic: raw.topic,
        overview: JSON.parse(raw.overviewJson),
        mindmap_mermaid: raw.mindmapMermaid,
        flashcards: JSON.parse(raw.flashcardsJson),
        quiz: JSON.parse(raw.quizJson),
        related_memories: raw.relatedMemoriesJson ? JSON.parse(raw.relatedMemoriesJson) : [],
        cached: raw.cached,
      }
      setExploration(result)
    } catch (err) {
      console.error('ExplorePage explore error:', err)
    }
  }, [exploreTopic])

  const handleSurpriseMe = useCallback(async () => {
    try {
      const { data } = await surpriseMe()
      const topic = data?.surpriseMe
      if (topic) await handleExplore(topic)
    } catch (err) {
      console.error('ExplorePage surpriseMe error:', err)
    }
  }, [surpriseMe, handleExplore])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleExplore(topicInput)
  }

  const handleSaveSection = useCallback(async (content: string, sectionKey: string) => {
    if (!exploration) return
    try {
      await saveSection({
        variables: {
          topic: exploration.topic,
          content,
          category: `Explore — ${exploration.topic}`,
        },
      })
      setSavedSections(prev => new Set(prev).add(sectionKey))
      setSaveMessage('Saved to Brain ✓')
      setTimeout(() => setSaveMessage(''), 2000)
    } catch (err) {
      console.error('ExplorePage saveSection error:', err)
    }
  }, [exploration, saveSection])

  return (
    <div className="explore">
      {/* Search bar */}
      <div className="explore__hero">
        <div className="explore__title">
          <span className="explore__icon">✦</span> Explore
        </div>
        <p className="explore__subtitle">Type any topic for an instant learning package</p>
        <div className="explore__search-row">
          <input
            className="explore__input"
            type="text"
            placeholder="e.g. Transformer architecture, Quantum entanglement…"
            value={topicInput}
            onChange={e => setTopicInput(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={loading}
          />
          <button
            className="explore__btn explore__btn--primary"
            onClick={() => handleExplore(topicInput)}
            disabled={loading || !topicInput.trim()}
          >
            {loading ? '…' : 'Explore'}
          </button>
          <button
            className="explore__btn explore__btn--surprise"
            onClick={handleSurpriseMe}
            disabled={loading}
            title="Pick a random topic from your brain"
          >
            🎲 Surprise Me
          </button>
        </div>
        {exploringError && (
          <p className="explore__error">{exploringError.message}</p>
        )}
      </div>

      {/* Loading skeleton */}
      {loading && (
        <div className="explore__skeleton">
          <div className="explore__skeleton-bar" style={{ width: '60%' }} />
          <div className="explore__skeleton-bar" style={{ width: '80%' }} />
          <div className="explore__skeleton-bar" style={{ width: '45%' }} />
          <p className="explore__skeleton-hint">Generating your learning package…</p>
        </div>
      )}

      {/* Results */}
      {!loading && exploration && (
        <div className="explore__result">
          <div className="explore__result-header">
            <h2 className="explore__result-topic">{exploration.topic}</h2>
            {exploration.cached && (
              <span className="explore__cached-badge">cached</span>
            )}
            <button
              className="explore__btn explore__btn--ghost"
              onClick={() => handleExplore(exploration.topic, true)}
              title="Regenerate with fresh content"
            >
              ↻ Regenerate
            </button>
          </div>

          {/* Tab bar */}
          <div className="explore__tabs">
            {(['overview', 'mindmap', 'flashcards', 'quiz'] as Tab[]).map(tab => (
              <button
                key={tab}
                className={`explore__tab ${activeTab === tab ? 'explore__tab--active' : ''}`}
                onClick={() => setActiveTab(tab)}
              >
                {tab === 'overview' ? '📖 Overview'
                  : tab === 'mindmap' ? '🗺 Mind Map'
                  : tab === 'flashcards' ? '🃏 Flashcards'
                  : '❓ Quiz'}
              </button>
            ))}
          </div>

          {saveMessage && (
            <div className="explore__save-toast">{saveMessage}</div>
          )}

          {/* Tab content */}
          <div className="explore__tab-content">
            {activeTab === 'overview' && (
              <OverviewTab
                overview={exploration.overview}
                relatedMemories={exploration.related_memories}
                onSave={handleSaveSection}
                savedSections={savedSections}
              />
            )}
            {activeTab === 'mindmap' && (
              <MindMapTab mermaid={exploration.mindmap_mermaid} topic={exploration.topic} />
            )}
            {activeTab === 'flashcards' && (
              <FlashcardsTab
                cards={exploration.flashcards}
                topic={exploration.topic}
                onSave={handleSaveSection}
                savedSections={savedSections}
              />
            )}
            {activeTab === 'quiz' && (
              <QuizTab questions={exploration.quiz} />
            )}
          </div>
        </div>
      )}

      {/* Empty state */}
      {!loading && !exploration && (
        <div className="explore__empty">
          <div className="explore__empty-icon">✦</div>
          <p>Enter a topic above or hit <strong>Surprise Me</strong> to explore something from your brain.</p>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Overview Tab
// ---------------------------------------------------------------------------

function OverviewTab({
  overview,
  relatedMemories,
  onSave,
  savedSections,
}: {
  overview: Overview
  relatedMemories: RelatedMemory[]
  onSave: (content: string, key: string) => void
  savedSections: Set<string>
}) {
  const saveKey = 'overview'
  const overviewText = [
    overview.eli5,
    '\n\nKey concepts: ' + overview.key_concepts.map(k => `${k.term}: ${k.definition}`).join('; '),
    '\n\nWhy it matters: ' + overview.why_it_matters,
  ].join('')

  return (
    <div className="explore__overview">
      {relatedMemories.length > 0 && (
        <div className="explore__related-memories">
          <div className="explore__section-label">What you already know</div>
          {relatedMemories.map((m, i) => (
            <div key={i} className="explore__memory-item">
              <span className="explore__memory-tag">{m.category}</span>
              <span className="explore__memory-text">{m.content}</span>
            </div>
          ))}
        </div>
      )}

      {/* ELI5 */}
      <div className="explore__section">
        <div className="explore__section-label">Plain English</div>
        <div className="explore__eli5">
          {overview.eli5.split('\n\n').map((para, i) => (
            <p key={i}>{para}</p>
          ))}
        </div>
      </div>

      {/* Key concepts */}
      {overview.key_concepts?.length > 0 && (
        <div className="explore__section">
          <div className="explore__section-label">Key Concepts</div>
          <div className="explore__concepts">
            {overview.key_concepts.map((kc, i) => (
              <div key={i} className="explore__concept-card">
                <span className="explore__concept-term">{kc.term}</span>
                <span className="explore__concept-def">{kc.definition}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Why it matters */}
      {overview.why_it_matters && (
        <div className="explore__section">
          <div className="explore__section-label">Why It Matters</div>
          <p className="explore__why">{overview.why_it_matters}</p>
        </div>
      )}

      {/* Misconceptions */}
      {overview.misconceptions?.length > 0 && (
        <div className="explore__section">
          <div className="explore__section-label">Common Misconceptions</div>
          <ul className="explore__misconceptions">
            {overview.misconceptions.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}

      <button
        className={`explore__save-btn ${savedSections.has(saveKey) ? 'explore__save-btn--saved' : ''}`}
        onClick={() => onSave(overviewText, saveKey)}
        disabled={savedSections.has(saveKey)}
      >
        {savedSections.has(saveKey) ? '✓ Saved to Brain' : '↗ Save to Brain'}
      </button>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Mind Map Tab
// ---------------------------------------------------------------------------

function MindMapTab({ mermaid, topic }: { mermaid: string; topic: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [renderError, setRenderError] = useState('')
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!mermaid || !containerRef.current) return

    let cancelled = false

    async function render() {
      try {
        const mermaidLib = await import('mermaid')
        const m = mermaidLib.default

        m.initialize({
          startOnLoad: false,
          theme: document.documentElement.classList.contains('light') ? 'default' : 'dark',
          mindmap: { useMaxWidth: true },
        })

        const id = `mermaid-explore-${Date.now()}`
        const { svg } = await m.render(id, mermaid)

        if (!cancelled && containerRef.current) {
          containerRef.current.innerHTML = svg
          setRenderError('')
        }
      } catch (err: any) {
        if (!cancelled) {
          console.error('Mermaid render error:', err)
          setRenderError('Could not render mind map. Raw syntax shown below.')
        }
      }
    }

    render()
    return () => { cancelled = true }
  }, [mermaid])

  const handleCopy = () => {
    navigator.clipboard.writeText(mermaid).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  return (
    <div className="explore__mindmap-tab">
      <div className="explore__mindmap-toolbar">
        <button className="explore__btn explore__btn--ghost" onClick={handleCopy}>
          {copied ? '✓ Copied' : 'Copy Mermaid'}
        </button>
      </div>
      {renderError ? (
        <div className="explore__mindmap-fallback">
          <p className="explore__error">{renderError}</p>
          <pre className="explore__mermaid-raw">{mermaid}</pre>
        </div>
      ) : (
        <div className="explore__mindmap-container" ref={containerRef} />
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Flashcards Tab
// ---------------------------------------------------------------------------

function FlashcardsTab({
  cards,
  topic,
  onSave,
  savedSections,
}: {
  cards: Flashcard[]
  topic: string
  onSave: (content: string, key: string) => void
  savedSections: Set<string>
}) {
  const [currentIndex, setCurrentIndex] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [order, setOrder] = useState<number[]>(cards.map((_, i) => i))

  const card = cards[order[currentIndex]]
  const total = cards.length

  const handleShuffle = () => {
    const shuffled = [...order].sort(() => Math.random() - 0.5)
    setOrder(shuffled)
    setCurrentIndex(0)
    setFlipped(false)
  }

  const handleNext = () => {
    setCurrentIndex(i => Math.min(i + 1, total - 1))
    setFlipped(false)
  }

  const handlePrev = () => {
    setCurrentIndex(i => Math.max(i - 1, 0))
    setFlipped(false)
  }

  const saveKey = `flashcard-${order[currentIndex]}`
  const saveContent = `Q: ${card?.question}\nA: ${card?.answer}`

  if (!card) return null

  return (
    <div className="explore__flashcards">
      <div className="explore__flashcard-progress">
        {currentIndex + 1} / {total}
      </div>

      <div
        className={`explore__card ${flipped ? 'explore__card--flipped' : ''}`}
        onClick={() => setFlipped(f => !f)}
        role="button"
        tabIndex={0}
        onKeyDown={e => e.key === 'Enter' && setFlipped(f => !f)}
        aria-label={flipped ? 'Answer (click to flip back)' : 'Question (click to reveal answer)'}
      >
        <div className="explore__card-inner">
          <div className="explore__card-front">
            <span className="explore__card-label">Question</span>
            <p className="explore__card-text">{card.question}</p>
            <span className="explore__card-hint">tap to reveal answer</span>
          </div>
          <div className="explore__card-back">
            <span className="explore__card-label">Answer</span>
            <p className="explore__card-text">{card.answer}</p>
          </div>
        </div>
      </div>

      <div className="explore__flashcard-nav">
        <button
          className="explore__btn explore__btn--ghost"
          onClick={handlePrev}
          disabled={currentIndex === 0}
        >
          ← Prev
        </button>
        <button
          className="explore__btn explore__btn--ghost"
          onClick={handleShuffle}
        >
          Shuffle
        </button>
        <button
          className={`explore__save-btn explore__save-btn--sm ${savedSections.has(saveKey) ? 'explore__save-btn--saved' : ''}`}
          onClick={() => onSave(saveContent, saveKey)}
          disabled={savedSections.has(saveKey)}
        >
          {savedSections.has(saveKey) ? '✓' : '↗ Save'}
        </button>
        <button
          className="explore__btn explore__btn--ghost"
          onClick={handleNext}
          disabled={currentIndex === total - 1}
        >
          Next →
        </button>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Quiz Tab
// ---------------------------------------------------------------------------

function QuizTab({ questions }: { questions: QuizQuestion[] }) {
  const [answers, setAnswers] = useState<(number | null)[]>(questions.map(() => null))
  const [submitted, setSubmitted] = useState(false)
  const [showResults, setShowResults] = useState(false)

  const handleSelect = (qIndex: number, optIndex: number) => {
    if (submitted) return
    setAnswers(prev => {
      const next = [...prev]
      next[qIndex] = optIndex
      return next
    })
  }

  const handleSubmit = () => {
    setSubmitted(true)
    setShowResults(true)
  }

  const handleReset = () => {
    setAnswers(questions.map(() => null))
    setSubmitted(false)
    setShowResults(false)
  }

  const score = submitted
    ? answers.reduce<number>((acc, a, i) => acc + (a === questions[i].correct_index ? 1 : 0), 0)
    : 0

  const verdict = score === questions.length
    ? 'Perfect score! You nailed it. 🎉'
    : score >= Math.ceil(questions.length * 0.6)
    ? 'Nice work! A few things to revisit.'
    : 'Review the basics and try again.'

  return (
    <div className="explore__quiz">
      {showResults && (
        <div className="explore__quiz-score">
          <span className="explore__quiz-score-num">{score}/{questions.length}</span>
          <span className="explore__quiz-verdict">{verdict}</span>
          <button className="explore__btn explore__btn--ghost" onClick={handleReset}>
            Try Again
          </button>
        </div>
      )}

      {questions.map((q, qi) => {
        const selected = answers[qi]
        const correct = q.correct_index
        const isCorrect = selected === correct

        return (
          <div key={qi} className={`explore__quiz-question ${submitted ? (isCorrect ? 'explore__quiz-question--correct' : selected !== null ? 'explore__quiz-question--wrong' : '') : ''}`}>
            <p className="explore__quiz-q">
              <span className="explore__quiz-qnum">{qi + 1}.</span> {q.question}
            </p>
            <div className="explore__quiz-options">
              {q.options.map((opt, oi) => {
                let cls = 'explore__quiz-option'
                if (submitted) {
                  if (oi === correct) cls += ' explore__quiz-option--correct'
                  else if (oi === selected && selected !== correct) cls += ' explore__quiz-option--wrong'
                } else if (oi === selected) {
                  cls += ' explore__quiz-option--selected'
                }
                return (
                  <button
                    key={oi}
                    className={cls}
                    onClick={() => handleSelect(qi, oi)}
                    disabled={submitted}
                  >
                    <span className="explore__quiz-opt-letter">
                      {String.fromCharCode(65 + oi)}.
                    </span>
                    {opt}
                  </button>
                )
              })}
            </div>
            {submitted && selected !== null && !isCorrect && (
              <div className="explore__quiz-explanation">
                {q.explanation}
              </div>
            )}
          </div>
        )
      })}

      {!submitted && (
        <button
          className="explore__btn explore__btn--primary explore__quiz-submit"
          onClick={handleSubmit}
          disabled={answers.some(a => a === null)}
        >
          Submit
        </button>
      )}
    </div>
  )
}
