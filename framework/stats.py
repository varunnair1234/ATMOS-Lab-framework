from __future__ import annotations
 
from pathlib import Path
from typing import Optional
 
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
import scipy.stats as stats
 
# ── Style (reused from plots.py conventions) ──────────────────────────────────
 
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
    "accent":       "#E8593C",
}
 
_SENSOR_COLS = ["temperature", "humidity", "pressure", "altitude", "wind_speed"]
 
_DARK_STYLE = {
    "axes.facecolor":    _PALETTE["panel"],
    "figure.facecolor":  _PALETTE["bg"],
    "axes.edgecolor":    _PALETTE["grid"],
    "axes.labelcolor":   _PALETTE["text_dim"],
    "xtick.color":       _PALETTE["text_dim"],
    "ytick.color":       _PALETTE["text_dim"],
    "grid.color":        _PALETTE["grid"],
    "grid.linewidth":    0.5,
    "text.color":        _PALETTE["text"],
    "axes.titlesize":    11,
    "axes.titleweight":  "normal",
    "axes.labelsize":    9,
    "font.family":       "sans-serif",
}
 
 
def _apply_style():
    plt.rcParams.update(_DARK_STYLE)
 
 
def _col_color(name: str) -> str:
    return _PALETTE.get(name, _PALETTE["accent"])
 
 
# ── Main class ────────────────────────────────────────────────────────────────
 
