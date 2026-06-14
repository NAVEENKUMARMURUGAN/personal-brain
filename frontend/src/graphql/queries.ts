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

// ── Dashboard ──────────────────────────────────────────────────

export const GET_DASHBOARD = gql`
  query GetDashboard {
    dashboard {
      briefing {
        id
        text
        generatedAt
        cycleDate
      }
      weather {
        tempC
        feelsLikeC
        rainProbability
        condition
        windKmh
        uvIndex
        uvMax
        precipSumMm
        windMaxKmh
        sunrise
        sunset
        hourly {
          hour
          tempC
          rainMm
        }
      }
      transit {
        overallSeverity
        alerts {
          id
          line
          severity
          title
          detail
        }
      }
      specialToday {
        emoji
        label
        kind
        note
      }
      today {
        due {
          id
          content
          status
          createdDate
          carriedOver
        }
        overdue {
          id
          content
          createdDate
          daysOverdue
        }
        inbox {
          id
          content
          createdDate
          source
        }
      }
      news {
        refreshedAt
        items {
          id
          rank
          title
          sourceName
          tag
          summaryShort
          summaryDetail
          sourceUrl
          mediaType
          durationMin
          bookmarked
        }
      }
      learningPicks {
        refreshedAt
        items {
          id
          rank
          title
          sourceName
          tag
          summaryShort
          summaryDetail
          sourceUrl
          mediaType
          durationMin
          bookmarked
        }
      }
      localToday {
        alerts {
          id
          line
          severity
          title
          detail
        }
        advisories {
          title
          detail
          icon
          severity
        }
      }
      trendingRepos {
        fullName
        description
        language
        starsGained7d
        whyItMatters
      }
      conceptOfTheDay {
        id
        term
        explanation
        usageLine
        codeExample
        pathwayNode
        ease
        timesSeen
        mastered
      }
      weeklyStats {
        tasksDone7d
        articlesSaved
        cardsMastered
        dayStreak
      }
    }
  }
`

export const SAVE_TO_BRAIN = gql`
  mutation SaveToBrain($feedItemId: ID!) {
    saveToBrain(feedItemId: $feedItemId) {
      id
      feedItemId
      createdAt
    }
  }
`

export const REVIEW_LEARNING_CARD = gql`
  mutation ReviewLearningCard($cardId: ID!, $result: ReviewResult!) {
    reviewLearningCard(cardId: $cardId, result: $result) {
      id
      ease
      timesSeen
      mastered
    }
  }
`

export const TRIAGE_INBOX_ITEM = gql`
  mutation TriageInboxItem($itemId: ID!, $action: TriageAction!) {
    triageInboxItem(itemId: $itemId, action: $action) {
      id
      content
      status
    }
  }
`

export const REFRESH_BRIEFING = gql`
  mutation RefreshBriefing {
    refreshBriefing {
      id
      text
      generatedAt
      cycleDate
    }
  }
`
