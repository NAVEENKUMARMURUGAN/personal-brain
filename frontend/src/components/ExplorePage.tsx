/**
 * ExplorePage — Topic Explorer / Surprise Me
 *
 * Layout:
 *   [History sidebar] | [Search + content]
 *
 * Overview tab has a Layman / Engineer toggle.
 * New sections: Use Cases, Sample Implementation.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useMutation, useQuery } from '@apollo/client'
import {
  EXPLORE_TOPIC,
  SURPRISE_ME,
  SAVE_EXPLORATION_SECTION,
  LIST_EXPLORATIONS,
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

interface EngineerSection {
  deep_dive: string
  internals: { aspect: string; detail: string }[]
  trade_offs: { pro: string; con: string }[]
}

interface UseCase {
  title: string
  company_or_context: string
  description: string
}

interface SampleImplementation {
  applicable: boolean
  language: string
  description: string
  code: string | null
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
  topicSlug: string
  overview: Overview
  engineer: EngineerSection
  use_cases: UseCase[]
  sample_implementation: SampleImplementation | null
  mindmap_mermaid: string
  flashcards: Flashcard[]
  quiz: QuizQuestion[]
  related_memories: RelatedMemory[]
  cached: boolean
  createdAt: string
}

interface HistoryItem {
  id: string
  topic: string
  topicSlug: string
  createdAt: string
}

type Tab = 'overview' | 'mindmap' | 'flashcards' | 'quiz'
type OverviewMode = 'layman' | 'engineer'

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export default function ExplorePage() {
  const [topicInput, setTopicInput] = useState('')
  const [exploration, setExploration] = useState<ExplorationResult | null>(null)
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const [overviewMode, setOverviewMode] = useState<OverviewMode>('layman')
  const [savedSections, setSavedSections] = useState<Set<string>>(new Set())
  const [saveMessage, setSaveMessage] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(true)

  const [exploreTopic, { loading: exploringLoading, error: exploringError }] = useMutation(EXPLORE_TOPIC)
  const [surpriseMe, { loading: surpriseLoading }] = useMutation(SURPRISE_ME)
  const [saveSection, { loading: saveLoading }] = useMutation(SAVE_EXPLORATION_SECTION)

  const { data: historyData, refetch: refetchHistory } = useQuery(LIST_EXPLORATIONS, {
    variables: { limit: 30 },
    fetchPolicy: 'cache-and-network',
  })

  const history: HistoryItem[] = historyData?.listExplorations ?? []
  const loading = exploringLoading || surpriseLoading

  const parseExploration = (raw: any): ExplorationResult => ({
    topic: raw.topic,
    topicSlug: raw.topicSlug,
    overview: JSON.parse(raw.overviewJson),
    engineer: raw.engineerJson ? JSON.parse(raw.engineerJson) : { deep_dive: '', internals: [], trade_offs: [] },
    use_cases: raw.useCasesJson ? JSON.parse(raw.useCasesJson) : [],
    sample_implementation: raw.sampleImplementationJson ? JSON.parse(raw.sampleImplementationJson) : null,
    mindmap_mermaid: raw.mindmapMermaid,
    flashcards: JSON.parse(raw.flashcardsJson),
    quiz: JSON.parse(raw.quizJson),
    related_memories: raw.relatedMemoriesJson ? JSON.parse(raw.relatedMemoriesJson) : [],
    cached: raw.cached,
    createdAt: raw.createdAt,
  })

  const handleExplore = useCallback(async (topic: string, regenerate = false) => {
    const t = topic.trim()
    if (!t) return
    setTopicInput(t)
    setExploration(null)
    setActiveTab('overview')
    setOverviewMode('layman')
    setSavedSections(new Set())
    setSaveMessage('')

    try {
      const { data } = await exploreTopic({ variables: { topic: t, regenerate } })
      if (data?.exploreTopic) {
        setExploration(parseExploration(data.exploreTopic))
        refetchHistory()
      }
    } catch (err) {
      console.error('ExplorePage explore error:', err)
    }
  }, [exploreTopic, refetchHistory])

  const handleSurpriseMe = useCallback(async () => {
    try {
      const { data } = await surpriseMe()
      if (data?.surpriseMe) await handleExplore(data.surpriseMe)
    } catch (err) {
      console.error('ExplorePage surpriseMe error:', err)
    }
  }, [surpriseMe, handleExplore])

  const handleHistoryClick = useCallback(async (item: HistoryItem) => {
    await handleExplore(item.topic)
  }, [handleExplore])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleExplore(topicInput)
  }

  const handleSaveSection = useCallback(async (content: string, sectionKey: string) => {
    if (!exploration || saveLoading) return
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
      setTimeout(() => setSaveMessage(''), 2500)
    } catch (err) {
      console.error('Save error:', err)
      setSaveMessage('Save failed — check connection')
      setTimeout(() => setSaveMessage(''), 2500)
    }
  }, [exploration, saveSection, saveLoading])

  return (
    <div className="explore">
      {/* History sidebar */}
      <aside className={`explore__sidebar ${sidebarOpen ? 'explore__sidebar--open' : ''}`}>
        <div className="explore__sidebar-header">
          <span className="explore__sidebar-title">History</span>
          <button
            className="explore__sidebar-toggle"
            onClick={() => setSidebarOpen(o => !o)}
            title={sidebarOpen ? 'Collapse sidebar' : 'Expand sidebar'}
          >
            {sidebarOpen ? '◂' : '▸'}
          </button>
        </div>
        {sidebarOpen && (
          <div className="explore__sidebar-list">
            {history.length === 0 && (
              <p className="explore__sidebar-empty">No explorations yet</p>
            )}
            {history.map(item => (
              <button
                key={item.id}
                className={`explore__sidebar-item ${exploration?.topicSlug === item.topicSlug ? 'explore__sidebar-item--active' : ''}`}
                onClick={() => handleHistoryClick(item)}
                title={item.topic}
              >
                <span className="explore__sidebar-item-icon">✦</span>
                <span className="explore__sidebar-item-label">{item.topic}</span>
              </button>
            ))}
          </div>
        )}
      </aside>

      {/* Main content */}
      <div className="explore__main">
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
            <div className="explore__skeleton-bar" style={{ width: '55%' }} />
            <div className="explore__skeleton-bar" style={{ width: '75%' }} />
            <div className="explore__skeleton-bar" style={{ width: '40%' }} />
            <div className="explore__skeleton-bar" style={{ width: '65%' }} />
            <p className="explore__skeleton-hint">Generating your learning package — this takes ~15 seconds…</p>
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
              <div className={`explore__save-toast ${saveMessage.includes('failed') ? 'explore__save-toast--error' : ''}`}>
                {saveMessage}
              </div>
            )}

            <div className="explore__tab-content">
              {activeTab === 'overview' && (
                <OverviewTab
                  overview={exploration.overview}
                  engineer={exploration.engineer}
                  useCases={exploration.use_cases}
                  sampleImpl={exploration.sample_implementation}
                  relatedMemories={exploration.related_memories}
                  mode={overviewMode}
                  onModeChange={setOverviewMode}
                  onSave={handleSaveSection}
                  savedSections={savedSections}
                  topic={exploration.topic}
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
    </div>
  )
}

