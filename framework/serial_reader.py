"""
Live serial ingestion for the InterMet iMet-X4 Hub.

Reference: iMet-X4 Hub User Guide and Manual, doc 252100.0200 Rev 12.

The X4 streams one CSV line per sample on J1 at 115200 baud (8N1). The set of
columns in that line is NOT fixed -- it depends on the board's live
configuration:

  * /CBD (Configure Board Data) carries a 20-bit mask that can omit any of
    the 20 built-in board columns (battery stats, pressure, GPS, ...) --
    see section 4.2 of the manual.
  * /CJ4 .. /CJ9 (Configure J4-J9) tell the board what peripheral, if any,
    is plugged into each port (temperature sensor, humidity sensor, serial
    forward, ...) -- see section 4.3. Each peripheral type contributes its
    own columns, appended in port order (J4, J5, J6, J7, J8, J9).

Rather than hardcode a column layout, this module polls the board for its
current configuration on connect (the same "Fetch" step the iMet-XS
software performs) and builds the column schema from the responses. That
keeps parsing correct regardless of which sensors are plugged in.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import serial
from serial.tools import list_ports

DEFAULT_BAUD = 115200

# Backoff for reconnecting after the serial link drops mid-session (a USB
# hiccup or radio dropout, not the initial connect -- see IMetX4SerialReader.start).
RECONNECT_INITIAL_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 15.0

# --------------------------------------------------------------------------- #
#  Board data columns (manual section 4.2, Table "Data Field")
#  index -> (key, header, unit, value kind)
# --------------------------------------------------------------------------- #
_TEXT, _FLOAT, _INT = "text", "float", "int"

BOARD_FIELDS = [
    ("serial_number",        "Serial Number",           None,   _TEXT),
    ("date",                 "Date",                    "mm/dd/yyyy", _TEXT),
    ("utc_time",              "UTC Time",                "hh:mm:ss", _TEXT),
    ("uptime_s",              "Uptime",                  "s",    _FLOAT),
    ("battery_charge_pct",    "Battery Charge",          "%",    _FLOAT),
    ("battery_capacity_mah",  "Battery Capacity",        "mAh",  _FLOAT),
    ("battery_power_mw",      "Battery Power",           "mW",   _FLOAT),
    ("battery_current_ma",    "Battery Current",         "mA",   _FLOAT),
    ("battery_charge_state",  "Battery Charge State",    None,   _INT),
    ("pressure_hpa",          "Pressure",                "hPa",  _FLOAT),
    ("pressure_temp_c",       "Pressure Temperature",    "C",    _FLOAT),
    # NOTE: manual Table 3-1 footnote 2 -- this onboard humidity sensor is
    # "for board health only" and is NOT indicative of free-air conditions.
    ("board_humidity_pct",      "Relative Humidity (onboard)", "%", _FLOAT),
    ("board_humidity_temp_c",   "Humidity Temperature (onboard)", "C", _FLOAT),
    ("satellites",             "Satellites",              None,  _INT),
    ("latitude_deg",           "Latitude",                "deg", _FLOAT),
    ("longitude_deg",          "Longitude",               "deg", _FLOAT),
    ("hdop",                   "Horizontal Dilution of Precision", None, _FLOAT),
    ("altitude_m",             "Altitude",                "m",   _FLOAT),
    ("ground_speed_mps",       "Speed Over Ground",       "m/s", _FLOAT),
    ("tracking_angle_deg",     "Tracking Angle (true)",   "deg", _FLOAT),
]
assert len(BOARD_FIELDS) == 20

# Peripheral IDs (manual sections 4.3.1-4.3.5)
PERIPHERAL_NO_SENSOR = 0
PERIPHERAL_TEMPERATURE = 3
PERIPHERAL_HUMIDITY = 4
PERIPHERAL_SERIAL_FORWARD = 6
_RESERVED_PERIPHERALS = {1: "Board sensors", 2: "Ozone", 5: "InterMet 100870", 7: "PM 2.5"}

INVALID_SENTINEL = 9999


def _bit_removed(mask: int, total_bits: int, index: int) -> bool:
    """True if the manual's "bit=1 removes this column" applies to `index`.

    The manual defines index 0 as the MOST significant bit, so for an
    N-bit mask, index i lives at bit position (N-1-i) counting from the LSB.
    """
    return bool((mask >> (total_bits - 1 - index)) & 1)


def _coerce(raw: str, kind: str):
    raw = raw.strip()
    if kind == _TEXT:
        return raw
    try:
        return int(float(raw)) if kind == _INT else float(raw)
    except ValueError:
        return raw  # leave malformed values as-is rather than crash


@dataclass
class FieldSchema:
    key: str
    header: str
    unit: Optional[str]
    kind: str
    group: str  # "board" or "j4".."j9"


@dataclass
class PacketSchema:
    delimiter: str
    fields: list = field(default_factory=list)

    @property
    def keys(self):
        return [f.key for f in self.fields]

    def to_dict(self) -> dict:
        return {"delimiter": self.delimiter, "fields": [asdict(f) for f in self.fields]}

    @classmethod
    def from_dict(cls, data: dict) -> "PacketSchema":
        return cls(delimiter=data["delimiter"], fields=[FieldSchema(**f) for f in data["fields"]])


def save_schema(schema: PacketSchema, path: str):
    """Cache a fetched schema to disk. /CBD and /CJ4-/CJ9 (the commands
    fetch_configuration() sends) only work over J1 (manual section 3.3.1) --
    the X4's own iMet-XS software treats J3 as data-view only, with no
    config querying over it (section 2.4.1). So a wireless session over an
    RFD900x radio on J3 has no command channel back to the board and can't
    fetch its own schema; it has to reuse one captured over a direct J1/USB
    connection instead.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(schema.to_dict(), indent=2))


