import { useState } from 'react'
import Dashboard from './pages/Dashboard'
import Analysis from './pages/Analysis'

export default function App() {
  const [tab, setTab] = useState('dashboard')
  const [lastUpdate, setLastUpdate] = useState(null)

  return (
    <>
      <div className="header">
        <div>
          <h1>iMet-X4 Monitor</h1>
          <p className="live-indicator">● LIVE</p>
        </div>
        <div className="header-right">
          <div className="tabs">
            <button
              className={tab === 'dashboard' ? 'active' : ''}
              onClick={() => setTab('dashboard')}
            >
              Dashboard
            </button>
            <button
              className={tab === 'analysis' ? 'active' : ''}
              onClick={() => setTab('analysis')}
            >
              Analysis
            </button>
          </div>
          {tab === 'dashboard' && (
            <div className="last-update">
              {lastUpdate ? `Last update: ${lastUpdate}` : ''}
            </div>
          )}
        </div>
      </div>

      {tab === 'dashboard' ? (
        <Dashboard onLastUpdate={setLastUpdate} />
      ) : (
        <Analysis />
      )}
    </>
  )
}
