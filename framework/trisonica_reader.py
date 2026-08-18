"""
Live serial ingestion for the Trisonica Mini (LI-COR LI-550, formerly
Anemoment) 3D ultrasonic anemometer.

Reference: LI-550 TriSonica Mini documentation (licor.com/products/trisonica),
and the field sensor's built-in `help` menu (connect with a terminal at the
sensor's configured baud and type `help` + Enter to see it firsthand).

The Trisonica streams one line per sample, terminated by \\r\\n, of
whitespace-separated `<KEY> <value>` pairs -- e.g.:

    S 00.08 S2 00.07 D 245 DV 033 U 00.06 V 00.03 W 00.05 T 21.4 C 346.68
    H 17.92 DP 03.68 P 1006.05 ...

Which keys actually appear is NOT fixed: they depend on which outputs are
enabled in the sensor's own config menu (`outputXXX` commands), so -- same
philosophy as framework.serial_reader's schema-adaptive parsing -- this
module doesn't hardcode a column list. It regex-parses whatever key/value
pairs are present on each line and lets to_canonical_row() pick out the
ones the rest of this repo knows how to plot, keeping everything else
available under its raw key for anyone who wants it.

Known key meanings (from the sensor's `help` menu):
    S    horizontal wind speed (m/s)          S2   alternate/2D speed (m/s)
    D    horizontal wind direction (deg)      DV   vertical wind angle (deg)
    U/V/W  orthogonal wind vector components (m/s)
    T    air temperature (C)                  C    compass heading (deg)
    H    relative humidity (%)                DP   dew point (C)
    P    pressure (hPa, if enabled)           AD   accumulated distance
    AX/AY/AZ  accelerometer (raw)             PI/RO pitch/roll (deg)
    MX/MY/MZ  magnetometer (raw)              MD/TD magnetic/true direction (deg)

Default serial settings are commonly 57600 8N1 out of the box, but the
sensor's own menu lets a user change both the baud and the output rate --
pass baud= to match whatever yours is configured for.
"""

from __future__ import annotations

import re
import threading
from datetime import datetime
from typing import Callable, Optional

import serial
from serial.tools import list_ports

DEFAULT_BAUD = 57600

RECONNECT_INITIAL_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 15.0

# key: run of letters (e.g. "S", "S2", "DV", "AX"); value: a signed
# int/float, possibly with no space before the next key (the sensor pads
# with spaces but doesn't guarantee exactly one, so this doesn't assume a
# fixed field width).
_KV_RE = re.compile(r"([A-Za-z]+\d*)\s*([+-]?\d+\.?\d*)")


def _coerce(raw: str) -> float:
    return float(raw)