def load_schema(path: str) -> PacketSchema:
    return PacketSchema.from_dict(json.loads(Path(path).read_text()))


# --------------------------------------------------------------------------- #
#  Port auto-detection
# --------------------------------------------------------------------------- #

def find_imet_x4_port() -> Optional[str]:
    """Best-effort auto-detect of the X4's J1 USB serial port.

    The X4 enumerates through an FTDI USB-serial chip (manual section 2.2.1),
    so we look for FTDI's USB vendor ID first, falling back to a
    description match on "USB Serial".
    """
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid == 0x0403:  # FTDI
            return p.device
    for p in ports:
        if "usb serial" in (p.description or "").lower():
            return p.device
    return None


# --------------------------------------------------------------------------- #
#  Reader
# --------------------------------------------------------------------------- #

class IMetX4SerialReader:
    """Connects to an iMet-X4 Hub's J1 port, learns its live column layout,
    and parses each incoming line into a dict of named variables.
    """

    def __init__(self, port: str, baud: int = DEFAULT_BAUD, data_queue=None,
                 read_timeout: float = 1.0,
                 reconnect_initial_delay: float = RECONNECT_INITIAL_DELAY_S,
                 reconnect_max_delay: float = RECONNECT_MAX_DELAY_S):
        self.port_name = port
        self.baud = baud
        self.data_queue = data_queue
        self.read_timeout = read_timeout
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay
        self._ser: Optional[serial.Serial] = None
        self.schema: Optional[PacketSchema] = None
        self.latest: Optional[dict] = None
        self._stop_event = threading.Event()

    # -- connection -------------------------------------------------------- #

    def connect(self):
        self._ser = serial.Serial(self.port_name, self.baud, timeout=self.read_timeout)
        return self

    def close(self):
        self.stop()
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass

    def stop(self):
        self._stop_event.set()

    def _reconnect(self):
        """Keep retrying to reopen the serial connection (exponential
        backoff, uncapped attempts) after the link drops mid-session --
        a USB hiccup or radio dropout, not the initial connect. The
        packet schema and any FlightLogger session stay exactly as they
        were; only the port handle is reopened, so an in-progress flight
        log isn't fragmented by a brief glitch.
        """
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass

        delay = self.reconnect_initial_delay
        attempt = 0
        while not self._stop_event.is_set():
            attempt += 1
            try:
                self._ser = serial.Serial(self.port_name, self.baud, timeout=self.read_timeout)
                print(f"[iMet-X4] Reconnected to {self.port_name} after {attempt} attempt(s).")
                return
            except (serial.SerialException, OSError):
                # Windows can reassign a different COM number when the
                # FTDI device re-enumerates; fall back to re-detecting it.
                fallback = find_imet_x4_port()
                if fallback and fallback != self.port_name:
                    print(f"[iMet-X4] {self.port_name} not available; trying {fallback} instead.")
                    self.port_name = fallback
                print(f"[iMet-X4] Connection lost. Retry {attempt} in {delay:.0f}s...")
                if self._stop_event.wait(delay):
                    return  # stop() was called while waiting
                delay = min(delay * 2, self.reconnect_max_delay)

    # -- configuration polling --------------------------------------------- #

    def _query(self, cmd: str, timeout_s: float = 3.0) -> str:
        """Send /<cmd>?\\r\\n and return the reply body after "<cmd>=".

        Data publication may still be running while we do this, so
        replies can arrive interleaved with ordinary data lines -- those
        are simply skipped until the matching reply (or the timeout) shows up.
        """
        prefix = f"{cmd}="
        self._ser.write(f"/{cmd}?\r\n".encode("ascii"))
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = self._ser.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if line.startswith(prefix):
                return line[len(prefix):]
        raise TimeoutError(f"No response to /{cmd}? within {timeout_s}s")

    def fetch_configuration(self) -> PacketSchema:
        """Poll /CBD? and /CJ4?-/CJ9? and build the packet schema (the
        live equivalent of clicking "Fetch" in iMet-XS)."""
        cbd_body = self._query("CBD")
        delimiter, board_mask = self._parse_cbd(cbd_body)

        fields = [
            FieldSchema(key, header, unit, kind, "board")
            for i, (key, header, unit, kind) in enumerate(BOARD_FIELDS)
            if not _bit_removed(board_mask, len(BOARD_FIELDS), i)
        ]

        for port_num in (4, 5, 6, 7, 8, 9):
            cj_body = self._query(f"CJ{port_num}")
            fields.extend(self._parse_cj(port_num, cj_body))

        self.schema = PacketSchema(delimiter=delimiter, fields=fields)
        return self.schema

    @staticmethod
    def _parse_cbd(body: str):
        """body looks like "0,30720" -- format char, delimiter char, bitmask."""
        _format_char, delimiter, mask_str = body[0], body[1], body[2:]
        return delimiter, int(mask_str)

    @staticmethod
    def _parse_cj(port_num: int, body: str) -> list:
        parts = [p.strip() for p in body.split(",")]
        peripheral_id = int(parts[0])
        prefix = f"j{port_num}_"

        if peripheral_id == PERIPHERAL_NO_SENSOR:
            return []

        if peripheral_id == PERIPHERAL_TEMPERATURE:
            # id, serial, coeff1..coeffN, bitmask
            bitmask = int(parts[-1])
            base = [
                (prefix + "serial_number", "Sensor Serial Number", None, _INT),
                (prefix + "temperature_c", "Temperature", "C", _FLOAT),
            ]
            return [
                FieldSchema(k, h, u, kind, f"j{port_num}")
                for i, (k, h, u, kind) in enumerate(base)
                if not _bit_removed(bitmask, len(base), i)
            ]

        if peripheral_id == PERIPHERAL_HUMIDITY:
            # id, i2c address, bitmask
            bitmask = int(parts[-1])
            base = [
                (prefix + "relative_humidity_pct", "Relative Humidity", "%", _FLOAT),
                (prefix + "humidity_temp_c", "Humidity Temperature", "C", _FLOAT),
            ]
            return [
                FieldSchema(k, h, u, kind, f"j{port_num}")
                for i, (k, h, u, kind) in enumerate(base)
                if not _bit_removed(bitmask, len(base), i)
            ]

        if peripheral_id == PERIPHERAL_SERIAL_FORWARD:
            # id, baud, column count -- raw passthrough, no documented semantics
            num_cols = int(parts[-1])
            return [
                FieldSchema(f"{prefix}col{n}", f"J{port_num} Column {n}", None, _TEXT, f"j{port_num}")
                for n in range(1, num_cols + 1)
            ]

        # Reserved/undocumented peripheral ID: keep the line parseable by
        # stashing whatever the board sent, without inventing field meaning.
        label = _RESERVED_PERIPHERALS.get(peripheral_id, f"peripheral {peripheral_id}")
        return [FieldSchema(f"{prefix}raw", f"J{port_num} {label} (raw)", None, _TEXT, f"j{port_num}")]

    # -- parsing ------------------------------------------------------------ #

    def parse_line(self, raw_line: str) -> dict:
        if self.schema is None:
            raise RuntimeError("Call fetch_configuration() before parsing data lines.")
        parts = raw_line.strip().split(self.schema.delimiter)
        if len(parts) != len(self.schema.fields):
            raise ValueError(
                f"Expected {len(self.schema.fields)} fields, got {len(parts)}: {raw_line!r}"
            )
        return {
            f.key: _coerce(value, f.kind)
            for f, value in zip(self.schema.fields, parts)
        }

    # -- streaming ----------------------------------------------------------- #

    def start(self, on_reading: Optional[Callable[[dict], None]] = None):
        """Blocking read loop. Parses each line and, if configured, pushes a
        Dashboard-ready reading onto self.data_queue. If the serial link
        drops mid-session, reconnects automatically (see _reconnect) and
        keeps going rather than ending the session."""
        if self.schema is None:
            self.fetch_configuration()
        self._stop_event.clear()
        while not self._stop_event.is_set():
            try:
                raw = self._ser.readline()
            except (serial.SerialException, OSError):
                print("[iMet-X4] Serial connection dropped.")
                self._reconnect()
                continue
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line or "=" in line.split(self.schema.delimiter, 1)[0]:
                continue  # command echoes/replies, not a data line
            try:
                reading = self.parse_line(line)
            except ValueError:
                continue  # partial line (e.g. right after connecting/reconnecting)
            self.latest = reading
            if on_reading is not None:
                on_reading(reading)
            if self.data_queue is not None:
                self.data_queue.put(self.to_canonical_row(reading))

    # -- Dashboard adapter ---------------------------------------------------- #

    @staticmethod
    def _valid(value):
        if value is None:
            return None
        if isinstance(value, (int, float)) and value == INVALID_SENTINEL:
            return None
        return value

    def to_canonical_row(self, r: dict) -> dict:
        """Reduce a fully-parsed reading down to the simple keys
        framework.dashboard.Dashboard expects (see its REQUIRED_COLS /
        OPTIONAL_COLS). External J4/J5/J8/J9 sensors are preferred over the
        onboard board sensor for temperature/humidity, since the manual
        states the onboard humidity sensor is for board health only.

        This is also the schema framework.flight_log.FlightLogger writes
        for the canonical (plot/stats-ready) CSV of a live session.
        """
        date, utc_time = r.get("date"), r.get("utc_time")
        timestamp = None
        if date and utc_time and "9999" not in date and "9999" not in utc_time:
            for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%y %H:%M:%S"):
                try:
                    timestamp = datetime.strptime(f"{date} {utc_time}", fmt)
                    break
                except ValueError:
                    continue
        if timestamp is None:
            timestamp = datetime.now()

        ext_temps = [
            self._valid(v) for k, v in r.items()
            if k.endswith("temperature_c") and k.startswith("j") and self._valid(v) is not None
        ]
        ext_hums = [
            self._valid(v) for k, v in r.items()
            if k.endswith("relative_humidity_pct") and self._valid(v) is not None
        ]

        return {
            "timestamp": timestamp,
            "temperature": (sum(ext_temps) / len(ext_temps)) if ext_temps else self._valid(r.get("pressure_temp_c")),
            "humidity": (sum(ext_hums) / len(ext_hums)) if ext_hums else self._valid(r.get("board_humidity_pct")),
            "pressure": self._valid(r.get("pressure_hpa")),
            "latitude": self._valid(r.get("latitude_deg")),
            "longitude": self._valid(r.get("longitude_deg")),
            "altitude": self._valid(r.get("altitude_m")),
        }
