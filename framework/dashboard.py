

import queue
import threading
from collections import deque
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import dash
from dash import dcc, html, Input, Output, callback

# How many readings to keep in memory for the live ring buffer
RING_BUFFER_SIZE = 3600  # ~1 hour at 1 Hz

# Columns we expect from the iMet-X4
REQUIRED_COLS = ["timestamp", "temperature", "humidity", "pressure"]
OPTIONAL_COLS = ["latitude", "longitude", "altitude", "wind_speed", "wind_direction"]

_COLORS = {
    "temperature":     "#E8593C",
    "humidity":        "#3B8BD4",
    "pressure":        "#1D9E75",
    "wind_speed":      "#BA7517",
    "altitude":        "#7F77DD",
    "background":      "#0f1117",
    "panel":           "#1a1d27",
    "border":          "#2a2d3a",
    "text_primary":    "#e8e6df",
    "text_secondary":  "#8a8880",
}

_PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="system-ui, sans-serif", color=_COLORS["text_primary"], size=12),
    margin=dict(l=48, r=16, t=32, b=40),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        bordercolor=_COLORS["border"],
        borderwidth=1,
    ),
    xaxis=dict(
        gridcolor=_COLORS["border"],
        zerolinecolor=_COLORS["border"],
        showgrid=True,
    ),
    yaxis=dict(
        gridcolor=_COLORS["border"],
        zerolinecolor=_COLORS["border"],
        showgrid=True,
    ),
)


def _make_stat_card(label: str, value: str, unit: str, color: str) -> html.Div:
    return html.Div(
        style={
            "background": _COLORS["panel"],
            "border": f"1px solid {_COLORS['border']}",
            "borderLeft": f"3px solid {color}",
            "borderRadius": "8px",
            "padding": "14px 18px",
            "flex": "1",
            "minWidth": "140px",
        },
        children=[
            html.P(label, style={"margin": "0 0 4px", "fontSize": "11px",
                                  "color": _COLORS["text_secondary"], "letterSpacing": "0.06em"}),
            html.Div(
                style={"display": "flex", "alignItems": "baseline", "gap": "4px"},
                children=[
                    html.Span(value, id=f"stat-{label.lower().replace(' ', '-')}",
                              style={"fontSize": "24px", "fontWeight": "600", "color": color}),
                    html.Span(unit, style={"fontSize": "13px", "color": _COLORS["text_secondary"]}),
                ],
            ),
        ],
    )