class StatsPlot:
    """
    Statistical analysis and visualization for iMet-X4 data.
 
    Parameters
    ----------
    df : pd.DataFrame
        Sensor data with at least some of: temperature, humidity, pressure,
        altitude, wind_speed, wind_direction.
    """
 
    def __init__(self, df: pd.DataFrame):
        self.df = df.copy()
        self._sensor_cols = [c for c in _SENSOR_COLS if c in self.df.columns]
        self._last_fig: Optional[plt.Figure] = None
 
    # ------------------------------------------------------------------ #
    #  Descriptive statistics                                              #
    # ------------------------------------------------------------------ #
 
    def summary(self, print_table: bool = True) -> pd.DataFrame:
        """
        Compute and optionally print a descriptive statistics table.
 
        Returns a DataFrame with: count, mean, std, min, 25%, 50%, 75%, max,
        skewness, kurtosis for each sensor variable.
        """
        cols = self._sensor_cols
        if not cols:
            raise ValueError("No recognized sensor columns found in DataFrame.")
 
        base = self.df[cols].describe().T
        base["skewness"] = self.df[cols].skew()
        base["kurtosis"] = self.df[cols].kurt()
        base = base.round(3)
 
        if print_table:
            width = 90
            print("─" * width)
            print(f"  iMet-X4 Statistical Summary  ({len(self.df):,} readings)")
            print("─" * width)
            print(base.to_string())
            print("─" * width)
 
        return base
 
    # ------------------------------------------------------------------ #
    #  Histogram grid                                                      #
    # ------------------------------------------------------------------ #
 
    def histograms(
        self,
        bins: int = 40,
        kde: bool = True,
        title: str = "Variable Distributions",
    ) -> plt.Figure:
        """
        Grid of histograms (one per sensor variable) with optional KDE overlay.
 
        Parameters
        ----------
        bins : int
            Number of histogram bins.
        kde : bool
            Overlay a kernel density estimate curve.
        """
        _apply_style()
        cols = self._sensor_cols
        ncols = min(3, len(cols))
        nrows = int(np.ceil(len(cols) / ncols))
 
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(5 * ncols, 3.5 * nrows),
                                 constrained_layout=True)
        fig.suptitle(title, fontsize=13, color=_PALETTE["text"])
 
        axes_flat = np.array(axes).flatten() if len(cols) > 1 else [axes]
 
        for i, col in enumerate(cols):
            ax = axes_flat[i]
            data = self.df[col].dropna()
            color = _col_color(col)
 
            ax.hist(data, bins=bins, color=color, alpha=0.55,
                    edgecolor="none", density=kde)
 
            if kde and len(data) > 5:
                kde_x = np.linspace(data.min(), data.max(), 300)
                kde_y = stats.gaussian_kde(data)(kde_x)
                ax.plot(kde_x, kde_y, color=color, lw=1.8)
 
            # Vertical lines for mean and ±1σ
            mean, std = data.mean(), data.std()
            ax.axvline(mean, color=color, lw=1.2, linestyle="--", alpha=0.9)
            ax.axvline(mean - std, color=color, lw=0.7, linestyle=":", alpha=0.5)
            ax.axvline(mean + std, color=color, lw=0.7, linestyle=":", alpha=0.5)
 
            # Annotation
            ax.text(0.97, 0.95,
                    f"μ = {mean:.2f}\nσ = {std:.2f}",
                    transform=ax.transAxes,
                    ha="right", va="top",
                    fontsize=8.5, color=_PALETTE["text_dim"],
                    linespacing=1.6)
 
            ax.set_title(col.replace("_", " ").title(), fontsize=10)
            ax.set_xlabel(col)
            ax.set_ylabel("Density" if kde else "Count")
            ax.grid(True, alpha=0.35)
            for sp in ax.spines.values():
                sp.set_color(_PALETTE["grid"])
 
        # Hide unused axes
        for j in range(len(cols), len(axes_flat)):
            axes_flat[j].set_visible(False)
 
        self._last_fig = fig
        return fig
 
    # ------------------------------------------------------------------ #
    #  Correlation heatmap                                                 #
    # ------------------------------------------------------------------ #
 
    def correlation_heatmap(self, title: str = "Correlation Matrix") -> plt.Figure:
        """
        Annotated heatmap of Pearson correlations between all sensor variables.
        """
        _apply_style()
        cols = self._sensor_cols
        corr = self.df[cols].corr()
 
        fig, ax = plt.subplots(figsize=(max(5, len(cols) + 1),
                                        max(4, len(cols))),
                               constrained_layout=True)
        fig.patch.set_facecolor(_PALETTE["bg"])
        ax.set_facecolor(_PALETTE["panel"])
 
        # Custom diverging colormap (blue → white → red)
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "imet_div", ["#3B8BD4", "#1a1d27", "#E8593C"], N=256
        )
 
        im = ax.imshow(corr.values, cmap=cmap, vmin=-1, vmax=1, aspect="auto")
 
        cbar = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.02)
        cbar.ax.tick_params(colors=_PALETTE["text_dim"], labelsize=9)
        cbar.set_label("Pearson r", color=_PALETTE["text_dim"])
 
        ax.set_xticks(range(len(cols)))
        ax.set_yticks(range(len(cols)))
        ax.set_xticklabels([c.replace("_", "\n") for c in cols], fontsize=9)
        ax.set_yticklabels([c.replace("_", " ").title() for c in cols], fontsize=9)
 
        # Annotate cells
        for i in range(len(cols)):
            for j in range(len(cols)):
                val = corr.values[i, j]
                text_color = _PALETTE["text"] if abs(val) > 0.5 else _PALETTE["text_dim"]
                ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                        fontsize=9, color=text_color)
 
        ax.set_title(title, color=_PALETTE["text"])
        ax.tick_params(colors=_PALETTE["text_dim"])
        for sp in ax.spines.values():
            sp.set_color(_PALETTE["grid"])
 
        self._last_fig = fig
        return fig
 
    # ------------------------------------------------------------------ #
    #  Wind rose                                                           #
    # ------------------------------------------------------------------ #
 
    def wind_rose(
        self,
        n_sectors: int = 16,
        title: str = "Wind Rose",
    ) -> plt.Figure:
        """
        Polar histogram of wind direction, bar length = frequency,
        bar color = mean speed per sector.
 
        Requires 'wind_direction' (degrees) in the DataFrame.
        'wind_speed' is used for color if available.
        """
        _apply_style()
        if "wind_direction" not in self.df.columns:
            raise ValueError("'wind_direction' column is required for wind rose.")
 
        directions = self.df["wind_direction"].dropna() % 360
        speeds     = self.df["wind_speed"].reindex(directions.index) \
                     if "wind_speed" in self.df.columns else None
 
        sector_width = 360 / n_sectors
        sector_centers = np.arange(0, 360, sector_width)
        freqs, mean_speeds = [], []
 
        for center in sector_centers:
            lo = (center - sector_width / 2) % 360
            hi = (center + sector_width / 2) % 360
            if lo < hi:
                mask = (directions >= lo) & (directions < hi)
            else:
                mask = (directions >= lo) | (directions < hi)
            freqs.append(mask.sum())
            if speeds is not None and mask.sum() > 0:
                mean_speeds.append(speeds[mask].mean())
            else:
                mean_speeds.append(0.0)
 
        freqs = np.array(freqs, dtype=float)
        mean_speeds = np.array(mean_speeds)
 
        # Normalize speeds for colormap
        if mean_speeds.max() > 0:
            norm_speeds = mean_speeds / mean_speeds.max()
        else:
            norm_speeds = np.zeros_like(mean_speeds)
 
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "wind", ["#1a1d27", "#BA7517", "#E8593C"], N=256
        )
        bar_colors = [cmap(t) for t in norm_speeds]
 
        # Convert directions to radians (meteorological: 0° = North = top)
        theta = np.deg2rad(sector_centers - 90)  # rotate so 0° is East → -90° offset
        theta = np.deg2rad(90 - sector_centers)  # North at top
 
        fig = plt.figure(figsize=(6, 6))
        fig.patch.set_facecolor(_PALETTE["bg"])
        ax = fig.add_subplot(111, projection="polar")
        ax.set_facecolor(_PALETTE["panel"])
 
        # Bars
        bars = ax.bar(
            theta, freqs,
            width=np.deg2rad(sector_width * 0.9),
            bottom=0,
            color=bar_colors,
            edgecolor=_PALETTE["panel"],
            linewidth=0.4,
            alpha=0.9,
        )
 
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_xticks(np.deg2rad([0, 45, 90, 135, 180, 225, 270, 315]))
        ax.set_xticklabels(["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
                           color=_PALETTE["text_dim"], fontsize=9)
        ax.tick_params(colors=_PALETTE["text_dim"])
        ax.spines["polar"].set_color(_PALETTE["grid"])
        ax.yaxis.set_tick_params(labelsize=8, labelcolor=_PALETTE["text_dim"])
        ax.set_ylabel("Count", color=_PALETTE["text_dim"], labelpad=24, fontsize=9)
 
        # Colorbar
        sm = plt.cm.ScalarMappable(cmap=cmap,
                                    norm=mcolors.Normalize(0, mean_speeds.max()))
        sm.set_array([])
        cbar = fig.colorbar(sm, ax=ax, pad=0.1, fraction=0.04)
        cbar.set_label("Mean speed (m/s)", color=_PALETTE["text_dim"], fontsize=9)
        cbar.ax.tick_params(colors=_PALETTE["text_dim"], labelsize=8)
 
        ax.set_title(title, color=_PALETTE["text"], pad=18)
        self._last_fig = fig
        return fig
 
    # ------------------------------------------------------------------ #
    #  Vertical profile with error envelope                               #
    # ------------------------------------------------------------------ #
 
    def vertical_profile(
        self,
        variable: str = "temperature",
        n_bins: int = 20,
        title: Optional[str] = None,
    ) -> plt.Figure:
        """
        Altitude-binned mean profile with ±1σ shaded envelope.
 
        Parameters
        ----------
        variable : str
            Sensor column to profile against altitude.
        n_bins : int
            Number of altitude bins.
        """
        _apply_style()
        if "altitude" not in self.df.columns:
            raise ValueError("'altitude' column is required for vertical profiles.")
        if variable not in self.df.columns:
            raise ValueError(f"Column '{variable}' not found in DataFrame.")
 
        df = self.df[["altitude", variable]].dropna()
        bins = pd.cut(df["altitude"], bins=n_bins)
        grouped = df.groupby(bins)[variable]
        means = grouped.mean()
        stds  = grouped.std().fillna(0)
        mid   = means.index.map(lambda x: x.mid)
 
        color = _col_color(variable)
 
        _apply_style()
        fig, ax = plt.subplots(figsize=(5, 8), constrained_layout=True)
        fig.patch.set_facecolor(_PALETTE["bg"])
        ax.set_facecolor(_PALETTE["panel"])
        ax.grid(True, alpha=0.35)
        for sp in ax.spines.values():
            sp.set_color(_PALETTE["grid"])
 
        ax.fill_betweenx(mid, means - stds, means + stds,
                         color=color, alpha=0.18)
        ax.plot(means, mid, color=color, lw=2)
        ax.scatter(means, mid, color=color, s=20, zorder=5)
 
        unit_map = {"temperature": "°C", "humidity": "%",
                    "pressure": "hPa", "wind_speed": "m/s"}
        unit = unit_map.get(variable, "")
        ax.set_xlabel(f"{variable.replace('_', ' ').title()} ({unit})")
        ax.set_ylabel("Altitude (m)")
        ax.set_title(title or f"Vertical Profile — {variable.replace('_', ' ').title()}")
 
        # Annotation: surface and peak values
        v_surface = means.iloc[0]
        v_peak    = means.iloc[-1]
        ax.text(0.97, 0.03,
                f"Surface: {v_surface:.1f} {unit}\nPeak:    {v_peak:.1f} {unit}",
                transform=ax.transAxes, ha="right", va="bottom",
                fontsize=8.5, color=_PALETTE["text_dim"], linespacing=1.7)
 
        self._last_fig = fig
        return fig
 
    # ------------------------------------------------------------------ #
    #  Save                                                                #
    # ------------------------------------------------------------------ #
 
    def save(self, path: str | Path, dpi: int = 150,
             fig: Optional[plt.Figure] = None):
        """Save a figure to disk (defaults to the last created figure)."""
        target = fig or self._last_fig
        if target is None:
            raise RuntimeError("No figure to save. Call a plot method first.")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        target.savefig(path, dpi=dpi, bbox_inches="tight",
                       facecolor=target.get_facecolor())
        print(f"Saved → {path}")