class TrisonicaMiniReader:
    """Connects to a Trisonica Mini's serial output, parses each line into
    a dict of named variables, and reduces that down to the canonical
    schema the rest of this repo consumes.

    Parameters
    ----------
    port : str
        Serial device (e.g. "COM6", "/dev/ttyUSB1"). Auto-detected on each
        (re)connect attempt if None -- see find_trisonica_port(), though
        with multiple USB-serial devices attached (e.g. alongside an
        iMet-X4 or an SDI-12 adapter) explicit port= is more reliable.
    baud : int
        Must match the sensor's configured output baud (factory default is
        commonly 57600, but the sensor's own config menu can change it).
    data_queue : queue.Queue, optional
        Thread-safe queue that receives to_canonical_row() dicts, e.g. to
        feed framework.dashboard.Dashboard or a fused multi-sensor session
        (see framework.multi_sensor).
    """

    def __init__(
        self,
        port: Optional[str],
        baud: int = DEFAULT_BAUD,
        data_queue=None,
        read_timeout: float = 1.0,
        reconnect_initial_delay: float = RECONNECT_INITIAL_DELAY_S,
        reconnect_max_delay: float = RECONNECT_MAX_DELAY_S,
    ):
        self.port_name = port
        self.baud = baud
        self.data_queue = data_queue
        self.read_timeout = read_timeout
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay

        self._ser: Optional[serial.Serial] = None
        self.latest: Optional[dict] = None
        self._stop_event = threading.Event()

        # Same honest connection-state surface as IMetX4SerialReader /
        # ATMOS22SDI12Reader, for a dashboard/CLI to reflect real status.
        self.connected = False
        self.last_error: Optional[str] = None

    # -- connection ---------------------------------------------------- #

    def connect(self):
        """One-shot connect attempt -- raises immediately on failure. For a
        connection that waits/retries until the device shows up, use
        start() instead."""
        self._ser = serial.Serial(self.port_name, self.baud, timeout=self.read_timeout)
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
        """Same retry-with-backoff shape as IMetX4SerialReader._reconnect."""
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
                detected = find_trisonica_port()
                if detected is None:
                    self.last_error = "No Trisonica Mini serial port found"
                    if self._stop_event.wait(delay):
                        return
                    delay = min(delay * 2, self.reconnect_max_delay)
                    continue
                self.port_name = detected

            try:
                self._ser = serial.Serial(self.port_name, self.baud, timeout=self.read_timeout)
                self.connected = True
                self.last_error = None
                verb = "Connected" if is_initial else "Reconnected"
                print(f"[Trisonica] {verb} to {self.port_name} after {attempt} attempt(s).")
                return
            except (serial.SerialException, OSError) as e:
                self.last_error = str(e)
                fallback = find_trisonica_port()
                if fallback and fallback != self.port_name:
                    print(f"[Trisonica] {self.port_name} not available; trying {fallback} instead.")
                    self.port_name = fallback
                print(f"[Trisonica] Connect attempt {attempt} failed ({e}); retrying in {delay:.0f}s...")
                if self._stop_event.wait(delay):
                    return
                delay = min(delay * 2, self.reconnect_max_delay)

    # -- parsing ------------------------------------------------------------ #

    @staticmethod
    def parse_line(raw_line: str) -> dict:
        """Parse one `<KEY> <value> <KEY> <value> ...` line. Returns
        whatever key/value pairs are present -- see module docstring for
        why the set isn't fixed. Raises ValueError on a line with no
        recognizable pairs at all (e.g. a boot banner or menu echo).

        Pure function, no serial I/O -- reusable by anything that already
        HAS a raw line (e.g. framework.radio_link_reader, demuxing a tagged
        line relayed from an LI-570's tapped TX line over a radio link)."""
        pairs = _KV_RE.findall(raw_line.strip())
        if not pairs:
            raise ValueError(f"No key/value pairs found: {raw_line!r}")
        return {key: _coerce(value) for key, value in pairs}

    # -- streaming ----------------------------------------------------------- #

    def start(
        self,
        on_reading: Optional[Callable[[dict], None]] = None,
        on_ready: Optional[Callable[[], None]] = None,
    ):
        """Blocking loop: connect (waiting/retrying indefinitely), then
        read and parse lines forever, reconnecting automatically if the
        link drops or the device goes away and comes back mid-session.

        on_ready() fires once, right after the first successfully parsed
        line -- the earliest point a caller knows real data is flowing (as
        opposed to just "the serial port opened").
        """
        self._stop_event.clear()
        ready_fired = False

        while not self._stop_event.is_set():
            if not self.connected:
                self._reconnect(is_initial=self.latest is None)
                if self._stop_event.is_set():
                    return

            try:
                raw = self._ser.readline()
            except (serial.SerialException, OSError) as e:
                print("[Trisonica] Serial connection dropped.")
                self.connected = False
                self.last_error = str(e)
                continue
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue
            try:
                reading = self.parse_line(line)
            except ValueError:
                continue  # boot banner, menu echo, or a torn line right after (re)connecting

            if not ready_fired and on_ready is not None:
                on_ready()
                ready_fired = True

            self.latest = reading
            if on_reading is not None:
                on_reading(reading)
            if self.data_queue is not None:
                self.data_queue.put(self.to_canonical_row(reading))

    # -- Dashboard adapter ---------------------------------------------------- #

    @staticmethod
    def to_canonical_row(r: dict) -> dict:
        """Reduce a parsed reading to the keys framework.dashboard.Dashboard
        and framework.flight_log.FlightLogger expect. Only the keys the
        sensor is currently configured to output will be present in `r`;
        missing ones come through as None rather than a KeyError, same as
        IMetX4SerialReader.to_canonical_row does for the X4's own optional
        columns.
        """
        return {
            "timestamp": datetime.now(),
            "wind_speed": r.get("S"),
            "wind_direction": r.get("D"),
            "temperature": r.get("T"),
            "humidity": r.get("H"),
            "pressure": r.get("P"),
            "wind_u": r.get("U"),
            "wind_v": r.get("V"),
            "wind_w": r.get("W"),
            "compass_heading": r.get("C"),
        }


# --------------------------------------------------------------------------- #
#  Port auto-detection
# --------------------------------------------------------------------------- #

def find_trisonica_port() -> Optional[str]:
    """Best-effort auto-detect of the Trisonica Mini's USB-serial port.

    There's no single documented VID/PID for the sensor across its various
    interface adapters (RS-232, RS-422, UART-3V, or LI-COR's 555USB
    adapter), so this matches a "trisonica" or "li-550" substring in the
    port description where the OS/driver provides one. If your adapter
    doesn't identify itself that way -- or you have more than one
    USB-serial device attached -- pass port= explicitly instead.
    """
    for p in list_ports.comports():
        desc = (p.description or "").lower()
        if "trisonica" in desc or "li-550" in desc or "li550" in desc:
            return p.device
    return None
