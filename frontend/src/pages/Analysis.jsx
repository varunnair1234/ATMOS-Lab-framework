import { useEffect, useState } from 'react'
import { getAnalysisSummary, analysisImageUrl } from '../api'

const PROFILE_VARIABLES = ['temperature', 'humidity', 'pressure', 'wind_speed']

export default function Analysis() {
  const [summary, setSummary] = useState(null)
  const [error, setError] = useState(null)
  const [variable, setVariable] = useState('temperature')
  const [imgNonce, setImgNonce] = useState(0)

  useEffect(() => {
    getAnalysisSummary()
      .then(setSummary)
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    // bust the browser cache so images reflect the growing live buffer
    setImgNonce(Date.now())
  }, [])

  if (error) {
    return <div className="error-message">Failed to load analysis: {error}</div>
  }

  const columns = summary ? Object.keys(Object.values(summary)[0] || {}) : []

  return (
    <>
      {summary ? (
        <table className="summary-table">
          <thead>
            <tr>
              <th>Variable</th>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Object.entries(summary).map(([variableName, row]) => (
              <tr key={variableName}>
                <td>{variableName}</td>
                {columns.map((c) => (
                  <td key={c}>{row[c]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="loading">Loading summary…</div>
      )}

      <div className="grid">
        <div className="panel">
          <p className="panel-title">Distributions</p>
          <img src={`${analysisImageUrl('/api/analysis/histograms')}?t=${imgNonce}`} alt="Histograms" />
        </div>
        <div className="panel">
          <p className="panel-title">Correlation Matrix</p>
          <img src={`${analysisImageUrl('/api/analysis/correlation')}?t=${imgNonce}`} alt="Correlation heatmap" />
        </div>
        <div className="panel">
          <p className="panel-title">Wind Rose</p>
          <img src={`${analysisImageUrl('/api/analysis/wind-rose')}?t=${imgNonce}`} alt="Wind rose" />
        </div>
        <div className="panel">
          <p className="panel-title">Vertical Profile</p>
          <select
            className="variable-select"
            value={variable}
            onChange={(e) => setVariable(e.target.value)}
          >
            {PROFILE_VARIABLES.map((v) => (
              <option key={v} value={v}>{v}</option>
            ))}
          </select>
          <img
            src={`${analysisImageUrl('/api/analysis/vertical-profile')}?variable=${variable}&t=${imgNonce}`}
            alt={`Vertical profile of ${variable}`}
          />
        </div>
      </div>
    </>
  )
}
