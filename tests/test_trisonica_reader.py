"""
Offline sanity checks for framework.trisonica_reader against a real sample
line shape from LI-COR's own ArduPilot integration example (Trisonica
LI-550 mini), entirely offline (no serial port needed):
  "S 00.08 S2 00.07 D 245 DV 033 U 00.06 V 00.03 W 00.05 T 21.40 C 346.68
   H 17.92 DP 03.68 P 1006.05"
"""

import threading
from unittest.mock import MagicMock, patch

import serial

from framework.trisonica_reader import TrisonicaMiniReader

SAMPLE_LINE = (
    "S 00.08 S2 00.07 D 245 DV 033 U 00.06 V 00.03 W 00.05 T 21.40 "
    "C 346.68 H 17.92 DP 03.68 P 1006.05"
)


def _fast_reader(**kwargs) -> TrisonicaMiniReader:
    kwargs.setdefault("reconnect_initial_delay", 0.01)
    kwargs.setdefault("reconnect_max_delay", 0.02)
    return TrisonicaMiniReader(port="COM_TEST", **kwargs)


def test_parses_known_values():
    reader = _fast_reader()
    parsed = reader.parse_line(SAMPLE_LINE)

    assert parsed["S"] == 0.08
    assert parsed["S2"] == 0.07
    assert parsed["D"] == 245
    assert parsed["DV"] == 33
    assert parsed["U"] == 0.06
    assert parsed["V"] == 0.03
    assert parsed["W"] == 0.05
    assert parsed["T"] == 21.40
    assert parsed["C"] == 346.68
    assert parsed["H"] == 17.92
    assert parsed["DP"] == 3.68
    assert parsed["P"] == 1006.05


def test_parse_line_raises_on_unparseable_line():
    reader = _fast_reader()
    try:
        reader.parse_line("Trisonica Mini booting...")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_to_canonical_row_maps_known_fields():
    reader = _fast_reader()
    parsed = reader.parse_line(SAMPLE_LINE)
    row = reader.to_canonical_row(parsed)

    assert row["wind_speed"] == 0.08
    assert row["wind_direction"] == 245
    assert row["temperature"] == 21.40
    assert row["humidity"] == 17.92
    assert row["pressure"] == 1006.05
    assert row["wind_u"] == 0.06
    assert row["wind_v"] == 0.03
    assert row["wind_w"] == 0.05
    assert row["compass_heading"] == 346.68
    assert "timestamp" in row


def test_to_canonical_row_handles_missing_optional_fields():
    """Only some outputs enabled (e.g. wind-only config, no P/H) shouldn't
    KeyError -- missing fields should come through as None."""
    reader = _fast_reader()
    parsed = reader.parse_line("S 01.20 D 090 U 00.00 V 01.20 W 00.00")
    row = reader.to_canonical_row(parsed)

    assert row["wind_speed"] == 1.20
    assert row["wind_direction"] == 90
    assert row["pressure"] is None
    assert row["humidity"] is None
    assert row["temperature"] is None


def test_reconnect_retries_then_succeeds():
    reader = _fast_reader()
    reader._ser = MagicMock()

    good_serial = MagicMock()
    with patch("framework.trisonica_reader.serial.Serial",
               side_effect=[serial.SerialException("gone"), good_serial]) as mock_serial, \
         patch("framework.trisonica_reader.find_trisonica_port", return_value=None):
        reader._reconnect()

    assert reader._ser is good_serial
    assert mock_serial.call_count == 2


def test_reconnect_stops_promptly_when_stop_is_called():
    reader = _fast_reader()
    with patch("framework.trisonica_reader.serial.Serial",
               side_effect=serial.SerialException("gone")), \
         patch("framework.trisonica_reader.find_trisonica_port", return_value=None):
        t = threading.Thread(target=reader._reconnect)
        t.start()
        reader.stop()
        t.join(timeout=1.0)

    assert not t.is_alive()
    assert reader._ser is None


def test_start_fires_on_ready_once_then_reads_queued_lines():
    reader = _fast_reader()
    reader._ser = MagicMock()
    reader.connected = True  # pretend _reconnect already succeeded

    lines = [SAMPLE_LINE.encode("ascii") + b"\r\n", b""]

    def fake_readline():
        if lines:
            line = lines.pop(0)
            if not lines:
                reader.stop()
            return line
        reader.stop()
        return b""

    reader._ser.readline.side_effect = fake_readline

    ready_calls = []
    readings = []
    reader.start(on_reading=readings.append, on_ready=lambda: ready_calls.append(True))

    assert len(ready_calls) == 1
    assert len(readings) == 1
    assert readings[0]["S"] == 0.08


if __name__ == "__main__":
    test_parses_known_values()
    test_parse_line_raises_on_unparseable_line()
    test_to_canonical_row_maps_known_fields()
    test_to_canonical_row_handles_missing_optional_fields()
    test_reconnect_retries_then_succeeds()
    test_reconnect_stops_promptly_when_stop_is_called()
    test_start_fires_on_ready_once_then_reads_queued_lines()
    print("All tests passed.")
