import { ApolloClient, InMemoryCache, HttpLink } from '@apollo/client'
import { API_URL } from './config'

const client = new ApolloClient({
  link: new HttpLink({ uri: `${API_URL}/graphql` }),
  cache: new InMemoryCache(),
})

export default client
