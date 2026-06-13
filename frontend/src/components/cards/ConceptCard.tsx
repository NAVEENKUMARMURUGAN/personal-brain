import React, { useCallback, useState } from 'react'
import { useMutation } from '@apollo/client'
import { REVIEW_LEARNING_CARD } from '../../graphql/queries'
import './ConceptCard.css'

interface LearningCardData {
  id: string
  term: string
  explanation: string
  usageLine?: string | null
  codeExample?: string | null
  pathwayNode: string
  ease: number
  timesSeen: number
  mastered: boolean
}

interface ConceptCardProps {
  card: LearningCardData
  onReviewed?: () => void
}

const PATHWAY_NODES = [
  'fundamentals',
  'embeddings',
  'RAG',
  'agents',
  'evals',
  'production',
]

export default function ConceptCard({ card, onReviewed }: ConceptCardProps) {
  const [flipped, setFlipped] = useState(false)
  const [reviewed, setReviewed] = useState(false)

  const [reviewCard, { loading }] = useMutation(REVIEW_LEARNING_CARD)

  const handleFlip = useCallback(() => {
    setFlipped(f => !f)
  }, [])

  const handleReview = useCallback(async (result: 'knew_it' | 'show_again') => {
    if (reviewed || loading) return
    try {
      await reviewCard({ variables: { cardId: card.id, result } })
      setReviewed(true)
      onReviewed?.()
    } catch {
      // Silent — card review is best-effort
    }
  }, [reviewCard, card.id, reviewed, loading, onReviewed])

  const currentNodeIndex = PATHWAY_NODES.findIndex(
    n => n.toLowerCase() === card.pathwayNode.toLowerCase()
  )

  return (
    <div className="concept-card">
      <div className={`concept-card__inner${flipped ? ' concept-card__inner--flipped' : ''}`}>
        {/* ── Front face ── */}
        <div className="concept-card__face concept-card__front" onClick={handleFlip}>
          <span className="concept-card__pathway">
            {card.pathwayNode}
          </span>
          <div className="concept-card__term">{card.term}</div>
          <div className="concept-card__flip-hint">
            Tap to reveal explanation
          </div>
        </div>

        {/* ── Back face ── */}
        <div className="concept-card__face concept-card__back">
          <div className="concept-card__explanation">{card.explanation}</div>

          {card.usageLine && (
            <div className="concept-card__usage">
              Where you'd use it: {card.usageLine}
            </div>
          )}

          {card.codeExample && (
            <pre className="concept-card__code">{card.codeExample}</pre>
          )}

          {!reviewed ? (
            <div className="concept-card__actions">
              <button
                className="concept-card__btn concept-card__btn--knew"
                onClick={() => handleReview('knew_it')}
                disabled={loading}
              >
                Knew it
              </button>
              <button
                className="concept-card__btn"
                onClick={() => handleReview('show_again')}
                disabled={loading}
              >
                Show again
              </button>
              <button
                className="concept-card__btn"
                onClick={handleFlip}
                style={{ flex: 0, padding: '7px 10px' }}
              >
                ↩
              </button>
            </div>
          ) : (
            <div className="concept-card__actions">
              <div style={{ flex: 1, fontSize: '12px', color: 'var(--accent)', fontFamily: 'var(--mono-font)' }}>
                {card.mastered ? 'Mastered!' : 'Logged — see you next time'}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── Pathway strip (always visible below card) ── */}
      <div className="concept-card__pathway-strip">
        {PATHWAY_NODES.map((node, idx) => (
          <React.Fragment key={node}>
            {idx > 0 && <div className="concept-card__path-connector" />}
            <div className="concept-card__path-node">
              <div className={`concept-card__path-dot${idx === currentNodeIndex ? ' concept-card__path-dot--active' : ''}`} />
              <span className={`concept-card__path-label${idx === currentNodeIndex ? ' concept-card__path-label--active' : ''}`}>
                {node}
              </span>
            </div>
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}
