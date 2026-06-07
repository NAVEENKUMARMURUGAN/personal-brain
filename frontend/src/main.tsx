import React from 'react'
import ReactDOM from 'react-dom/client'
import { ApolloProvider } from '@apollo/client'
import client from './apollo'
import App from './App'

// Set theme before first render to avoid flash
const savedTheme = (() => { try { return localStorage.getItem('pb-theme') } catch { return null } })()
document.documentElement.setAttribute('data-theme', savedTheme === 'light' ? 'light' : 'dark')

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ApolloProvider client={client}>
      <App />
    </ApolloProvider>
  </React.StrictMode>
)
