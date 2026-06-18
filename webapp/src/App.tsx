import { useState } from 'react'
import './App.css'

interface Challenge {
  id: number
  name: string
  emoji: string
  streak: number
  longestStreak: number
  successCount: number
  totalDays: number
  progress: number
  days: DayRecord[]
}

interface DayRecord {
  date: string
  status: 'success' | 'fail' | 'skip' | 'empty'
}

// Mock data
const CHALLENGES: Challenge[] = [
  {
    id: 1,
    name: 'Питание по КБЖ',
    emoji: '🤪',
    streak: 11,
    longestStreak: 11,
    successCount: 10,
    totalDays: 11,
    progress: 91,
    days: Array.from({ length: 11 }, (_, i) => ({
      date: new Date(Date.now() - (10 - i) * 86400000).toISOString().split('T')[0],
      status: i === 10 ? 'fail' : 'success' as const,
    })),
  },
  {
    id: 2,
    name: 'Алко-пауза',
    emoji: '🍷',
    streak: 4,
    longestStreak: 7,
    successCount: 14,
    totalDays: 19,
    progress: 73,
    days: Array.from({ length: 19 }, (_, i) => {
      const statuses = ['success', 'success', 'success', 'success', 'fail', 'success', 'skip', 'success', 'success', 'success', 'success', 'success', 'success', 'success', 'success', 'success', 'success', 'success', 'success']
      return {
        date: new Date(Date.now() - (18 - i) * 86400000).toISOString().split('T')[0],
        status: statuses[i] as any,
      }
    }),
  },
  {
    id: 3,
    name: 'ПП без РПП',
    emoji: '🍔',
    streak: 9,
    longestStreak: 9,
    successCount: 16,
    totalDays: 19,
    progress: 84,
    days: Array.from({ length: 19 }, (_, i) => ({
      date: new Date(Date.now() - (18 - i) * 86400000).toISOString().split('T')[0],
      status: 'success' as const,
    })),
  },
]

const DAYS_OF_WEEK = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

function getDayStatus(status: string) {
  switch (status) {
    case 'success': return '✅'
    case 'fail': return '😔'
    case 'skip': return '⏭'
    default: return '⬜'
  }
}

function DashboardTab() {
  const totalXp = 1250
  const completedChallenges = 5
  const freezeCount = 3

  return (
    <div className="tab-content">
      {/* Stats Summary */}
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-value">⭐</div>
          <div className="stat-label">{totalXp} XP</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">🏆</div>
          <div className="stat-label">{completedChallenges} завершено</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">🧊</div>
          <div className="stat-label">{freezeCount} заморозок</div>
        </div>
      </div>

      {/* Challenges */}
      <div className="challenges-list">
        {CHALLENGES.map(challenge => (
          <div key={challenge.id} className="challenge-card">
            <div className="challenge-header">
              <div className="challenge-name">
                <span className="emoji">{challenge.emoji}</span>
                <span>{challenge.name}</span>
              </div>
            </div>

            {/* Streak Info */}
            <div className="challenge-streak">
              <span className="streak-value">🔥 {challenge.streak} дней подряд</span>
              {challenge.longestStreak > challenge.streak && (
                <span className="record">рекорд {challenge.longestStreak}</span>
              )}
            </div>

            {/* Progress */}
            <div className="challenge-progress">
              <div className="progress-bar">
                <div className="progress-fill" style={{ width: `${challenge.progress}%` }}></div>
              </div>
              <div className="progress-text">
                из {challenge.totalDays} дней: {challenge.successCount} выполнено
              </div>
            </div>

            {/* 7-day Heatmap */}
            <div className="heatmap">
              {challenge.days.slice(-7).map((day, idx) => (
                <div key={idx} className="heatmap-cell" title={day.date}>
                  {getDayStatus(day.status)}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function CalendarTab() {
  const [selectedChallenge, setSelectedChallenge] = useState(0)
  const [currentDate, setCurrentDate] = useState(new Date(2026, 5, 18)) // June 2026
  const challenge = CHALLENGES[selectedChallenge]

  const year = currentDate.getFullYear()
  const month = currentDate.getMonth()
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()

  const days = Array(firstDay === 0 ? 6 : firstDay - 1).fill(null)
  for (let i = 1; i <= daysInMonth; i++) {
    days.push(i)
  }

  const getStatusForDay = (day: number) => {
    if (!day) return 'empty'
    const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`
    const dayRecord = challenge.days.find(d => d.date === dateStr)
    return dayRecord?.status || 'empty'
  }

  const monthNames = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']

  const prevMonth = () => setCurrentDate(new Date(year, month - 1, 1))
  const nextMonth = () => setCurrentDate(new Date(year, month + 1, 1))

  return (
    <div className="tab-content">
      <div className="calendar-header">
        <select
          value={selectedChallenge}
          onChange={(e) => setSelectedChallenge(Number(e.target.value))}
          className="challenge-select"
        >
          {CHALLENGES.map((c, i) => (
            <option key={i} value={i}>
              {c.emoji} {c.name}
            </option>
          ))}
        </select>
      </div>

      {/* Month Navigation */}
      <div className="month-nav">
        <button onClick={prevMonth} className="nav-btn">◀</button>
        <h2 className="month-title">{monthNames[month]} {year}</h2>
        <button onClick={nextMonth} className="nav-btn">▶</button>
      </div>

      {/* Calendar Grid */}
      <div className="calendar-grid">
        {/* Day headers */}
        {DAYS_OF_WEEK.map(day => (
          <div key={day} className="day-header">{day}</div>
        ))}

        {/* Days */}
        {days.map((day, idx) => (
          <div
            key={idx}
            className={`calendar-cell ${day ? 'active' : ''}`}
            title={day ? `${day} ${monthNames[month]}` : ''}
          >
            {day ? getDayStatus(getStatusForDay(day as number)) : ''}
          </div>
        ))}
      </div>

      {/* Stats */}
      <div className="calendar-stats">
        <span>✅ {challenge.successCount}</span>
        <span>😔 {challenge.totalDays - challenge.successCount - 2}</span>
        <span>⏭ 2</span>
      </div>
    </div>
  )
}

export default function App() {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'calendar'>('dashboard')

  return (
    <div className="app-container">
      {/* Header */}
      <header className="app-header">
        <h1>just_do_it</h1>
        <span className="subtitle">Ваш прогресс</span>
      </header>

      {/* Content */}
      <main className="app-main">
        {activeTab === 'dashboard' && <DashboardTab />}
        {activeTab === 'calendar' && <CalendarTab />}
      </main>

      {/* Tab Navigation */}
      <nav className="tab-nav">
        <button
          className={`tab-btn ${activeTab === 'dashboard' ? 'active' : ''}`}
          onClick={() => setActiveTab('dashboard')}
        >
          📊 Статистика
        </button>
        <button
          className={`tab-btn ${activeTab === 'calendar' ? 'active' : ''}`}
          onClick={() => setActiveTab('calendar')}
        >
          📅 Календарь
        </button>
      </nav>
    </div>
  )
}
