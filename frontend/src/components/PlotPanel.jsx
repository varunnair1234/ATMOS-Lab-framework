import Plot from 'react-plotly.js'

export default function PlotPanel({ title, figure }) {
  return (
    <div className="panel">
      <p className="panel-title">{title}</p>
      {figure ? (
        <Plot
          data={figure.data}
          layout={{ ...figure.layout, autosize: true }}
          config={{ displayModeBar: false, responsive: true }}
          style={{ width: '100%', height: '240px' }}
          useResizeHandler
        />
      ) : (
        <div className="loading">No data yet</div>
      )}
    </div>
  )
}
