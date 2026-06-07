import React from 'react'

interface Category {
  name: string
  icon: string
  count: number
}

interface CategoryCardProps {
  payload: { categories: Category[] }
  onDrill: (category: string) => void
}

export default function CategoryCard({ payload, onDrill }: CategoryCardProps) {
  const categories = payload.categories ?? []

  return (
    <div className="category-card">
      <div className="category-card__header">KNOWLEDGE BASE</div>
      <div className="category-card__grid">
        {categories.map((cat) => (
          <button
            key={cat.name}
            className="category-card__item"
            onClick={() => onDrill(cat.name)}
          >
            <span className="category-card__icon">{cat.icon}</span>
            <span className="category-card__name">{cat.name}</span>
            <span className="category-card__count">{cat.count}</span>
          </button>
        ))}
        {categories.length === 0 && (
          <div style={{ padding: '12px', fontSize: '12px', color: 'var(--text-faint)', fontFamily: 'var(--mono-font)', gridColumn: 'span 2' }}>
            No saved knowledge yet.
          </div>
        )}
      </div>
    </div>
  )
}
