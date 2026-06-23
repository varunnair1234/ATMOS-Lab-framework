from __future__ import annotations
 
from pathlib import Path
from typing import Optional, Sequence
 
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
 
# ── Style constants ───────────────────────────────────────────────────────────
 
_PALETTE = {
    "temperature":  "#E8593C",
    "humidity":     "#3B8BD4",
    "pressure":     "#1D9E75",
    "altitude":     "#7F77DD",
    "wind_speed":   "#BA7517",
    "wind_dir":     "#888780",
    "grid":         "#2a2d3a",
    "bg":           "#0f1117",
    "panel":        "#1a1d27",
    "text":         "#e8e6df",
    "text_dim":     "#8a8880",
}
 
_DARK_STYLE = {
    "axes.facecolor":     _PALETTE["panel"],
    "figure.facecolor":   _PALETTE["bg"],
    "axes.edgecolor":     _PALETTE["grid"],
    "axes.labelcolor":    _PALETTE["text_dim"],
    "xtick.color":        _PALETTE["text_dim"],
    "ytick.color":        _PALETTE["text_dim"],
    "grid.color":         _PALETTE["grid"],
    "grid.linewidth":     0.5,
    "text.color":         _PALETTE["text"],
    "axes.titlesize":     12,
    "axes.titleweight":   "normal",
    "axes.labelsize":     10,
    "font.family":        "sans-serif",
}
 
 
def _apply_style():
    plt.rcParams.update(_DARK_STYLE)
 
 
def _format_xaxis(ax: plt.Axes, df: pd.DataFrame):
    """Auto-format x-axis depending on flight duration."""
    if pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        duration = (df["timestamp"].max() - df["timestamp"].min()).total_seconds()
        if duration < 3600:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M:%S"))
        elif duration < 86400:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        else:
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=25, ha="right")
    ax.grid(True, alpha=0.4)
 
 
def _twin_ax(ax: plt.Axes) -> plt.Axes:
    """Create a styled secondary y-axis."""
    ax2 = ax.twinx()
    ax2.tick_params(colors=_PALETTE["text_dim"])
    ax2.yaxis.label.set_color(_PALETTE["text_dim"])
    ax2.spines["right"].set_color(_PALETTE["grid"])
    for sp in ["top", "bottom", "left"]:
        ax2.spines[sp].set_visible(False)
    return ax2
 
 
# ── Main class ────────────────────────────────────────────────────────────────
 
