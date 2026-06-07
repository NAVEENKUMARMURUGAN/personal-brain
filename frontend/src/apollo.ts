import { ApolloClient, InMemoryCache, HttpLink, ApolloLink } from '@apollo/client'
import { API_URL } from './config'

// Auth link — attaches Bearer token from localStorage to every request
const authLink = new ApolloLink((operation, forward) => {
  const token = localStorage.getItem('pb-token')
  operation.setContext(({ headers = {} }) => ({
    headers: {
      ...headers,
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  }))
  return forward(operation)
})

const httpLink = new HttpLink({ uri: `${API_URL}/graphql` })

const client = new ApolloClient({
  link: authLink.concat(httpLink),
  cache: new InMemoryCache(),
})

export default client
