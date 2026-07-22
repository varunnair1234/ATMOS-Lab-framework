"""
Synthetic iMet-X4 flight data generator.

Reuses the same sensor formulas as quickstart.py (lapse rate, barometric
formula, etc.) but generates readings one at a time, looping the aircraft
through an indefinite climb/descend cycle instead of a single fixed-length
flight. Used to drive the "live" dashboard feed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd


class FlightSimulator:
    """
    Produces one synthetic sensor reading per `step()` call, forever.

    Altitude follows a smooth triangle-wave climb/descend cycle of
    `period_seconds` up to `ceiling` meters, with the same derived
    temperature/humidity/pressure/wind relationships used in quickstart.py.
    """

    def __init__(
        self,
        start_time: Optional[datetime] = None,
        period_seconds: int = 600,
        ceiling: float = 1800.0,
        seed: Optional[int] = 42,
    ):
        self._rng = np.random.default_rng(seed)
        self._t = 0
        self._start_time = start_time or datetime.utcnow()
        self._period = period_seconds
        self._ceiling = ceiling
        self._lat = 40.7128
        self._lon = -74.0060

    def step(self) -> dict:
        phase = (self._t % self._period) / self._period
        triangle = 1 - abs(2 * phase - 1)  # 0 -> 1 -> 0 over one period
        altitude = max(0.0, self._ceiling * triangle + self._rng.normal(0, 5))

        temperature = 22 - (altitude / 100) * 0.65 + self._rng.normal(0, 0.3)
        humidity = float(np.clip(60 - altitude / 50 + self._rng.normal(0, 2), 10, 100))
        pressure = 1013.25 * np.exp(-altitude / 8500) + self._rng.normal(0, 0.1)
        wind_speed = 3 + altitude / 500 + abs(self._rng.normal(0, 1))
        wind_direction = (180 + altitude / 10 + self._rng.normal(0, 15)) % 360

        self._lat += self._rng.normal(0, 0.0001)
        self._lon += self._rng.normal(0, 0.0001)

        reading = {
            "timestamp": self._start_time + timedelta(seconds=self._t),
            "altitude": altitude,
            "temperature": temperature,
            "humidity": humidity,
            "pressure": pressure,
            "wind_speed": wind_speed,
            "wind_direction": wind_direction,
            "latitude": self._lat,
            "longitude": self._lon,
        }
        self._t += 1
        return reading

    def generate(self, n: int) -> pd.DataFrame:
        """Convenience helper: run `n` steps and return them as a DataFrame."""
        return pd.DataFrame([self.step() for _ in range(n)])