// ---------------------------------------------------------------------------
// Overview Tab (Layman + Engineer toggle, Use Cases, Implementation)
// ---------------------------------------------------------------------------

function OverviewTab({
  overview,
  engineer,
  useCases,
  sampleImpl,
  relatedMemories,
  mode,
  onModeChange,
  onSave,
  savedSections,
  topic,
}: {
  overview: Overview
  engineer: EngineerSection
  useCases: UseCase[]
  sampleImpl: SampleImplementation | null
  relatedMemories: RelatedMemory[]
  mode: OverviewMode
  onModeChange: (m: OverviewMode) => void
  onSave: (content: string, key: string) => void
  savedSections: Set<string>
  topic: string
}) {
  const [copiedCode, setCopiedCode] = useState(false)

  const laymanText = [
    overview.eli5,
    overview.key_concepts?.map(k => `${k.term}: ${k.definition}`).join('\n'),
    `Why it matters: ${overview.why_it_matters}`,
    `Misconceptions: ${overview.misconceptions?.join(' | ')}`,
  ].join('\n\n')

  const engineerText = [
    engineer.deep_dive,
    engineer.internals?.map(i => `${i.aspect}: ${i.detail}`).join('\n'),
    engineer.trade_offs?.map(t => `+ ${t.pro} / − ${t.con}`).join('\n'),
  ].join('\n\n')

  const useCasesText = useCases?.map(u => `${u.title} (${u.company_or_context}): ${u.description}`).join('\n\n')

  const handleCopyCode = () => {
    if (sampleImpl?.code) {
      navigator.clipboard.writeText(sampleImpl.code).then(() => {
        setCopiedCode(true)
        setTimeout(() => setCopiedCode(false), 2000)
      })
    }
  }

  return (
    <div className="explore__overview">
      {/* Mode toggle */}
      <div className="explore__mode-toggle">
        <button
          className={`explore__mode-btn ${mode === 'layman' ? 'explore__mode-btn--active' : ''}`}
          onClick={() => onModeChange('layman')}
        >
          🧩 Layman
        </button>
        <button
          className={`explore__mode-btn ${mode === 'engineer' ? 'explore__mode-btn--active' : ''}`}
          onClick={() => onModeChange('engineer')}
        >
          ⚙ Engineer
        </button>
      </div>

      {/* What you already know */}
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

      {/* LAYMAN MODE */}
      {mode === 'layman' && (
        <>
          <div className="explore__section">
            <div className="explore__section-label">Plain English</div>
            <div className="explore__eli5">
              {overview.eli5?.split('\n\n').map((para, i) => <p key={i}>{para}</p>)}
            </div>
          </div>

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

          {overview.why_it_matters && (
            <div className="explore__section">
              <div className="explore__section-label">Why It Matters</div>
              <p className="explore__why">{overview.why_it_matters}</p>
            </div>
          )}

          {overview.misconceptions?.length > 0 && (
            <div className="explore__section">
              <div className="explore__section-label">Common Misconceptions</div>
              <ul className="explore__misconceptions">
                {overview.misconceptions.map((m, i) => <li key={i}>{m}</li>)}
              </ul>
            </div>
          )}

          <SaveBtn
            label="Save overview to Brain"
            saveKey="overview-layman"
            savedSections={savedSections}
            onSave={() => onSave(laymanText, 'overview-layman')}
          />
        </>
      )}

      {/* ENGINEER MODE */}
      {mode === 'engineer' && (
        <>
          <div className="explore__section">
            <div className="explore__section-label">Deep Dive</div>
            <div className="explore__eli5">
              {engineer.deep_dive?.split('\n\n').map((para, i) => <p key={i}>{para}</p>)}
            </div>
          </div>

          {engineer.internals?.length > 0 && (
            <div className="explore__section">
              <div className="explore__section-label">Internals</div>
              <div className="explore__internals">
                {engineer.internals.map((item, i) => (
                  <div key={i} className="explore__internal-card">
                    <span className="explore__internal-aspect">{item.aspect}</span>
                    <span className="explore__internal-detail">{item.detail}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {engineer.trade_offs?.length > 0 && (
            <div className="explore__section">
              <div className="explore__section-label">Trade-offs</div>
              <div className="explore__tradeoffs">
                {engineer.trade_offs.map((t, i) => (
                  <div key={i} className="explore__tradeoff-row">
                    <span className="explore__tradeoff-pro">+ {t.pro}</span>
                    <span className="explore__tradeoff-con">− {t.con}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <SaveBtn
            label="Save engineer view to Brain"
            saveKey="overview-engineer"
            savedSections={savedSections}
            onSave={() => onSave(engineerText, 'overview-engineer')}
          />
        </>
      )}

      {/* USE CASES — always visible regardless of mode */}
      {useCases?.length > 0 && (
        <div className="explore__section explore__section--divider">
          <div className="explore__section-label">Real-World Use Cases</div>
          <div className="explore__use-cases">
            {useCases.map((uc, i) => (
              <div key={i} className="explore__use-case-card">
                <div className="explore__use-case-header">
                  <span className="explore__use-case-title">{uc.title}</span>
                  <span className="explore__use-case-company">{uc.company_or_context}</span>
                </div>
                <p className="explore__use-case-desc">{uc.description}</p>
              </div>
            ))}
          </div>
          <SaveBtn
            label="Save use cases to Brain"
            saveKey="use-cases"
            savedSections={savedSections}
            onSave={() => onSave(useCasesText, 'use-cases')}
          />
        </div>
      )}

      {/* SAMPLE IMPLEMENTATION — only if applicable */}
      {sampleImpl?.applicable && sampleImpl.code && (
        <div className="explore__section explore__section--divider">
          <div className="explore__section-label-row">
            <div className="explore__section-label">
              Sample Implementation
              <span className="explore__lang-badge">{sampleImpl.language}</span>
            </div>
            <button className="explore__btn explore__btn--ghost explore__btn--sm" onClick={handleCopyCode}>
              {copiedCode ? '✓ Copied' : 'Copy'}
            </button>
          </div>
          {sampleImpl.description && (
            <p className="explore__impl-desc">{sampleImpl.description}</p>
          )}
          <pre className="explore__code-block">
            <code>{sampleImpl.code}</code>
          </pre>
          <SaveBtn
            label="Save implementation to Brain"
            saveKey="implementation"
            savedSections={savedSections}
            onSave={() => onSave(`${sampleImpl.description}\n\n\`\`\`${sampleImpl.language}\n${sampleImpl.code}\n\`\`\``, 'implementation')}
          />
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Shared Save Button
// ---------------------------------------------------------------------------

function SaveBtn({
  label,
  saveKey,
  savedSections,
  onSave,
}: {
  label: string
  saveKey: string
  savedSections: Set<string>
  onSave: () => void
}) {
  const saved = savedSections.has(saveKey)
  return (
    <button
      className={`explore__save-btn ${saved ? 'explore__save-btn--saved' : ''}`}
      onClick={onSave}
      disabled={saved}
    >
      {saved ? '✓ Saved to Brain' : `↗ ${label}`}
    </button>
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
          setRenderError('Could not render mind map — raw syntax shown below.')
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
    setOrder([...order].sort(() => Math.random() - 0.5))
    setCurrentIndex(0)
    setFlipped(false)
  }

  const handleNext = () => { setCurrentIndex(i => Math.min(i + 1, total - 1)); setFlipped(false) }
  const handlePrev = () => { setCurrentIndex(i => Math.max(i - 1, 0)); setFlipped(false) }

  const saveKey = `flashcard-${order[currentIndex]}`
  const saveContent = card ? `Q: ${card.question}\nA: ${card.answer}` : ''

  if (!card) return null

  return (
    <div className="explore__flashcards">
      <div className="explore__flashcard-progress">{currentIndex + 1} / {total}</div>
      <div
        className={`explore__card ${flipped ? 'explore__card--flipped' : ''}`}
        onClick={() => setFlipped(f => !f)}
        role="button"
        tabIndex={0}
        onKeyDown={e => e.key === 'Enter' && setFlipped(f => !f)}
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
        <button className="explore__btn explore__btn--ghost" onClick={handlePrev} disabled={currentIndex === 0}>← Prev</button>
        <button className="explore__btn explore__btn--ghost" onClick={handleShuffle}>Shuffle</button>
        <SaveBtn label="Save" saveKey={saveKey} savedSections={savedSections} onSave={() => onSave(saveContent, saveKey)} />
        <button className="explore__btn explore__btn--ghost" onClick={handleNext} disabled={currentIndex === total - 1}>Next →</button>
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

  const handleSelect = (qi: number, oi: number) => {
    if (submitted) return
    setAnswers(prev => { const next = [...prev]; next[qi] = oi; return next })
  }

  const handleReset = () => {
    setAnswers(questions.map(() => null))
    setSubmitted(false)
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
      {submitted && (
        <div className="explore__quiz-score">
          <span className="explore__quiz-score-num">{score}/{questions.length}</span>
          <span className="explore__quiz-verdict">{verdict}</span>
          <button className="explore__btn explore__btn--ghost" onClick={handleReset}>Try Again</button>
        </div>
      )}
      {questions.map((q, qi) => {
        const selected = answers[qi]
        const correct = q.correct_index
        const isCorrect = selected === correct
        return (
          <div key={qi} className={`explore__quiz-question ${submitted ? (isCorrect ? 'explore__quiz-question--correct' : selected !== null ? 'explore__quiz-question--wrong' : '') : ''}`}>
            <p className="explore__quiz-q"><span className="explore__quiz-qnum">{qi + 1}.</span> {q.question}</p>
            <div className="explore__quiz-options">
              {q.options.map((opt, oi) => {
                let cls = 'explore__quiz-option'
                if (submitted) {
                  if (oi === correct) cls += ' explore__quiz-option--correct'
                  else if (oi === selected && !isCorrect) cls += ' explore__quiz-option--wrong'
                } else if (oi === selected) cls += ' explore__quiz-option--selected'
                return (
                  <button key={oi} className={cls} onClick={() => handleSelect(qi, oi)} disabled={submitted}>
                    <span className="explore__quiz-opt-letter">{String.fromCharCode(65 + oi)}.</span>
                    {opt}
                  </button>
                )
              })}
            </div>
            {submitted && selected !== null && !isCorrect && (
              <div className="explore__quiz-explanation">{q.explanation}</div>
            )}
          </div>
        )
      })}
      {!submitted && (
        <button className="explore__btn explore__btn--primary explore__quiz-submit" onClick={() => setSubmitted(true)} disabled={answers.some(a => a === null)}>
          Submit
        </button>
      )}
    </div>
  )
}
