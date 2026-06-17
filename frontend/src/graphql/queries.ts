import { gql } from '@apollo/client'

export const SEND_MESSAGE = gql`
  mutation Send($content: String!, $clearedAt: String, $attachments: [AttachmentInput]) {
    send(content: $content, clearedAt: $clearedAt, attachments: $attachments) {
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
        taskType
        reminderTime
        isRecurring
        recurrence
        recurrenceEndDate
        parentTaskId
      }
      completed {
        id
        content
        status
        completedDate
        carriedOver
        taskType
        reminderTime
        isRecurring
        recurrence
        recurrenceEndDate
        parentTaskId
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
      taskType
      reminderTime
      isRecurring
      recurrence
      recurrenceEndDate
      parentTaskId
    }
  }
`

export const ADD_TASK = gql`
  mutation AddTask($content: String!, $date: String, $recurrence: String, $recurrenceEndDate: String) {
    addTask(content: $content, date: $date, recurrence: $recurrence, recurrenceEndDate: $recurrenceEndDate) {
      id
      content
      status
      createdDate
      carriedOver
      taskType
      reminderTime
      isRecurring
      recurrence
      recurrenceEndDate
      parentTaskId
    }
  }
`

export const ADD_REMINDER = gql`
  mutation AddReminder($content: String!, $date: String!, $time: String!, $recurrence: String, $recurrenceEndDate: String) {
    addReminder(content: $content, date: $date, time: $time, recurrence: $recurrence, recurrenceEndDate: $recurrenceEndDate) {
      id
      content
      status
      createdDate
      carriedOver
      taskType
      reminderTime
      isRecurring
      recurrence
      recurrenceEndDate
      parentTaskId
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
      taskType
      reminderTime
      isRecurring
      recurrence
      recurrenceEndDate
      parentTaskId
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
          videoId
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
          videoId
          reaction
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

export const REACT_FEED_ITEM = gql`
  mutation ReactFeedItem($feedItemId: ID!, $reaction: FeedReaction!) {
    reactFeedItem(feedItemId: $feedItemId, reaction: $reaction) {
      feedItemId
      reaction
    }
  }
`

export const REFRESH_LEARNING_PICKS = gql`
  mutation RefreshLearningPicks {
    refreshLearningPicks {
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
        videoId
        reaction
      }
    }
  }
`

// ── Topic Explorer ─────────────────────────────────────────────

export const EXPLORE_TOPIC = gql`
  mutation ExploreTopic($topic: String!, $regenerate: Boolean) {
    exploreTopic(topic: $topic, regenerate: $regenerate) {
      id
      topic
      topicSlug
      overviewJson
      engineerJson
      useCasesJson
      sampleImplementationJson
      mindmapMermaid
      flashcardsJson
      quizJson
      relatedMemoriesJson
      createdAt
      cached
    }
  }
`

export const SURPRISE_ME = gql`
  mutation SurpriseMe {
    surpriseMe
  }
`

export const SAVE_EXPLORATION_SECTION = gql`
  mutation SaveExplorationSection($topic: String!, $content: String!, $category: String!) {
    saveExplorationSection(topic: $topic, content: $content, category: $category) {
      id
      content
      category
      createdAt
    }
  }
`

export const LIST_EXPLORATIONS = gql`
  query ListExplorations($limit: Int) {
    listExplorations(limit: $limit) {
      id
      topic
      topicSlug
      createdAt
    }
  }
`

export const DELETE_EXPLORATION = gql`
  mutation DeleteExploration($topicSlug: String!) {
    deleteExploration(topicSlug: $topicSlug)
  }
`
