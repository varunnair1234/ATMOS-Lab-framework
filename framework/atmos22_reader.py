"""
Live SDI-12 ingestion for the METER ATMOS 22 ultrasonic anemometer.

Reference: ATMOS 22 Integrator's Guide, doc 18195 (METER Group).

Unlike the iMet-X4 (framework.serial_reader), the ATMOS 22 doesn't stream --
it's a request/response SDI-12 sensor. This module talks to it through a
USB-to-SDI-12 adapter that exposes a normal serial port (e.g. METER's
SDI-12-to-USB adapter, or an equivalent). The adapter itself handles the
SDI-12 bus electrical/timing details (12 V excitation, break/marking); from
Python it looks like an ordinary pyserial port running at SDI-12's line
rate of 1200 baud, 7 data bits, even parity, 1 stop bit (7E1) -- see manual
section "SDI-12 Electrical Specifications". If your adapter instead exposes
a virtual 9600 8N1 port and translates SDI-12 commands internally (common
on some cheaper USB-SDI-12 bridges), pass baud=9600, bytesize=EIGHTBITS,
parity=PARITY_NONE when constructing the reader to match it.

Measurement cycle (manual section 5, "SDI-12 Commands"):
  1. `a M!`  (or `aC!` for the CRC-checked variant) starts a measurement.
     The sensor replies `attt n<CR><LF>` -- ttt = seconds until the reading
     is ready, n = how many values it will return.
  2. Wait ttt seconds (or for the service request `a<CR><LF>` the sensor
     sends early, if the adapter surfaces it).
  3. `aD0!` retrieves the values: `a<value><value>...<CR><LF>`, each value
     prefixed with its own + or - sign (no delimiter between them).

The ATMOS 22's aM!/aC! response is documented to return exactly six values,
in this order (manual section 5.3, "Measurement Values"):
    wind_speed_ms, wind_direction_deg, gust_wind_speed_ms,
    air_temperature_c, x_tilt_deg, y_tilt_deg
wind_speed/direction are the average since the last query; gust is the max
instantaneous speed seen in that window (manual section 3, "Theory of
Operation"). -9999 is the sensor's general error code and -9990 is its
invalid-wind-measurement code (manual section 5) -- both are normalized to
None here rather than left as sentinels a chart would happily plot.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

import serial
from serial.tools import list_ports

# SDI-12 bus line rate (manual: "SDI-12 Electrical Specifications").
# Override at construction time if your adapter bridges to a different
# host-side baud (see module docstring).
DEFAULT_BAUD = 1200
DEFAULT_BYTESIZE = serial.SEVENBITS
DEFAULT_PARITY = serial.PARITY_EVEN
DEFAULT_STOPBITS = serial.STOPBITS_ONE

RECONNECT_INITIAL_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 15.0

# Order the ATMOS 22 returns values in for aM!/aC! + aD0! (see module docstring).
VALUE_FIELDS = [
    "wind_speed_ms",
    "wind_direction_deg",
    "gust_wind_speed_ms",
    "air_temperature_c",
    "x_tilt_deg",
    "y_tilt_deg",
]

ERROR_SENTINELS = {-9999.0, -9990.0}

# Matches one SDI-12 value: a leading sign, then digits/decimal point.
# SDI-12 values are always explicitly signed and packed with no delimiter
# (e.g. "+1.23-45.0+0.00"), so this is what splits them apart.
_VALUE_RE = re.compile(r"[+-]\d+(?:\.\d+)?")


def _valid(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return None if value in ERROR_SENTINELS else value


@dataclass
class MeasurementReply:
    wait_s: int
    n_values: int


class ATMOS22SDI12Reader:
    """Polls a METER ATMOS 22 over SDI-12 on a fixed interval and parses
    each reply into a dict of named variables.

    Parameters
    ----------
    port : str
        Serial device for the SDI-12 USB adapter (e.g. "COM5", "/dev/ttyUSB0").
        Auto-detected on each (re)connect attempt if None -- see
        find_sdi12_adapter_port().
    address : str
        SDI-12 sensor address, '0'-'9'/'A'-'Z'/'a'-'z'. ATMOS 22 ships at
        address '0'; pass the address you configured if you changed it
        (recommended when multiple SDI-12 sensors share the bus -- see
        the integrator's guide section on the aAb! address-change command).
    poll_interval_s : float
        Seconds between measurement requests. The ATMOS 22 itself samples
        internally every 10 s regardless of how often you ask (manual
        section 3), so polling faster than that just re-reads the same
        internal average/gust window rather than getting new information.
    use_crc : bool
        Use `aC!` (CRC-checked) instead of `aM!`. Recommended on longer or
        noisier cable runs; adds a bit of parsing overhead.
    data_queue : queue.Queue, optional
        Thread-safe queue that receives to_canonical_row() dicts, e.g. to
        feed framework.dashboard.Dashboard or a fused multi-sensor session
        (see framework.multi_sensor).
    """

    def __init__(
        self,
        port: Optional[str],
        address: str = "0",
        baud: int = DEFAULT_BAUD,
        bytesize: int = DEFAULT_BYTESIZE,
        parity: str = DEFAULT_PARITY,
        stopbits: float = DEFAULT_STOPBITS,
        poll_interval_s: float = 10.0,
        use_crc: bool = False,
        data_queue=None,
        read_timeout: float = 1.0,
        reconnect_initial_delay: float = RECONNECT_INITIAL_DELAY_S,
        reconnect_max_delay: float = RECONNECT_MAX_DELAY_S,
    ):
        self.port_name = port
        self.address = address
        self.baud = baud
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.poll_interval_s = poll_interval_s
        self.use_crc = use_crc
        self.data_queue = data_queue
        self.read_timeout = read_timeout
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay

        self._ser: Optional[serial.Serial] = None
        self.latest: Optional[dict] = None
        self._stop_event = threading.Event()

        # Mirrors IMetX4SerialReader's honest connection-state surface so a
        # dashboard/CLI can show real "waiting" / "connected" / "reconnecting"
        # status instead of guessing.
        self.connected = False
        self.last_error: Optional[str] = None

    # -- connection ---------------------------------------------------- #

    def connect(self):
        """One-shot connect attempt -- raises immediately on failure. For a
        connection that waits/retries until the adapter shows up, use
        start() instead."""
        self._ser = serial.Serial(
            self.port_name, self.baud, bytesize=self.bytesize,
            parity=self.parity, stopbits=self.stopbits, timeout=self.read_timeout,
        )
        self.connected = True
        self.last_error = None
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

    def _reconnect(self, is_initial: bool = False):
        """Same retry-with-backoff shape as IMetX4SerialReader._reconnect:
        keeps trying (uncapped attempts, exponential backoff) until it
        succeeds or stop() is called, re-running auto-detection each
        attempt when no explicit port was given."""
        self.connected = False
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None

        delay = self.reconnect_initial_delay
        attempt = 0
        while not self._stop_event.is_set():
            attempt += 1

            if self.port_name is None:
                detected = find_sdi12_adapter_port()
                if detected is None:
                    self.last_error = "No SDI-12 adapter port found"
                    if self._stop_event.wait(delay):
                        return
                    delay = min(delay * 2, self.reconnect_max_delay)
                    continue
                self.port_name = detected

            try:
                self._ser = serial.Serial(
                    self.port_name, self.baud, bytesize=self.bytesize,
                    parity=self.parity, stopbits=self.stopbits, timeout=self.read_timeout,
                )
                self.connected = True
                self.last_error = None
                verb = "Connected" if is_initial else "Reconnected"
                print(f"[ATMOS22] {verb} to {self.port_name} after {attempt} attempt(s).")
                return
            except (serial.SerialException, OSError) as e:
                self.last_error = str(e)
                fallback = find_sdi12_adapter_port()
                if fallback and fallback != self.port_name:
                    print(f"[ATMOS22] {self.port_name} not available; trying {fallback} instead.")
                    self.port_name = fallback
                print(f"[ATMOS22] Connect attempt {attempt} failed ({e}); retrying in {delay:.0f}s...")
                if self._stop_event.wait(delay):
                    return
                delay = min(delay * 2, self.reconnect_max_delay)

    # -- SDI-12 transport ------------------------------------------------ #

    def _send(self, command_body: str):
        """Send `<address><command_body>!` -- e.g. _send("M") -> "0M!"."""
        cmd = f"{self.address}{command_body}!\r\n"
        self._ser.reset_input_buffer()
        self._ser.write(cmd.encode("ascii"))

    def _read_reply(self, timeout_s: float) -> str:
        """Read one SDI-12 reply line, stripping the leading address echo."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = self._ser.readline()
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if line.startswith(self.address):
                return line[len(self.address):]
        raise TimeoutError(f"No SDI-12 reply within {timeout_s}s")

    def probe(self, timeout_s: float = 2.0) -> bool:
        """Send the `a!` acknowledge-active command. True if the sensor at
        self.address answers -- useful before start() to fail fast on a
        wrong address rather than only finding out after a full poll cycle.
        """
        self._send("")
        try:
            self._read_reply(timeout_s)
            return True
        except TimeoutError:
            return False

    def take_measurement(self, timeout_s: float = 15.0) -> dict:
        """Runs one full aM!/aC! -> wait -> aD0! cycle and returns the
        parsed value dict (raw field names, see VALUE_FIELDS). Blocking."""
        self._send("C" if self.use_crc else "M")
        reply = self._read_reply(timeout_s)
        wait_s, n_values = self._parse_measurement_reply(reply)

        # The sensor may send an early service request ("a<CR><LF>") once
        # ready; either way, don't wait past what it told us to.
        if wait_s > 0:
            time.sleep(wait_s)

        self._send("D0")
        data_reply = self._read_reply(timeout_s)
        return self.parse_data_reply(data_reply, n_values)

    @staticmethod
    def parse_data_reply(data_reply: str, n_values: int = len(VALUE_FIELDS)) -> dict:
        """Pure parsing half of take_measurement() -- no serial I/O. Takes
        the address-stripped aD0! reply body (e.g. "+1.23+180+2.05+22.40
        +0.30-0.10") and returns the named value dict.

        Split out so anything that already HAS that reply text -- not just
        this reader's own live SDI-12 session -- can reuse the same
        parsing. e.g. framework.radio_link_reader, for a Teensy (or other
        relay) that runs the SDI-12 M!/D0! cycle itself and forwards just
        the raw reply body over a radio link rather than raw SDI-12 timing.
        """
        values = [float(v) for v in _VALUE_RE.findall(data_reply)]
        if len(values) < n_values:
            raise ValueError(
                f"Expected {n_values} values, got {len(values)} from {data_reply!r}"
            )
        reading = dict(zip(VALUE_FIELDS, values[:n_values]))
        reading["_raw"] = data_reply
        return reading

    @staticmethod
    def _parse_measurement_reply(reply: str) -> tuple:
        """reply looks like "0053" (no CRC) meaning wait 005 s for 3 values."""
        digits = reply.strip()
        if len(digits) < 4:
            raise ValueError(f"Malformed measurement reply: {reply!r}")
        return int(digits[:3]), int(digits[3:])

    # -- streaming ---------------------------------------------------------- #

    def start(
        self,
        on_reading: Optional[Callable[[dict], None]] = None,
        on_ready: Optional[Callable[[], None]] = None,
    ):
        """Blocking loop: connect (waiting/retrying indefinitely -- never
        raises for "not connected yet"), then poll every poll_interval_s,
        automatically reconnecting on a dropped/failed link.

        on_ready() fires once, right after the first successful probe --
        the earliest point a caller knows a sensor actually answered at
        self.address (as opposed to just "the serial port opened").
        """
        self._stop_event.clear()
        ready_fired = False

        while not self._stop_event.is_set():
            if not self.connected:
                self._reconnect(is_initial=self.latest is None)
                if self._stop_event.is_set():
                    return

            try:
                reading = self.take_measurement()
            except (TimeoutError, ValueError, serial.SerialException, OSError) as e:
                # Ambiguous whether this is "wrong address" or "link
                # dropped" -- treat like a dropped connection and retry
                # from the top rather than spinning on a bad reply.
                self.connected = False
                self.last_error = f"Measurement failed: {e}"
                print(f"[ATMOS22] {self.last_error}")
                continue

            if not ready_fired and on_ready is not None:
                on_ready()
                ready_fired = True

            self.latest = reading
            if on_reading is not None:
                on_reading(reading)
            if self.data_queue is not None:
                self.data_queue.put(self.to_canonical_row(reading))

            if self._stop_event.wait(self.poll_interval_s):
                return

    # -- Dashboard adapter ---------------------------------------------------- #

    @staticmethod
    def to_canonical_row(r: dict) -> dict:
        """Reduce a parsed reading to the keys framework.dashboard.Dashboard
        and framework.flight_log.FlightLogger expect (see dashboard.py's
        REQUIRED_COLS/OPTIONAL_COLS, extended here with wind_gust/tilt for
        the framework.multi_sensor fusion path).
        """
        return {
            "timestamp": datetime.now(),
            "wind_speed": _valid(r.get("wind_speed_ms")),
            "wind_direction": _valid(r.get("wind_direction_deg")),
            "wind_gust": _valid(r.get("gust_wind_speed_ms")),
            "temperature": _valid(r.get("air_temperature_c")),
            "tilt_x": _valid(r.get("x_tilt_deg")),
            "tilt_y": _valid(r.get("y_tilt_deg")),
        }


# --------------------------------------------------------------------------- #
#  Port auto-detection
# --------------------------------------------------------------------------- #

def find_sdi12_adapter_port() -> Optional[str]:
    """Best-effort auto-detect of an SDI-12-to-USB adapter.

    Unlike find_imet_x4_port() (framework.serial_reader), there's no single
    documented VID/PID for "an SDI-12 adapter" -- different manufacturers'
    bridges enumerate differently, and several use the same FTDI/CP210x
    chips as other USB-serial devices you might also have plugged in (the
    iMet-X4 included). This match on a "SDI-12", "SDI12", or "SDI 12"
    substring in the port description, which most such adapters advertise;
    if yours doesn't, or you have more than one USB-serial device
    connected, pass port= explicitly rather than relying on this.
    """
    for p in list_ports.comports():
        desc = (p.description or "").lower()
        if "sdi-12" in desc or "sdi12" in desc or "sdi 12" in desc:
            return p.device
    return None
