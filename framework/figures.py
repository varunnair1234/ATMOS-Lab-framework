"""
Dash-independent Plotly figure builders for iMet-X4 data.

Extracted from dashboard.py so this logic can be reused by any consumer
(the Dash app, the FastAPI backend, tests, ...) without pulling in the
`dash`/Flask dependency chain.
"""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# How many readings to keep in memory for the live ring buffer
RING_BUFFER_SIZE = 3600  # ~1 hour at 1 Hz

# Columns we expect from the iMet-X4
REQUIRED_COLS = ["timestamp", "temperature", "humidity", "pressure"]
OPTIONAL_COLS = ["latitude", "longitude", "altitude", "wind_speed", "wind_direction"]

COLORS = {
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

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(family="system-ui, sans-serif", color=COLORS["text_primary"], size=12),
    margin=dict(l=48, r=16, t=32, b=40),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        bordercolor=COLORS["border"],
        borderwidth=1,
    ),
    xaxis=dict(
        gridcolor=COLORS["border"],
        zerolinecolor=COLORS["border"],
        showgrid=True,
    ),
    yaxis=dict(
        gridcolor=COLORS["border"],
        zerolinecolor=COLORS["border"],
        showgrid=True,
    ),
)


def build_figures(df: pd.DataFrame):
    """Build the four Plotly figures (T&H, pressure, profile, wind) from a DataFrame."""
    ts = df.get("timestamp", pd.Series(range(len(df))))

    # — Temperature & Humidity —
    fig_th = make_subplots(specs=[[{"secondary_y": True}]])
    fig_th.add_trace(go.Scatter(
        x=ts, y=df.get("temperature"),
        name="Temperature (°C)", line=dict(color=COLORS["temperature"], width=1.5),
    ), secondary_y=False)
    fig_th.add_trace(go.Scatter(
        x=ts, y=df.get("humidity"),
        name="Humidity (%)", line=dict(color=COLORS["humidity"], width=1.5, dash="dot"),
    ), secondary_y=True)
    fig_th.update_layout(**PLOTLY_LAYOUT, height=240)
    fig_th.update_yaxes(title_text="°C",  secondary_y=False,
                        gridcolor=COLORS["border"], zerolinecolor=COLORS["border"])
    fig_th.update_yaxes(title_text="%", secondary_y=True, showgrid=False)

    # — Pressure —
    fig_p = go.Figure(go.Scatter(
        x=ts, y=df.get("pressure"),
        fill="tozeroy", fillcolor="rgba(29,158,117,0.08)",
        line=dict(color=COLORS["pressure"], width=1.5),
        name="Pressure (hPa)",
    ))
    fig_p.update_layout(**PLOTLY_LAYOUT, height=240)

    # — Atmospheric profile (altitude vs temperature) —
    fig_prof = go.Figure(go.Scatter(
        x=df.get("temperature"), y=df.get("altitude"),
        mode="lines+markers",
        marker=dict(size=3, color=df.get("temperature"),
                    colorscale=[[0, COLORS["humidity"]], [1, COLORS["temperature"]]],
                    showscale=False),
        line=dict(color=COLORS["temperature"], width=1),
        name="T profile",
    ))
    fig_prof.update_layout(**PLOTLY_LAYOUT, height=240,
                           xaxis_title="Temperature (°C)",
                           yaxis_title="Altitude (m)")

    # — Wind (speed time-series + direction overlay) —
    fig_wind = make_subplots(specs=[[{"secondary_y": True}]])
    if "wind_speed" in df.columns:
        fig_wind.add_trace(go.Scatter(
            x=ts, y=df["wind_speed"],
            name="Speed (m/s)", line=dict(color=COLORS["wind_speed"], width=1.5),
        ), secondary_y=False)
    if "wind_direction" in df.columns:
        fig_wind.add_trace(go.Scatter(
            x=ts, y=df["wind_direction"],
            name="Direction (°)", mode="markers",
            marker=dict(size=3, color=COLORS["text_secondary"]),
        ), secondary_y=True)
    fig_wind.update_layout(**PLOTLY_LAYOUT, height=240)
    fig_wind.update_yaxes(title_text="m/s", secondary_y=False,
                          gridcolor=COLORS["border"], zerolinecolor=COLORS["border"])
    fig_wind.update_yaxes(title_text="°", secondary_y=True, range=[0, 360], showgrid=False)

    return fig_th, fig_p, fig_prof, fig_wind


def build_stat_values(last: dict):
    def fmt(key, decimals=1):
        v = last.get(key)
        return f"{v:.{decimals}f}" if v is not None else "—"
    return {
        "temperature": fmt("temperature"),
        "humidity":    fmt("humidity", 0),
        "pressure":    fmt("pressure", 1),
        "altitude":    fmt("altitude", 0),
        "wind_speed":  fmt("wind_speed"),
    }


def validate_df(df: pd.DataFrame):
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")
