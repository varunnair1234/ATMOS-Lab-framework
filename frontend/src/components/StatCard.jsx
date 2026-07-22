export default function StatCard({ label, value, unit, color }) {
  return (
    <div className="stat-card" style={{ borderLeftColor: color }}>
      <p className="stat-label">{label}</p>
      <div className="stat-value-row">
        <span className="stat-value" style={{ color }}>{value}</span>
        <span className="stat-unit">{unit}</span>
      </div>
    </div>
  )
}
