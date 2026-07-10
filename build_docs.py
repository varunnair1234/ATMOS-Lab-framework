"""
Builds a static HTML snapshot of the iMet-X4 dashboard into docs/ for
GitHub Pages. Run locally with `python build_docs.py`, or let the
"Deploy to GitHub Pages" workflow run it on every push to main.

Loads data from DATA_CSV if set (a CSV with the columns iMet-X4 produces),
otherwise falls back to the same synthetic flight used by quickstart.py.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from datetime import datetime, timedelta

from framework.dashboard import Dashboard

DATA_CSV = os.environ.get("DATA_CSV")


def _load_data() -> pd.DataFrame:
    if DATA_CSV:
        return pd.read_csv(DATA_CSV, parse_dates=["timestamp"])

    np.random.seed(42)
    n = 600  # 10-minute flight at 1 Hz
    timestamps = [datetime(2024, 6, 1, 10, 0, 0) + timedelta(seconds=i) for i in range(n)]
    altitude = np.linspace(0, 1800, n) + np.random.normal(0, 5, n)
    temperature = 22 - (altitude / 100) * 0.65 + np.random.normal(0, 0.3, n)
    humidity = np.clip(60 - altitude / 50 + np.random.normal(0, 2, n), 10, 100)
    pressure = 1013.25 * np.exp(-altitude / 8500) + np.random.normal(0, 0.1, n)
    wind_speed = 3 + altitude / 500 + np.abs(np.random.normal(0, 1, n))
    wind_dir = (180 + altitude / 10 + np.random.normal(0, 15, n)) % 360

    return pd.DataFrame({
        "timestamp": timestamps,
        "altitude": altitude,
        "temperature": temperature,
        "humidity": humidity,
        "pressure": pressure,
        "wind_speed": wind_speed,
        "wind_direction": wind_dir,
    })


if __name__ == "__main__":
    df = _load_data()
    dashboard = Dashboard(dataframe=df)
    dashboard.export_static_html("docs/index.html")
    # Prevent GitHub Pages from running the Jekyll build step over docs/
    open("docs/.nojekyll", "w").close()
