"""
Offline sanity check for framework.serial_reader against the real sample line
and configuration captured from the user's iMet-X4 (serial 905302):
  - CBD default config (omits battery capacity/power/current/state)
  - J4: humidity sensor, not currently reporting (9999s)
  - J5: humidity sensor, reporting
  - J6, J7: no sensor
  - J8, J9: temperature sensors
"""

import threading
from unittest.mock import MagicMock, patch

import serial

from framework.serial_reader import IMetX4SerialReader

SAMPLE_LINE = (
    "905302,9999/9999/9999,9999:9999:9999,381.47,100,1015.10,28.15,46.30,"
    "28.82,9999,9999,9999,9999,9999,9999,9999,9999,9999,54.95,24.50,83271,"
    "23.98,83307,9999"
)


def build_reader() -> IMetX4SerialReader:
    from framework.serial_reader import BOARD_FIELDS, FieldSchema, PacketSchema, _bit_removed

    reader = IMetX4SerialReader(port="COM_TEST")
    cbd_body = "0,30720"  # comma delimiter, omits battery capacity/power/current/state
    delimiter, mask = reader._parse_cbd(cbd_body)

    board_fields = [
        FieldSchema(key, header, unit, kind, "board")
        for i, (key, header, unit, kind) in enumerate(BOARD_FIELDS)
        if not _bit_removed(mask, len(BOARD_FIELDS), i)
    ]

    cj_bodies = {
        4: "4,0x28,0",
        5: "4,0x28,0",
        6: "0",
        7: "0",
        8: "3,83271,+8.95098529E-004,+2.95526317E-004,-3.33149775E-006,+2.57965989E-007,0",
        9: "3,83307,+8.95098529E-004,+2.95526317E-004,-3.33149775E-006,+2.57965989E-007,0",
    }
    peripheral_fields = []
    for port_num in (4, 5, 6, 7, 8, 9):
        peripheral_fields.extend(reader._parse_cj(port_num, cj_bodies[port_num]))

    reader.schema = PacketSchema(delimiter=delimiter, fields=board_fields + peripheral_fields)
    return reader


def test_field_count_matches_sample_line():
    reader = build_reader()
    assert len(reader.schema.fields) == len(SAMPLE_LINE.split(","))


def test_parses_known_values():
    reader = build_reader()
    parsed = reader.parse_line(SAMPLE_LINE)

    assert parsed["serial_number"] == "905302"
    assert parsed["date"] == "9999/9999/9999"
    assert parsed["uptime_s"] == 381.47
    assert parsed["battery_charge_pct"] == 100
    assert parsed["pressure_hpa"] == 1015.10
    assert parsed["pressure_temp_c"] == 28.15
    assert parsed["board_humidity_pct"] == 46.30
    assert parsed["board_humidity_temp_c"] == 28.82
    assert parsed["satellites"] == 9999

    assert parsed["j4_relative_humidity_pct"] == 9999
    assert parsed["j4_humidity_temp_c"] == 9999
    assert parsed["j5_relative_humidity_pct"] == 54.95
    assert parsed["j5_humidity_temp_c"] == 24.50
    assert parsed["j8_serial_number"] == 83271
    assert parsed["j8_temperature_c"] == 23.98
    assert parsed["j9_serial_number"] == 83307
    assert parsed["j9_temperature_c"] == 9999

    # J6/J7 have no sensor configured, so they contribute no columns
    assert not any(k.startswith("j6_") or k.startswith("j7_") for k in parsed)


def test_dashboard_adapter_prefers_external_sensors():
    reader = build_reader()
    parsed = reader.parse_line(SAMPLE_LINE)
    item = reader.to_canonical_row(parsed)

    # only j8 has a valid (non-9999) temperature reading
    assert item["temperature"] == 23.98
    # only j5 has a valid (non-9999) humidity reading
    assert item["humidity"] == 54.95
    assert item["pressure"] == 1015.10
    assert item["latitude"] is None  # 9999 sentinel -> None
    assert item["altitude"] is None


def _fast_reader() -> IMetX4SerialReader:
    return IMetX4SerialReader(
        port="COM_TEST", reconnect_initial_delay=0.01, reconnect_max_delay=0.02
    )


def test_reconnect_retries_then_succeeds():
    reader = _fast_reader()
    reader._ser = MagicMock()  # pretend a connection existed before it dropped

    good_serial = MagicMock()
    with patch("framework.serial_reader.serial.Serial",
               side_effect=[serial.SerialException("gone"), serial.SerialException("gone"), good_serial]) as mock_serial, \
         patch("framework.serial_reader.find_imet_x4_port", return_value=None):
        reader._reconnect()

    assert reader._ser is good_serial
    assert mock_serial.call_count == 3


def test_reconnect_stops_promptly_when_stop_is_called():
    reader = _fast_reader()

    with patch("framework.serial_reader.serial.Serial",
               side_effect=serial.SerialException("gone")), \
         patch("framework.serial_reader.find_imet_x4_port", return_value=None):
        t = threading.Thread(target=reader._reconnect)
        t.start()
        reader.stop()
        t.join(timeout=1.0)

    assert not t.is_alive()  # returned promptly instead of waiting out the full backoff
    assert reader._ser is None  # never succeeded


if __name__ == "__main__":
    test_field_count_matches_sample_line()
    test_parses_known_values()
    test_dashboard_adapter_prefers_external_sensors()
    test_reconnect_retries_then_succeeds()
    test_reconnect_stops_promptly_when_stop_is_called()
    print("All tests passed.")
