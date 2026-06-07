// API base URL — set VITE_API_URL in production (Railway env var)
// Falls back to localhost for local Docker development
export const API_URL = import.meta.env.VITE_API_URL?.replace(/\/$/, '') || 'http://localhost:8000'
