import { useEffect, useRef, useState } from 'react'
import { getDashboardSnapshot } from '../api'
import StatCard from '../components/StatCard'
import PlotPanel from '../components/PlotPanel'
import InsightsPanel from '../components/InsightsPanel'

const REFRESH_MS = 1000

const STAT_DEFS = [
  { key: 'temperature', label: 'Temperature', unit: '°C', color: 'var(--temperature)' },
  { key: 'humidity', label: 'Humidity', unit: '%', color: 'var(--humidity)' },
  { key: 'pressure', label: 'Pressure', unit: 'hPa', color: 'var(--pressure)' },
  { key: 'altitude', label: 'Altitude', unit: 'm', color: 'var(--altitude)' },
  { key: 'wind_speed', label: 'Wind Speed', unit: 'm/s', color: 'var(--wind-speed)' },
]

export default function Dashboard({ onLastUpdate }) {
  const [snapshot, setSnapshot] = useState(null)
  const [error, setError] = useState(null)
  const pollRef = useRef(null)

  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const data = await getDashboardSnapshot()
        if (!cancelled) {
          setSnapshot(data)
          setError(null)
          onLastUpdate?.(data.last_update)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }

    poll()
    pollRef.current = setInterval(poll, REFRESH_MS)
    return () => {
      cancelled = true
      clearInterval(pollRef.current)
    }
  }, [onLastUpdate])

  if (error) {
    return <div className="error-message">Failed to load dashboard: {error}</div>
  }

  if (!snapshot) {
    return <div className="loading">Loading…</div>
  }

  const stats = snapshot.stats || {}
  const figures = snapshot.figures || {}

  return (
    <>
      <div className="stat-cards">
        {STAT_DEFS.map((s) => (
          <StatCard
            key={s.key}
            label={s.label}
            value={stats[s.key] ?? '—'}
            unit={s.unit}
            color={s.color}
          />
        ))}
      </div>
      <div className="grid">
        <PlotPanel title="Atmospheric Profile" figure={figures.profile} />
        <PlotPanel title="Wind" figure={figures.wind} />
        <PlotPanel title="Temperature & Humidity" figure={figures.temperature_humidity} />
        <PlotPanel title="Pressure" figure={figures.pressure} />
      </div>
      <InsightsPanel />
    </>
  )
}
