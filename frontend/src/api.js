const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

async function getJson(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) {
    throw new Error(`${path} -> ${res.status}`)
  }
  return res.json()
}

export function getDashboardSnapshot() {
  return getJson('/api/dashboard/snapshot')
}

export function getAnalysisSummary() {
  return getJson('/api/analysis/summary')
}

export function analysisImageUrl(path) {
  return `${API_BASE}${path}`
}
