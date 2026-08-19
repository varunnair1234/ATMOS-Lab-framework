"""
Offline sanity checks for framework.atmos22_reader against synthetic SDI-12
replies shaped like the ATMOS 22 Integrator's Guide examples -- entirely
offline (no serial port needed).
"""

import threading
from unittest.mock import MagicMock, patch

import serial

from framework.atmos22_reader import ATMOS22SDI12Reader, VALUE_FIELDS


def _fast_reader(**kwargs) -> ATMOS22SDI12Reader:
    kwargs.setdefault("reconnect_initial_delay", 0.01)
    kwargs.setdefault("reconnect_max_delay", 0.02)
    return ATMOS22SDI12Reader(port="COM_TEST", **kwargs)


def test_parse_measurement_reply():
    wait_s, n_values = ATMOS22SDI12Reader._parse_measurement_reply("0056")
    assert wait_s == 5
    assert n_values == 6


def test_take_measurement_parses_signed_packed_values():
    reader = _fast_reader()
    reader._ser = MagicMock()
    reader._ser.readline.side_effect = [
        b"00056\r\n",  # aM! reply: 005 s wait, 6 values (address '0' stripped by _read_reply)
        b"0+1.23+180+2.05+22.40+0.30-0.10\r\n",  # aD0! reply
    ]

    with patch("time.sleep") as mock_sleep:
        reading = reader.take_measurement()

    mock_sleep.assert_called_once_with(5)
    assert reading["wind_speed_ms"] == 1.23
    assert reading["wind_direction_deg"] == 180.0
    assert reading["gust_wind_speed_ms"] == 2.05
    assert reading["air_temperature_c"] == 22.40
    assert reading["x_tilt_deg"] == 0.30
    assert reading["y_tilt_deg"] == -0.10
    assert set(VALUE_FIELDS) <= reading.keys()


def test_take_measurement_raises_on_short_reply():
    reader = _fast_reader()
    reader._ser = MagicMock()
    reader._ser.readline.side_effect = [
        b"00053\r\n",  # claims 3 values
        b"0+1.23+180\r\n",  # but only 2 present
    ]
    with patch("time.sleep"):
        try:
            reader.take_measurement()
            assert False, "expected ValueError"
        except ValueError:
            pass


def test_to_canonical_row_normalizes_error_sentinels():
    reader = _fast_reader()
    reading = {
        "wind_speed_ms": -9999.0,   # general error sentinel
        "wind_direction_deg": 90.0,
        "gust_wind_speed_ms": -9990.0,  # invalid-wind sentinel
        "air_temperature_c": 18.5,
        "x_tilt_deg": 0.1,
        "y_tilt_deg": -0.2,
    }
    row = reader.to_canonical_row(reading)
    assert row["wind_speed"] is None
    assert row["wind_direction"] == 90.0
    assert row["wind_gust"] is None
    assert row["temperature"] == 18.5
    assert row["tilt_x"] == 0.1
    assert row["tilt_y"] == -0.2
    assert "timestamp" in row


def test_probe_true_on_reply_false_on_timeout():
    reader = _fast_reader()
    reader._ser = MagicMock()

    reader._ser.readline.side_effect = [b"0\r\n"]
    assert reader.probe(timeout_s=0.05) is True

    reader._ser.readline.side_effect = lambda: b""  # nothing ever comes back
    assert reader.probe(timeout_s=0.05) is False


def test_reconnect_retries_then_succeeds():
    reader = _fast_reader()
    reader._ser = MagicMock()

    good_serial = MagicMock()
    with patch("framework.atmos22_reader.serial.Serial",
               side_effect=[serial.SerialException("gone"), good_serial]) as mock_serial, \
         patch("framework.atmos22_reader.find_sdi12_adapter_port", return_value=None):
        reader._reconnect()

    assert reader._ser is good_serial
    assert mock_serial.call_count == 2


def test_reconnect_stops_promptly_when_stop_is_called():
    reader = _fast_reader()
    with patch("framework.atmos22_reader.serial.Serial",
               side_effect=serial.SerialException("gone")), \
         patch("framework.atmos22_reader.find_sdi12_adapter_port", return_value=None):
        t = threading.Thread(target=reader._reconnect)
        t.start()
        reader.stop()
        t.join(timeout=1.0)

    assert not t.is_alive()
    assert reader._ser is None


def test_start_fires_on_ready_once_then_reads_measurements():
    """start() should connect, poll one measurement cycle, fire on_ready
    exactly once, then stop cleanly -- covers the same lifecycle a
    dashboard/SensorHub relies on to flip from 'waiting' to 'live'."""
    reader = _fast_reader(poll_interval_s=0)
    reader._ser = MagicMock()
    reader.connected = True  # pretend _reconnect already succeeded

    replies = [b"00056\r\n", b"0+1.23+180+2.05+22.40+0.30-0.10\r\n"]
    reader._ser.readline.side_effect = lambda: replies.pop(0)

    ready_calls = []
    readings = []

    def on_reading(r):
        readings.append(r)
        reader.stop()  # stop right after the one cycle we seeded, instead of a second real cycle

    with patch("time.sleep"):
        reader.start(on_reading=on_reading, on_ready=lambda: ready_calls.append(True))

    assert len(ready_calls) == 1
    assert len(readings) == 1
    assert readings[0]["wind_speed_ms"] == 1.23


if __name__ == "__main__":
    test_parse_measurement_reply()
    test_take_measurement_parses_signed_packed_values()
    test_take_measurement_raises_on_short_reply()
    test_to_canonical_row_normalizes_error_sentinels()
    test_probe_true_on_reply_false_on_timeout()
    test_reconnect_retries_then_succeeds()
    test_reconnect_stops_promptly_when_stop_is_called()
    test_start_fires_on_ready_once_then_reads_measurements()
    print("All tests passed.")
