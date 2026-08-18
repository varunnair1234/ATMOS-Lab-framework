"""
Fuses one or more live sensor readers into a single canonical stream, so
framework.dashboard.Dashboard and framework.flight_log.FlightLogger --
which were both built around a single iMet-X4 -- can show/log one combined
picture even though wind now comes from a separate SDI-12 or serial sensor
that samples on its own schedule.

Supported readers, anything with a .start(on_reading=, on_ready=) method
and a .to_canonical_row(reading) method following the pattern in
framework.serial_reader.IMetX4SerialReader:
    framework.serial_reader.IMetX4SerialReader      -- temperature, humidity,
                                                        pressure, lat/lon, altitude
    framework.atmos22_reader.ATMOS22SDI12Reader      -- wind_speed, wind_direction,
                                                        wind_gust, tilt_x/y
    framework.trisonica_reader.TrisonicaMiniReader   -- wind_speed, wind_direction,
                                                        temperature, humidity,
                                                        pressure, wind_u/v/w

Why fusion instead of just pointing three readers at the same data_queue:
each sensor samples on its own schedule (iMet-X4 ~1 Hz, ATMOS 22 ~every
10 s, Trisonica at whatever rate its own config sets), so raw interleaving
would give the dashboard a stream of mostly-empty rows -- e.g. a wind
reading with temperature/pressure blank because the X4 hasn't reported
since. SensorHub instead keeps the latest known value per field and emits
a merged row (last-observation-carried-forward) every time ANY source
reports -- the same tradeoff most live multi-sensor dashboards make, at
the cost of wind and met values not being perfectly time-aligned to the
second within a merged row.

Field precedence when two sources both provide a column (e.g. wind_speed
from both the ATMOS 22 and the Trisonica, or temperature from both the
iMet-X4 and the Trisonica): the source list order given to SensorHub()
wins, first-listed source takes priority whenever it has a non-None value
for that field. Every source's fields are also kept unprefixed-merged AND
stashed per-source below for the raw log (see to_raw_dict), so nothing is
silently discarded even when a field is overridden in the fused view.
"""

from __future__ import annotations

import threading
from typing import Optional


class SensorHub:
    """
    Parameters
    ----------
    sources : dict[str, reader]
        Name -> reader instance (already constructed, not yet started).
        Iteration order is precedence order for overlapping canonical
        fields -- put your primary wind sensor first if both a dedicated
        anemometer and the Trisonica are attached, for example.
    data_queue : queue.Queue, optional
        Thread-safe queue that receives merged canonical rows (dashboard-
        or FlightLogger-ready). Rows also always update self.latest even
        without a queue.
    """

    def __init__(self, sources: dict, data_queue=None):
        if not sources:
            raise ValueError("Provide at least one source reader.")
        self.sources = sources
        self.data_queue = data_queue
        self.latest: Optional[dict] = None
        self._last_by_source: dict = {name: {} for name in sources}
        self._lock = threading.Lock()
        self._threads: list = []
        self._ready_sources: set = set()

    # -- lifecycle -------------------------------------------------------- #

    def start(self, on_reading=None, on_ready=None):
        """Starts every source's reader.start() loop in its own daemon
        thread (they're already independently blocking/reconnecting, so
        each just needs its own thread rather than any extra coordination
        here). Non-blocking -- returns once threads are launched.

        on_ready() fires once ALL sources have reported at least once
        (i.e. the fused row has every configured source's contribution),
        not once per source -- so a dashboard doesn't flip to "live" while
        two of three sensors are still silent.
        """
        for name, reader in self.sources.items():
            t = threading.Thread(
                target=reader.start,
                kwargs={
                    "on_reading": self._make_on_reading(name, on_reading),
                    "on_ready": self._make_on_ready(name, on_ready),
                },
                daemon=True,
                name=f"sensorhub-{name}",
            )
            t.start()
            self._threads.append(t)

    def stop(self):
        for reader in self.sources.values():
            reader.stop()
        for t in self._threads:
            t.join(timeout=2.0)

    def close(self):
        self.stop()
        for reader in self.sources.values():
            close = getattr(reader, "close", None)
            if close is not None:
                close()

    # -- fusion ------------------------------------------------------------ #

    def _make_on_reading(self, name, forward_to):
        def _on_reading(raw_reading):
            reader = self.sources[name]
            canonical = reader.to_canonical_row(raw_reading)
            with self._lock:
                self._last_by_source[name] = canonical
                merged = self._merge_locked()
                self.latest = merged
            if forward_to is not None:
                forward_to(name, raw_reading, merged)
            if self.data_queue is not None:
                self.data_queue.put(merged)
        return _on_reading

    def _make_on_ready(self, name, forward_to):
        def _on_ready(*_args):
            with self._lock:
                self._ready_sources.add(name)
                all_ready = self._ready_sources == set(self.sources)
            if all_ready and forward_to is not None:
                forward_to()
        return _on_ready

    def _merge_locked(self) -> dict:
        """Builds one row: for each field, the first source (in `sources`
        iteration/precedence order) that has reported a non-None value for
        it. Must be called with self._lock held."""
        merged: dict = {}
        for name in self.sources:  # precedence order
            for key, value in self._last_by_source.get(name, {}).items():
                if key == "timestamp":
                    continue
                if key not in merged or merged[key] is None:
                    if value is not None:
                        merged[key] = value
        # timestamp reflects "when was this merged row assembled", not any
        # one source's own timestamp -- consistent with each reader's
        # to_canonical_row already stamping its own reading at receipt time.
        merged["timestamp"] = max(
            (row.get("timestamp") for row in self._last_by_source.values() if row.get("timestamp")),
            default=None,
        )
        return merged

    def to_raw_dict(self) -> dict:
        """Every source's canonical fields, prefixed by source name, for a
        full-fidelity raw log alongside the merged/fused canonical row --
        same "canonical + raw" split framework.flight_log.FlightLogger
        already does for a single iMet-X4 session."""
        with self._lock:
            out = {}
            for name, row in self._last_by_source.items():
                for key, value in row.items():
                    out[f"{name}_{key}"] = value
            return out
