
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

DATA_CSV = os.environ.get("DATA_CSV")
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "outputs")

os.makedirs(OUTPUT_DIR, exist_ok=True)


def _load_data() -> pd.DataFrame:
    if DATA_CSV:
        return pd.read_csv(DATA_CSV, parse_dates=["timestamp"])

    # ── Generate synthetic flight data ────────────────────────────────────
    np.random.seed(42)
    n = 600  # 10-minute flight at 1 Hz

    timestamps  = [datetime(2024, 6, 1, 10, 0, 0) + timedelta(seconds=i) for i in range(n)]
    altitude    = np.linspace(0, 1800, n) + np.random.normal(0, 5, n)
    temperature = 22 - (altitude / 100) * 0.65 + np.random.normal(0, 0.3, n)  # lapse rate
    humidity    = np.clip(60 - altitude / 50 + np.random.normal(0, 2, n), 10, 100)
    pressure    = 1013.25 * np.exp(-altitude / 8500) + np.random.normal(0, 0.1, n)
    wind_speed  = 3 + altitude / 500 + np.abs(np.random.normal(0, 1, n))
    wind_dir    = (180 + altitude / 10 + np.random.normal(0, 15, n)) % 360
    latitude    = 40.7128 + np.cumsum(np.random.normal(0, 0.0001, n))
    longitude   = -74.0060 + np.cumsum(np.random.normal(0, 0.0001, n))

    return pd.DataFrame({
        "timestamp":      timestamps,
        "altitude":       altitude,
        "temperature":    temperature,
        "humidity":       humidity,
        "pressure":       pressure,
        "wind_speed":     wind_speed,
        "wind_direction": wind_dir,
        "latitude":       latitude,
        "longitude":      longitude,
    })


df = _load_data()
print(f"Loaded {len(df)} rows of {'real' if DATA_CSV else 'synthetic'} iMet-X4 data.\n")

# ── 1. Statistical summary ────────────────────────────────────────────────────
from framework.stats import StatsPlot

s = StatsPlot(df)
summary = s.summary()                   # prints table + returns DataFrame

s.histograms(bins=35)
s.save(f"{OUTPUT_DIR}/histograms.png")

s.correlation_heatmap()
s.save(f"{OUTPUT_DIR}/correlation.png")

# wind_rose/vertical_profile need real (non-null) data in these columns --
# a real X4 flight has no wind sensor, and may have no GPS altitude fix.
if "wind_direction" in df.columns and df["wind_direction"].notna().any():
    s.wind_rose()
    s.save(f"{OUTPUT_DIR}/wind_rose.png")

if "altitude" in df.columns and df["altitude"].notna().any():
    s.vertical_profile("temperature")
    s.save(f"{OUTPUT_DIR}/profile_temperature.png")

# ── 2. Time-series plots ──────────────────────────────────────────────────────
from framework.plots import TimeSeriesPlot

p = TimeSeriesPlot(df)
p.temperature_humidity()
p.save(f"{OUTPUT_DIR}/ts_temp_humidity.png")

p.pressure_altitude()
p.save(f"{OUTPUT_DIR}/ts_pressure_altitude.png")

if "wind_speed" in df.columns and df["wind_speed"].notna().any():
    p.wind()
    p.save(f"{OUTPUT_DIR}/ts_wind.png")

p.all_variables()
p.save(f"{OUTPUT_DIR}/ts_overview.png")

# ── 3. Real-time dashboard ────────────────────────────────────────────────────
from framework.dashboard import Dashboard

dash = Dashboard(dataframe=df)
dash.run(debug=False)