class TimeSeriesPlot:
    """
    Generate time-series plots from an iMet-X4 DataFrame.
 
    Parameters
    ----------
    df : pd.DataFrame
        Must contain 'timestamp' and at least one sensor column.
    figsize : tuple, optional
        Default figure size for single-panel plots.
    """
 
    def __init__(self, df: pd.DataFrame, figsize: tuple = (12, 4)):
        self.df = df.copy()
        self.figsize = figsize
        self._last_fig: Optional[plt.Figure] = None
 
        # Ensure timestamp is datetime if possible
        if "timestamp" in self.df.columns:
            self.df["timestamp"] = pd.to_datetime(self.df["timestamp"], errors="coerce")
 
    # ------------------------------------------------------------------ #
    #  Single-variable helpers                                            #
    # ------------------------------------------------------------------ #
 
    def _base_fig(self, nrows: int = 1, ncols: int = 1,
                  figsize: Optional[tuple] = None) -> tuple[plt.Figure, any]:
        _apply_style()
        fig, axes = plt.subplots(nrows, ncols, figsize=figsize or self.figsize,
                                 constrained_layout=True)
        return fig, axes
 
    def _col(self, name: str) -> Optional[pd.Series]:
        return self.df[name] if name in self.df.columns else None
 
    # ------------------------------------------------------------------ #
    #  Public plot methods                                                 #
    # ------------------------------------------------------------------ #
 
    def temperature_humidity(self, title: str = "Temperature & Humidity") -> plt.Figure:
        """Dual-axis time series: temperature (left) and humidity (right)."""
        fig, ax1 = self._base_fig()
        ts = self.df["timestamp"]
 
        temp = self._col("temperature")
        hum  = self._col("humidity")
 
        if temp is not None:
            ax1.plot(ts, temp, color=_PALETTE["temperature"], lw=1.5, label="Temperature (°C)")
            ax1.set_ylabel("Temperature (°C)", color=_PALETTE["temperature"])
            ax1.tick_params(axis="y", labelcolor=_PALETTE["temperature"])
 
        if hum is not None:
            ax2 = _twin_ax(ax1)
            ax2.plot(ts, hum, color=_PALETTE["humidity"], lw=1.5,
                     linestyle="--", label="Humidity (%)")
            ax2.set_ylabel("Humidity (%)", color=_PALETTE["humidity"])
            ax2.tick_params(axis="y", labelcolor=_PALETTE["humidity"])
            ax2.set_ylim(0, 105)
 
            # Combined legend
            lines1, labs1 = ax1.get_legend_handles_labels()
            lines2, labs2 = ax2.get_legend_handles_labels()
            ax1.legend(lines1 + lines2, labs1 + labs2,
                       loc="upper right", framealpha=0.2, fontsize=9)
 
        _format_xaxis(ax1, self.df)
        ax1.set_title(title)
        self._last_fig = fig
        return fig
 
    def pressure_altitude(self, title: str = "Pressure & Altitude") -> plt.Figure:
        """Dual-axis: pressure (left) and altitude (right)."""
        fig, ax1 = self._base_fig()
        ts = self.df["timestamp"]
 
        pres = self._col("pressure")
        alt  = self._col("altitude")
 
        if pres is not None:
            ax1.fill_between(ts, pres, alpha=0.15, color=_PALETTE["pressure"])
            ax1.plot(ts, pres, color=_PALETTE["pressure"], lw=1.5, label="Pressure (hPa)")
            ax1.set_ylabel("Pressure (hPa)", color=_PALETTE["pressure"])
            ax1.tick_params(axis="y", labelcolor=_PALETTE["pressure"])
 
        if alt is not None:
            ax2 = _twin_ax(ax1)
            ax2.plot(ts, alt, color=_PALETTE["altitude"], lw=1.2,
                     linestyle=":", label="Altitude (m)")
            ax2.set_ylabel("Altitude (m)", color=_PALETTE["altitude"])
            ax2.tick_params(axis="y", labelcolor=_PALETTE["altitude"])
 
            lines1, labs1 = ax1.get_legend_handles_labels()
            lines2, labs2 = ax2.get_legend_handles_labels()
            ax1.legend(lines1 + lines2, labs1 + labs2,
                       loc="upper right", framealpha=0.2, fontsize=9)
 
        _format_xaxis(ax1, self.df)
        ax1.set_title(title)
        self._last_fig = fig
        return fig
 
    def wind(self, title: str = "Wind Speed & Direction") -> plt.Figure:
        """Wind speed time-series with direction scatter overlay."""
        fig, ax1 = self._base_fig()
        ts = self.df["timestamp"]
 
        speed = self._col("wind_speed")
        direc = self._col("wind_direction")
 
        if speed is not None:
            ax1.plot(ts, speed, color=_PALETTE["wind_speed"], lw=1.5, label="Speed (m/s)")
            ax1.set_ylabel("Wind speed (m/s)", color=_PALETTE["wind_speed"])
            ax1.tick_params(axis="y", labelcolor=_PALETTE["wind_speed"])
 
        if direc is not None:
            ax2 = _twin_ax(ax1)
            ax2.scatter(ts, direc, s=4, color=_PALETTE["wind_dir"],
                        alpha=0.6, label="Direction (°)")
            ax2.set_ylabel("Direction (°)", color=_PALETTE["wind_dir"])
            ax2.set_ylim(0, 360)
            ax2.yaxis.set_major_locator(mticker.MultipleLocator(90))
            ax2.tick_params(axis="y", labelcolor=_PALETTE["wind_dir"])
 
        _format_xaxis(ax1, self.df)
        ax1.set_title(title)
        self._last_fig = fig
        return fig
 
    def atmospheric_profile(self, title: str = "Atmospheric Profile") -> plt.Figure:
        """Temperature vs. altitude sounding profile (x=temp, y=altitude)."""
        _apply_style()
        fig, ax = plt.subplots(figsize=(5, 8), constrained_layout=True)
        fig.patch.set_facecolor(_PALETTE["bg"])
        ax.set_facecolor(_PALETTE["panel"])
        ax.grid(True, alpha=0.4)
        for sp in ax.spines.values():
            sp.set_color(_PALETTE["grid"])
 
        temp = self._col("temperature")
        alt  = self._col("altitude")
 
        if temp is not None and alt is not None:
            scatter = ax.scatter(temp, alt, c=temp, cmap="RdYlBu_r",
                                 s=6, alpha=0.8, linewidths=0)
            ax.plot(temp, alt, color=_PALETTE["temperature"], lw=0.8, alpha=0.5)
            cbar = fig.colorbar(scatter, ax=ax, pad=0.02)
            cbar.ax.tick_params(colors=_PALETTE["text_dim"], labelsize=9)
            cbar.set_label("Temperature (°C)", color=_PALETTE["text_dim"])
 
        ax.set_xlabel("Temperature (°C)")
        ax.set_ylabel("Altitude (m)")
        ax.set_title(title)
        self._last_fig = fig
        return fig
 
    def all_variables(self, title: str = "iMet-X4 Flight Overview") -> plt.Figure:
        """4-panel overview: temperature/humidity, pressure, wind, altitude."""
        _apply_style()
        fig, axes = plt.subplots(2, 2, figsize=(14, 7), constrained_layout=True)
        fig.suptitle(title, fontsize=13, color=_PALETTE["text"], y=1.01)
 
        ts = self.df["timestamp"]
 
        # Panel 1: Temperature & Humidity
        ax = axes[0, 0]
        temp = self._col("temperature")
        hum  = self._col("humidity")
        if temp is not None:
            ax.plot(ts, temp, color=_PALETTE["temperature"], lw=1.4, label="Temp (°C)")
        if hum is not None:
            ax2 = _twin_ax(ax)
            ax2.plot(ts, hum, color=_PALETTE["humidity"], lw=1.2, linestyle="--", label="RH (%)")
            ax2.set_ylim(0, 105)
        ax.set_title("Temperature & Humidity", fontsize=10)
        _format_xaxis(ax, self.df)
 
        # Panel 2: Pressure
        ax = axes[0, 1]
        pres = self._col("pressure")
        if pres is not None:
            ax.fill_between(ts, pres, alpha=0.12, color=_PALETTE["pressure"])
            ax.plot(ts, pres, color=_PALETTE["pressure"], lw=1.4)
            ax.set_ylabel("hPa", fontsize=9)
        ax.set_title("Pressure", fontsize=10)
        _format_xaxis(ax, self.df)
 
        # Panel 3: Wind speed
        ax = axes[1, 0]
        speed = self._col("wind_speed")
        if speed is not None:
            ax.plot(ts, speed, color=_PALETTE["wind_speed"], lw=1.4)
            ax.set_ylabel("m/s", fontsize=9)
        ax.set_title("Wind Speed", fontsize=10)
        _format_xaxis(ax, self.df)
 
        # Panel 4: Altitude
        ax = axes[1, 1]
        alt = self._col("altitude")
        if alt is not None:
            ax.fill_between(ts, alt, alpha=0.12, color=_PALETTE["altitude"])
            ax.plot(ts, alt, color=_PALETTE["altitude"], lw=1.4)
            ax.set_ylabel("m", fontsize=9)
        ax.set_title("Altitude", fontsize=10)
        _format_xaxis(ax, self.df)
 
        self._last_fig = fig
        return fig
 
    # ------------------------------------------------------------------ #
    #  Save                                                                #
    # ------------------------------------------------------------------ #
 
    def save(self, path: str | Path, dpi: int = 150, fig: Optional[plt.Figure] = None):
        """Save a figure to disk. Defaults to the last created figure."""
        target = fig or self._last_fig
        if target is None:
            raise RuntimeError("No figure to save. Call a plot method first.")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        target.savefig(path, dpi=dpi, bbox_inches="tight",
                       facecolor=target.get_facecolor())
        print(f"Saved → {path}")