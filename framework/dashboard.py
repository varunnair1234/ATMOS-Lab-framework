import queue
from collections import deque
from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import dash
from dash import dcc, html, Input, Output

from framework.insights import get_insights

# How many readings to keep in memory for the live ring buffer
RING_BUFFER_SIZE = 3600  # ~1 hour at 1 Hz

# How many raw serial lines to keep in the debug log panel (see raw_log_queue)
RAW_LOG_BUFFER_SIZE = 200

# Columns we expect from the iMet-X4 (or, in a fused session, from
# framework.multi_sensor.SensorHub combining it with an ATMOS 22 and/or a
# Trisonica Mini -- see framework.atmos22_reader / framework.trisonica_reader)
REQUIRED_COLS = ["timestamp", "temperature", "humidity", "pressure"]
OPTIONAL_COLS = ["latitude", "longitude", "altitude", "wind_speed", "wind_direction", "wind_gust"]

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
    "waiting":         "#8a8880",
    "reconnecting":    "#BA7517",
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
        Thread-safe queue fed by IMetX4SerialReader (live mode). Each item
        should be a dict with keys matching REQUIRED_COLS.
    reader : IMetX4SerialReader, optional
        The reader instance feeding data_queue, if any. Purely optional —
        used only to show real connection status ("waiting for device",
        "reconnecting", the actual port/error) instead of just blank
        charts while there's no data yet. Live mode works without it, it
        just can't explain *why* nothing has arrived yet.
    port : int
        Local port for the Dash server (default 8050).
    refresh_interval : int
        Dashboard refresh interval in milliseconds (default 1000).
    insights_interval : int
        How often to refresh the Actionable Insights panel, in
        milliseconds (default 20000). Kept much slower than
        refresh_interval since each refresh is a real network call to an
        LLM provider — see framework.insights for caching details.
    raw_log_queue : queue.Queue, optional
        Thread-safe queue of raw serial-line strings, e.g. fed by
        RadioLinkReader.start(on_raw_line=...) or any reader that exposes
        the unparsed text it received. When provided, a "Raw Serial"
        debug panel is shown with the most recent lines (including ones
        that failed to parse) — useful for confirming data is actually
        arriving over the wire before worrying about why it isn't
        showing up as parsed readings (e.g. "nothing coming back" from an
        ATMOS 22 leg: this panel shows whether ANY bytes are arriving at
        all, which narrows the problem to wiring/firmware vs. parsing).
    """

    def __init__(
        self,
        dataframe: Optional[pd.DataFrame] = None,
        data_queue: Optional[queue.Queue] = None,
        reader=None,
        port: int = 8050,
        refresh_interval: int = 1000,
        insights_interval: int = 20000,
        raw_log_queue: Optional[queue.Queue] = None,
    ):
        if dataframe is None and data_queue is None:
            raise ValueError("Provide either dataframe= or data_queue=.")

        self.port = port
        self.refresh_interval = refresh_interval
        self.insights_interval = insights_interval
        self._queue = data_queue
        self._reader = reader
        self._live = data_queue is not None

        # Internal ring buffer for live mode
        self._buffer: deque = deque(maxlen=RING_BUFFER_SIZE)

        # Raw serial debug log (optional — see raw_log_queue above)
        self._raw_log_queue = raw_log_queue
        self._raw_log_buffer: deque = deque(maxlen=RAW_LOG_BUFFER_SIZE)

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
    #  Connection status                                                   #
    # ------------------------------------------------------------------ #

    def _connection_status(self):
        """(text, color) for the header status line — reflects the real
        reader state when one was provided, rather than assuming "live" =
        "connected"."""
        if not self._live:
            return "Post-flight mode", _COLORS["text_secondary"]
        if self._reader is None:
            # Live mode but no reader reference to introspect — best we
            # can say is whether data has actually arrived.
            return ("● LIVE" if self._buffer else "○ Waiting for data…"), \
                   (_COLORS["pressure"] if self._buffer else _COLORS["waiting"])
        if self._reader.connected:
            if self._buffer:
                return "● LIVE", _COLORS["pressure"]
            return "● Connected — waiting for first reading…", _COLORS["pressure"]
        if self._buffer:
            # We had data before, link dropped — _reconnect() is retrying.
            return f"⚠ Reconnecting… ({self._reader.last_error})", _COLORS["reconnecting"]
        detail = self._reader.last_error or f"listening on {self._reader.port_name or 'auto-detected port'}"
        return f"○ Waiting for iMet-X4 ({detail})", _COLORS["waiting"]

    def _build_waiting_panel(self) -> html.Div:
        if self._reader is not None:
            port = self._reader.port_name or "auto-detecting a port…"
            detail = self._reader.last_error or f"Listening on {port} — no data yet."
        else:
            detail = "No data received yet."
        return html.Div(
            style={
                "background": _COLORS["panel"], "border": f"1px solid {_COLORS['border']}",
                "borderRadius": "10px", "padding": "60px 24px", "textAlign": "center",
                "gridColumn": "1 / -1",
            },
            children=[
                html.P("Waiting for iMet-X4…",
                       style={"margin": "0 0 8px", "fontSize": "16px", "fontWeight": "500"}),
                html.P(detail, style={"margin": 0, "fontSize": "13px", "color": _COLORS["text_secondary"]}),
            ],
        )

    # ------------------------------------------------------------------ #
    #  Layout                                                              #
    # ------------------------------------------------------------------ #

    def _build_layout(self):
        status_text, status_color = self._connection_status()

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
                                status_text, id="connection-status",
                                style={"margin": "2px 0 0", "fontSize": "12px", "color": status_color},
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

                # Main plots grid — swapped between a waiting message and the
                # four chart panels by the refresh callback below.
                html.Div(
                    id="content-area",
                    style={"display": "grid",
                           "gridTemplateColumns": "1fr 1fr",
                           "gridTemplateRows": "auto auto",
                           "gap": "16px", "marginBottom": "20px"},
                    children=[self._build_waiting_panel()] if not self._buffer
                              else self._build_chart_panels(pd.DataFrame(list(self._buffer))),
                ),

                # Actionable Insights
                self._panel(
                    "Actionable Insights",
                    html.Div(
                        id="insights-content",
                        children=[html.P(
                            "Waiting for data before generating insights…" if not self._buffer
                            else "Generating…",
                            style={"margin": 0, "fontSize": "13px", "color": _COLORS["text_secondary"]},
                        )],
                    ),
                ),

                # Raw Serial debug log — only shown when raw_log_queue was provided
                *([self._build_raw_log_panel()] if self._raw_log_queue is not None else []),

                # Refresh triggers
                dcc.Interval(
                    id="interval",
                    interval=self.refresh_interval,
                    n_intervals=0,
                    disabled=not self._live,
                ),
                dcc.Interval(
                    id="insights-interval",
                    interval=self.insights_interval,
                    n_intervals=0,
                    disabled=not self._live,
                ),
            ],
        )

    def _build_raw_log_panel(self) -> html.Div:
        """A scrolling monospace log of raw serial lines, newest at the
        bottom — same 'is anything arriving at all' debug view you'd get
        from Arduino IDE's Serial Monitor, but inside the dashboard so you
        don't need a second tool/window open during hardware bring-up."""
        return self._panel(
            "Raw Serial",
            html.Div(
                id="raw-log-content",
                children=[html.P("No serial data received yet.",
                                  style={"margin": 0, "fontSize": "12px", "color": _COLORS["text_secondary"]})],
                style={
                    "fontFamily": "ui-monospace, 'SF Mono', Menlo, Consolas, monospace",
                    "fontSize": "12px",
                    "color": _COLORS["text_primary"],
                    "maxHeight": "220px",
                    "overflowY": "auto",
                    "whiteSpace": "pre-wrap",
                    "wordBreak": "break-all",
                },
            ),
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

    def _build_chart_panels(self, df: pd.DataFrame) -> list:
        fig_th, fig_p, fig_prof, fig_wind = self._build_figures(df)
        graph_config = {"displayModeBar": False}
        return [
            self._panel("Atmospheric Profile", dcc.Graph(figure=fig_prof, config=graph_config)),
            self._panel("Wind",                dcc.Graph(figure=fig_wind, config=graph_config)),
            self._panel("Temperature & Humidity", dcc.Graph(figure=fig_th, config=graph_config)),
            self._panel("Pressure",            dcc.Graph(figure=fig_p, config=graph_config)),
        ]

    # ------------------------------------------------------------------ #
    #  Figures                                                             #
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

    @staticmethod
    def _render_insights(result: dict):
        if result["error"]:
            return html.P(result["error"],
                           style={"margin": 0, "fontSize": "13px", "color": _COLORS["text_secondary"]})
        bullets = [line.strip("-• \t") for line in result["insights"].splitlines() if line.strip()]
        return html.Div([
            html.Ul(
                [html.Li(b, style={"marginBottom": "4px"}) for b in bullets],
                style={"margin": "0 0 8px", "paddingLeft": "18px", "fontSize": "13px",
                       "color": _COLORS["text_primary"]},
            ),
            html.P(f"Generated by {result['model']} · {result['generated_at']}",
                   style={"margin": 0, "fontSize": "11px", "color": _COLORS["text_secondary"]}),
        ])

    # ------------------------------------------------------------------ #
    #  Callbacks                                                           #
    # ------------------------------------------------------------------ #

    def _register_callbacks(self):

        @self.app.callback(
            Output("content-area",     "children"),
            Output("connection-status", "children"),
            Output("connection-status", "style"),
            Output("last-update",      "children"),
            Output("stat-temperature", "children"),
            Output("stat-humidity",    "children"),
            Output("stat-pressure",    "children"),
            Output("stat-altitude",    "children"),
            Output("stat-wind-speed",  "children"),
            Input("interval",          "n_intervals"),
        )
        def refresh(_n):
            # Pull new items from the queue into the buffer (live mode)
            if self._queue is not None:
                while True:
                    try:
                        self._buffer.append(self._queue.get_nowait())
                    except queue.Empty:
                        break

            status_text, status_color = self._connection_status()
            status_style = {"margin": "2px 0 0", "fontSize": "12px", "color": status_color}

            if not self._buffer:
                return (
                    [self._build_waiting_panel()], status_text, status_style, "No data yet",
                    "—", "—", "—", "—", "—",
                )

            df = pd.DataFrame(list(self._buffer))
            content = self._build_chart_panels(df)

            last = df.iloc[-1]
            timestamp_str = str(last.get("timestamp", ""))
            stat_values = self._build_stat_values(last.to_dict())
            return (content, status_text, status_style, f"Last update: {timestamp_str}", *stat_values)

        @self.app.callback(
            Output("insights-content", "children"),
            Input("insights-interval", "n_intervals"),
        )
        def refresh_insights(_n):
            df = pd.DataFrame(list(self._buffer)) if self._buffer else pd.DataFrame()
            result = get_insights(df)
            return self._render_insights(result)

        if self._raw_log_queue is not None:
            @self.app.callback(
                Output("raw-log-content", "children"),
                Input("interval", "n_intervals"),
            )
            def refresh_raw_log(_n):
                while True:
                    try:
                        self._raw_log_buffer.append(self._raw_log_queue.get_nowait())
                    except queue.Empty:
                        break
                if not self._raw_log_buffer:
                    return [html.P("No serial data received yet.",
                                    style={"margin": 0, "fontSize": "12px", "color": _COLORS["text_secondary"]})]
                return [html.Div(line) for line in self._raw_log_buffer]

    # ------------------------------------------------------------------ #
    #  Run                                                                 #
    # ------------------------------------------------------------------ #

    def run(self, debug: bool = False, open_browser: bool = True):
        """Start the Dash server. Blocking call.

        threaded=True so a slow Actionable Insights request (a real
        network call to an LLM provider) doesn't stall the 1s chart
        refresh while it's in flight.
        """
        if open_browser:
            import webbrowser, threading
            threading.Timer(1.5, lambda: webbrowser.open_new(f"http://localhost:{self.port}")).start()
        print(f"\n  iMet-X4 Dashboard running → http://localhost:{self.port}")
        print("  Press Ctrl+C to stop.\n")
        self.app.run(debug=debug, port=self.port, use_reloader=False, host="127.0.0.1", threaded=True)

    def export_static_html(self, path: str):
        """
        Render the current buffer as a single self-contained static HTML file
        (interactive Plotly charts, no live server). Used to publish a
        post-flight snapshot to GitHub Pages, since Pages can't run the
        live Dash server. Includes a one-shot Actionable Insights call
        (if HF_TOKEN is set) baked into the static page.
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

        insights_result = get_insights(df)
        if insights_result["error"]:
            insights_html = f'<p class="insights-note">{insights_result["error"]}</p>'
        else:
            bullets = [line.strip("-• \t") for line in insights_result["insights"].splitlines() if line.strip()]
            items_html = "".join(f"<li>{b}</li>" for b in bullets)
            insights_html = (
                f'<ul class="insights-list">{items_html}</ul>'
                f'<p class="insights-note">Generated by {insights_result["model"]} '
                f'&middot; {insights_result["generated_at"]}</p>'
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
  .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }}
  .panel {{ background: {_COLORS['panel']}; border: 1px solid {_COLORS['border']}; border-radius: 10px; padding: 16px; }}
  .panel p {{ margin: 0 0 10px; font-size: 13px; color: {_COLORS['text_secondary']}; font-weight: 500; }}
  .insights-list {{ margin: 0 0 8px; padding-left: 18px; font-size: 13px; }}
  .insights-list li {{ margin-bottom: 4px; }}
  .insights-note {{ margin: 0; font-size: 11px; color: {_COLORS['text_secondary']}; }}
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
  <div class="panel"><p>Actionable Insights</p>{insights_html}</div>
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
