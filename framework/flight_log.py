"""
Persists a live IMetX4SerialReader session to disk as two CSVs:

  * a canonical file (timestamp, temperature, humidity, pressure,
    latitude, longitude, altitude) -- the same schema quickstart.py,
    build_docs.py and framework.dashboard.Dashboard already consume, so a
    real flight drops straight into the existing stats/plots pipeline.
  * a raw file with every field IMetX4SerialReader.parse_line() produces
    for the session's live configuration (battery stats, GPS HDOP,
    per-peripheral sensor serials, etc.) -- independent of the X4's own
    onboard microSD log.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Sequence


class FlightLogger:
    def __init__(self, canonical_fields: Sequence[str], raw_fields: Sequence[str],
                 log_dir: str = "flights"):
        log_dir_path = Path(log_dir)
        log_dir_path.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.canonical_path = log_dir_path / f"imet_x4_{stamp}.csv"
        self.raw_path = log_dir_path / f"imet_x4_{stamp}_raw.csv"
        self.rows_written = 0

        self._canonical_fh = open(self.canonical_path, "w", newline="", encoding="ascii")
        self._raw_fh = open(self.raw_path, "w", newline="", encoding="ascii")
        self._canonical_writer = csv.DictWriter(self._canonical_fh, fieldnames=list(canonical_fields))
        self._raw_writer = csv.DictWriter(self._raw_fh, fieldnames=list(raw_fields))
        self._canonical_writer.writeheader()
        self._raw_writer.writeheader()

    def write(self, raw_reading: dict, canonical_reading: dict):
        self._raw_writer.writerow(raw_reading)
        self._canonical_writer.writerow(canonical_reading)
        self.rows_written += 1

    def close(self):
        self._canonical_fh.close()
        self._raw_fh.close()
