import { gql } from '@apollo/client'

export const SEND_MESSAGE = gql`
  mutation Send($content: String!, $clearedAt: String) {
    send(content: $content, clearedAt: $clearedAt) {
      answer
      type
      action
      payload
      sources {
        id
        content
        category
        score
        createdAt
      }
    }
  }
`

export const GET_MESSAGES = gql`
  query GetMessages($limit: Int, $cursor: String) {
    messages(limit: $limit, cursor: $cursor) {
      messages {
        id
        content
        type
        role
        payload
        createdAt
      }
      pageInfo {
        hasNextPage
        cursor
      }
    }
  }
`

export const GET_TASKS = gql`
  query GetTasks($date: String) {
    tasks(date: $date) {
      pending {
        id
        content
        status
        createdDate
        carriedOver
      }
      completed {
        id
        content
        status
        completedDate
        carriedOver
      }
      date
    }
  }
`

export const GET_CATEGORIES = gql`
  query GetCategories {
    categories {
      name
      icon
      count
    }
  }
`

export const COMPLETE_TASK = gql`
  mutation CompleteTask($taskId: String!) {
    completeTask(taskId: $taskId) {
      id
      content
      status
      completedDate
      carriedOver
    }
  }
`

export const ADD_TASK = gql`
  mutation AddTask($content: String!, $date: String) {
    addTask(content: $content, date: $date) {
      id
      content
      status
      createdDate
      carriedOver
    }
  }
`

export const EDIT_TASK = gql`
  mutation EditTask($taskId: String!, $content: String!) {
    editTask(taskId: $taskId, content: $content) {
      id
      content
      status
      createdDate
      completedDate
      carriedOver
    }
  }
`

export const DELETE_TASK = gql`
  mutation DeleteTask($taskId: String!) {
    deleteTask(taskId: $taskId)
  }
`

export const GET_MEMORIES = gql`
  query GetMemories($category: String, $limit: Int, $cursor: String) {
    memories(category: $category, limit: $limit, cursor: $cursor) {
      memories {
        id
        content
        category
        createdAt
      }
      pageInfo {
        hasNextPage
        cursor
      }
    }
  }
`