class Dashboard:
    """
    Real-time + post-flight dashboard for iMet-X4 data.

    Parameters
    ----------
    dataframe : pd.DataFrame, optional
        Pre-loaded DataFrame (post-flight mode). Must contain at minimum
        the columns in REQUIRED_COLS.
    data_queue : queue.Queue, optional
        Thread-safe queue fed by SerialReader (live mode). Each item
        should be a dict with keys matching REQUIRED_COLS.
    port : int
        Local port for the Dash server (default 8050).
    refresh_interval : int
        Dashboard refresh interval in milliseconds (default 1000).
    """

    def __init__(
        self,
        dataframe: Optional[pd.DataFrame] = None,
        data_queue: Optional[queue.Queue] = None,
        port: int = 8050,
        refresh_interval: int = 1000,
    ):
        if dataframe is None and data_queue is None:
            raise ValueError("Provide either dataframe= or data_queue=.")

        self.port = port
        self.refresh_interval = refresh_interval
        self._queue = data_queue
        self._live = data_queue is not None

        # Internal ring buffer for live mode
        self._buffer: deque = deque(maxlen=RING_BUFFER_SIZE)

        if dataframe is not None:
            self._validate_df(dataframe)
            for _, row in dataframe.iterrows():
                self._buffer.append(row.to_dict())

        self.app = dash.Dash(
            __name__,
            title="iMet-X4 Dashboard",
            meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
        )
        self._build_layout()
        self._register_callbacks()

    # ------------------------------------------------------------------ #
    #  Layout                                                              #
    # ------------------------------------------------------------------ #

    def _build_layout(self):
        self.app.layout = html.Div(
            style={"minHeight": "100vh", "background": _COLORS["background"],
                   "color": _COLORS["text_primary"], "fontFamily": "system-ui, sans-serif",
                   "padding": "20px 24px"},
            children=[
                # Header
                html.Div(
                    style={"display": "flex", "alignItems": "center",
                           "justifyContent": "space-between", "marginBottom": "20px"},
                    children=[
                        html.Div([
                            html.H1("iMet-X4 Monitor",
                                    style={"margin": 0, "fontSize": "20px", "fontWeight": "500"}),
                            html.P(
                                "● LIVE" if self._live else "Post-flight mode",
                                style={"margin": "2px 0 0", "fontSize": "12px",
                                       "color": "#1D9E75" if self._live else _COLORS["text_secondary"]},
                            ),
                        ]),
                        html.Div(id="last-update",
                                 style={"fontSize": "12px", "color": _COLORS["text_secondary"]}),
                    ],
                ),

                # Stat cards row
                html.Div(
                    id="stat-cards",
                    style={"display": "flex", "gap": "12px", "flexWrap": "wrap", "marginBottom": "20px"},
                    children=[
                        _make_stat_card("Temperature", "—", "°C", _COLORS["temperature"]),
                        _make_stat_card("Humidity",    "—", "%",  _COLORS["humidity"]),
                        _make_stat_card("Pressure",    "—", "hPa",_COLORS["pressure"]),
                        _make_stat_card("Altitude",    "—", "m",  _COLORS["altitude"]),
                        _make_stat_card("Wind Speed",  "—", "m/s",_COLORS["wind_speed"]),
                    ],
                ),

                # Main plots grid
                html.Div(
                    style={"display": "grid",
                           "gridTemplateColumns": "1fr 1fr",
                           "gridTemplateRows": "auto auto",
                           "gap": "16px"},
                    children=[
                        self._panel("Atmospheric Profile", dcc.Graph(id="graph-profile",   config={"displayModeBar": False})),
                        self._panel("Wind",                dcc.Graph(id="graph-wind",       config={"displayModeBar": False})),
                        self._panel("Temperature & Humidity", dcc.Graph(id="graph-th",      config={"displayModeBar": False})),
                        self._panel("Pressure",            dcc.Graph(id="graph-pressure",   config={"displayModeBar": False})),
                    ],
                ),

                # Refresh trigger
                dcc.Interval(
                    id="interval",
                    interval=self.refresh_interval,
                    n_intervals=0,
                    disabled=not self._live,
                ),
            ],
        )

    @staticmethod
    def _panel(title: str, child) -> html.Div:
        return html.Div(
            style={
                "background": _COLORS["panel"],
                "border": f"1px solid {_COLORS['border']}",
                "borderRadius": "10px",
                "padding": "16px",
            },
            children=[
                html.P(title, style={"margin": "0 0 10px", "fontSize": "13px",
                                     "color": _COLORS["text_secondary"], "fontWeight": "500"}),
                child,
            ],
        )

    # ------------------------------------------------------------------ #
    #  Callbacks                                                           #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_figures(df: pd.DataFrame):
        """Build the four Plotly figures (T&H, pressure, profile, wind) from a DataFrame."""
        ts = df.get("timestamp", pd.Series(range(len(df))))

        # — Temperature & Humidity —
        fig_th = make_subplots(specs=[[{"secondary_y": True}]])
        fig_th.add_trace(go.Scatter(
            x=ts, y=df.get("temperature"),
            name="Temperature (°C)", line=dict(color=_COLORS["temperature"], width=1.5),
        ), secondary_y=False)
        fig_th.add_trace(go.Scatter(
            x=ts, y=df.get("humidity"),
            name="Humidity (%)", line=dict(color=_COLORS["humidity"], width=1.5, dash="dot"),
        ), secondary_y=True)
        fig_th.update_layout(**_PLOTLY_LAYOUT, height=240)
        fig_th.update_yaxes(title_text="°C",  secondary_y=False,
                            gridcolor=_COLORS["border"], zerolinecolor=_COLORS["border"])
        fig_th.update_yaxes(title_text="%", secondary_y=True, showgrid=False)

        # — Pressure —
        fig_p = go.Figure(go.Scatter(
            x=ts, y=df.get("pressure"),
            fill="tozeroy", fillcolor="rgba(29,158,117,0.08)",
            line=dict(color=_COLORS["pressure"], width=1.5),
            name="Pressure (hPa)",
        ))
        fig_p.update_layout(**_PLOTLY_LAYOUT, height=240)

        # — Atmospheric profile (altitude vs temperature) —
        fig_prof = go.Figure(go.Scatter(
            x=df.get("temperature"), y=df.get("altitude"),
            mode="lines+markers",
            marker=dict(size=3, color=df.get("temperature"),
                        colorscale=[[0, _COLORS["humidity"]], [1, _COLORS["temperature"]]],
                        showscale=False),
            line=dict(color=_COLORS["temperature"], width=1),
            name="T profile",
        ))
        fig_prof.update_layout(**_PLOTLY_LAYOUT, height=240,
                               xaxis_title="Temperature (°C)",
                               yaxis_title="Altitude (m)")

        # — Wind (speed time-series + direction overlay) —
        fig_wind = make_subplots(specs=[[{"secondary_y": True}]])
        if "wind_speed" in df.columns:
            fig_wind.add_trace(go.Scatter(
                x=ts, y=df["wind_speed"],
                name="Speed (m/s)", line=dict(color=_COLORS["wind_speed"], width=1.5),
            ), secondary_y=False)
        if "wind_direction" in df.columns:
            fig_wind.add_trace(go.Scatter(
                x=ts, y=df["wind_direction"],
                name="Direction (°)", mode="markers",
                marker=dict(size=3, color=_COLORS["text_secondary"]),
            ), secondary_y=True)
        fig_wind.update_layout(**_PLOTLY_LAYOUT, height=240)
        fig_wind.update_yaxes(title_text="m/s", secondary_y=False,
                              gridcolor=_COLORS["border"], zerolinecolor=_COLORS["border"])
        fig_wind.update_yaxes(title_text="°", secondary_y=True, range=[0, 360], showgrid=False)

        return fig_th, fig_p, fig_prof, fig_wind

    @staticmethod
    def _build_stat_values(last: dict):
        def fmt(key, decimals=1):
            v = last.get(key)
            return f"{v:.{decimals}f}" if v is not None else "—"
        return fmt("temperature"), fmt("humidity", 0), fmt("pressure", 1), \
               fmt("altitude", 0), fmt("wind_speed")

    def _register_callbacks(self):

        @self.app.callback(
            Output("graph-th",       "figure"),
            Output("graph-pressure", "figure"),
            Output("graph-profile",  "figure"),
            Output("graph-wind",     "figure"),
            Output("last-update",    "children"),
            Input("interval",        "n_intervals"),
        )
        def refresh(_n):
            # Pull new items from the queue into the buffer (live mode)
            if self._queue is not None:
                while True:
                    try:
                        item = self._queue.get_nowait()
                        self._buffer.append(item)
                    except queue.Empty:
                        break

            if not self._buffer:
                empty = go.Figure()
                empty.update_layout(**_PLOTLY_LAYOUT)
                return empty, empty, empty, empty, "No data yet"

            df = pd.DataFrame(list(self._buffer))
            fig_th, fig_p, fig_prof, fig_wind = self._build_figures(df)

            last = df.iloc[-1]
            timestamp_str = str(last.get("timestamp", ""))
            return fig_th, fig_p, fig_prof, fig_wind, f"Last update: {timestamp_str}"

        # Live stat card values
        @self.app.callback(
            Output("stat-temperature", "children"),
            Output("stat-humidity",    "children"),
            Output("stat-pressure",    "children"),
            Output("stat-altitude",    "children"),
            Output("stat-wind-speed",  "children"),
            Input("interval", "n_intervals"),
        )
        def update_stats(_n):
            if not self._buffer:
                return "—", "—", "—", "—", "—"
            return self._build_stat_values(self._buffer[-1])

    # ------------------------------------------------------------------ #
    #  Run                                                                 #
    # ------------------------------------------------------------------ #

    def run(self, debug: bool = False, open_browser: bool = True):
        """Start the Dash server. Blocking call."""
        if open_browser:
            import webbrowser, threading
            threading.Timer(1.5, lambda: webbrowser.open_new(f"http://localhost:{self.port}")).start()
        print(f"\n  iMet-X4 Dashboard running → http://localhost:{self.port}")
        print("  Press Ctrl+C to stop.\n")
        self.app.run(debug=debug, port=self.port, use_reloader=False, host="127.0.0.1")

    def export_static_html(self, path: str):
        """
        Render the current buffer as a single self-contained static HTML file
        (interactive Plotly charts, no live server). Used to publish a
        post-flight snapshot to GitHub Pages, since Pages can't run the
        live Dash server.
        """
        import plotly.io as pio

        if not self._buffer:
            raise ValueError("No data to export — buffer is empty.")

        df = pd.DataFrame(list(self._buffer))
        fig_th, fig_p, fig_prof, fig_wind = self._build_figures(df)
        stat_values = self._build_stat_values(df.iloc[-1].to_dict())
        stat_labels = [
            ("Temperature", "°C"), ("Humidity", "%"), ("Pressure", "hPa"),
            ("Altitude", "m"), ("Wind Speed", "m/s"),
        ]
        stat_colors = [_COLORS["temperature"], _COLORS["humidity"], _COLORS["pressure"],
                       _COLORS["altitude"], _COLORS["wind_speed"]]

        graphs_html = [
            pio.to_html(fig, full_html=False, include_plotlyjs="cdn", config={"displayModeBar": False})
            for fig in (fig_th, fig_p, fig_prof, fig_wind)
        ]

        stat_cards_html = "".join(
            f'''<div class="stat-card" style="border-left-color:{color}">
                    <p class="stat-label">{label}</p>
                    <div><span class="stat-value" style="color:{color}">{value}</span>
                    <span class="stat-unit">{unit}</span></div>
                </div>'''
            for (label, unit), value, color in zip(stat_labels, stat_values, stat_colors)
        )

        timestamp_str = str(df.iloc[-1].get("timestamp", ""))

        html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>iMet-X4 Dashboard (snapshot)</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body {{ min-height: 100vh; background: {_COLORS['background']}; color: {_COLORS['text_primary']};
         font-family: system-ui, sans-serif; padding: 20px 24px; margin: 0; }}
  .header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }}
  .header h1 {{ margin: 0; font-size: 20px; font-weight: 500; }}
  .header p {{ margin: 2px 0 0; font-size: 12px; color: {_COLORS['text_secondary']}; }}
  .stat-cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
  .stat-card {{ background: {_COLORS['panel']}; border: 1px solid {_COLORS['border']};
               border-left: 3px solid; border-radius: 8px; padding: 14px 18px; flex: 1; min-width: 140px; }}
  .stat-label {{ margin: 0 0 4px; font-size: 11px; color: {_COLORS['text_secondary']}; letter-spacing: 0.06em; }}
  .stat-value {{ font-size: 24px; font-weight: 600; }}
  .stat-unit {{ font-size: 13px; color: {_COLORS['text_secondary']}; margin-left: 4px; }}
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  .panel {{ background: {_COLORS['panel']}; border: 1px solid {_COLORS['border']}; border-radius: 10px; padding: 16px; }}
  .panel p {{ margin: 0 0 10px; font-size: 13px; color: {_COLORS['text_secondary']}; font-weight: 500; }}
  @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
  <div class="header">
    <div>
      <h1>iMet-X4 Monitor</h1>
      <p>Post-flight snapshot &middot; last update: {timestamp_str}</p>
    </div>
  </div>
  <div class="stat-cards">{stat_cards_html}</div>
  <div class="grid">
    <div class="panel"><p>Temperature &amp; Humidity</p>{graphs_html[0]}</div>
    <div class="panel"><p>Pressure</p>{graphs_html[1]}</div>
    <div class="panel"><p>Atmospheric Profile</p>{graphs_html[2]}</div>
    <div class="panel"><p>Wind</p>{graphs_html[3]}</div>
  </div>
</body>
</html>
"""
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(html, encoding="utf-8")
        print(f"Static dashboard written -> {out_path.resolve()}")

    @staticmethod
    def _validate_df(df: pd.DataFrame):
        missing = [c for c in REQUIRED_COLS if c not in df.columns]
        if missing:
            raise ValueError(f"DataFrame missing required columns: {missing}")