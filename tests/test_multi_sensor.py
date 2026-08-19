"""
Offline tests for framework.multi_sensor.SensorHub's fusion logic, using
lightweight fake readers instead of real serial/SDI-12 devices.
"""

from datetime import datetime

from framework.multi_sensor import SensorHub


class _FakeReader:
    """Minimal stand-in for IMetX4SerialReader/ATMOS22SDI12Reader/
    TrisonicaMiniReader: on start(), synchronously fires each queued
    canonical row through on_reading, then returns (no real threading/
    blocking needed for these tests)."""

    def __init__(self, rows):
        self._rows = rows
        self.stopped = False

    def to_canonical_row(self, raw):
        return raw  # tests pass already-canonical dicts as "raw" for simplicity

    def start(self, on_reading=None, on_ready=None):
        if on_ready is not None:
            on_ready()
        for row in self._rows:
            if on_reading is not None:
                on_reading(row)

    def stop(self):
        self.stopped = True


def test_merge_prefers_first_listed_source_for_overlapping_fields():
    atmos22 = _FakeReader([
        {"timestamp": datetime(2026, 1, 1, 0, 0, 0), "wind_speed": 3.5, "wind_direction": 180, "wind_gust": 5.0},
    ])
    trisonica = _FakeReader([
        {"timestamp": datetime(2026, 1, 1, 0, 0, 1), "wind_speed": 3.9, "wind_direction": 175, "temperature": 21.0},
    ])

    hub = SensorHub({"atmos22": atmos22, "trisonica": trisonica})
    hub.start()
    for t in hub._threads:
        t.join(timeout=1.0)

    # atmos22 listed first -> its wind_speed/direction win even though
    # trisonica reported more recently; fields atmos22 doesn't have (temperature)
    # still come through from trisonica.
    assert hub.latest["wind_speed"] == 3.5
    assert hub.latest["wind_direction"] == 180
    assert hub.latest["wind_gust"] == 5.0
    assert hub.latest["temperature"] == 21.0


def test_merge_falls_back_when_preferred_source_field_is_none():
    atmos22 = _FakeReader([
        {"timestamp": datetime(2026, 1, 1), "wind_speed": None, "wind_direction": None},
    ])
    trisonica = _FakeReader([
        {"timestamp": datetime(2026, 1, 1), "wind_speed": 2.2, "wind_direction": 90},
    ])

    hub = SensorHub({"atmos22": atmos22, "trisonica": trisonica})
    hub.start()
    for t in hub._threads:
        t.join(timeout=1.0)

    assert hub.latest["wind_speed"] == 2.2
    assert hub.latest["wind_direction"] == 90


def test_on_ready_fires_once_all_sources_ready():
    x4 = _FakeReader([{"timestamp": datetime(2026, 1, 1), "temperature": 20.0}])
    atmos22 = _FakeReader([{"timestamp": datetime(2026, 1, 1), "wind_speed": 1.0}])

    ready_calls = []
    hub = SensorHub({"atmos22": atmos22, "imet_x4": x4})
    hub.start(on_ready=lambda: ready_calls.append(True))
    for t in hub._threads:
        t.join(timeout=1.0)

    assert len(ready_calls) == 1


def test_to_raw_dict_prefixes_each_source():
    x4 = _FakeReader([{"timestamp": datetime(2026, 1, 1), "temperature": 20.0}])
    atmos22 = _FakeReader([{"timestamp": datetime(2026, 1, 1), "wind_speed": 1.0}])

    hub = SensorHub({"atmos22": atmos22, "imet_x4": x4})
    hub.start()
    for t in hub._threads:
        t.join(timeout=1.0)

    raw = hub.to_raw_dict()
    assert raw["atmos22_wind_speed"] == 1.0
    assert raw["imet_x4_temperature"] == 20.0


def test_construction_requires_at_least_one_source():
    try:
        SensorHub({})
        assert False, "expected ValueError"
    except ValueError:
        pass


if __name__ == "__main__":
    test_merge_prefers_first_listed_source_for_overlapping_fields()
    test_merge_falls_back_when_preferred_source_field_is_none()
    test_on_ready_fires_once_all_sources_ready()
    test_to_raw_dict_prefixes_each_source()
    test_construction_requires_at_least_one_source()
    print("All tests passed.")
