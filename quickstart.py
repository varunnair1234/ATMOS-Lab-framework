
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

os.makedirs("outputs", exist_ok=True)

# ── Generate synthetic flight data ────────────────────────────────────────────

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

df = pd.DataFrame({
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

print(f"Loaded {len(df)} rows of synthetic iMet-X4 data.\n")

# ── 1. Statistical summary ────────────────────────────────────────────────────
from framework.stats import StatsPlot

s = StatsPlot(df)
summary = s.summary()                   # prints table + returns DataFrame

s.histograms(bins=35)
s.save("outputs/histograms.png")

s.correlation_heatmap()
s.save("outputs/correlation.png")

s.wind_rose()
s.save("outputs/wind_rose.png")

s.vertical_profile("temperature")
s.save("outputs/profile_temperature.png")

# ── 2. Time-series plots ──────────────────────────────────────────────────────
from framework.plots import TimeSeriesPlot

p = TimeSeriesPlot(df)
p.temperature_humidity()
p.save("outputs/ts_temp_humidity.png")

p.pressure_altitude()
p.save("outputs/ts_pressure_altitude.png")

p.wind()
p.save("outputs/ts_wind.png")

p.all_variables()
p.save("outputs/ts_overview.png")

# ── 3. Real-time dashboard ────────────────────────────────────────────────────
from framework.dashboard import Dashboard

dash = Dashboard(dataframe=df)
dash.run(debug=False)