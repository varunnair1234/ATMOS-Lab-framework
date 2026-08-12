"""
Live serial ingestion for the iMet-X4 sensor hub.

Runs a background thread that reads ASCII sentences off the X4's
UART/USB-serial output and turns them into the same reading-dict shape
FlightSimulator.step() produces, so they drop straight into the shared
ring buffer / Dash / FastAPI dashboards unchanged: timestamp, temperature,
humidity, pressure, and optionally altitude, wind_speed, wind_direction,
latitude, longitude.

⚠️  `_parse_line()` is NOT verified against a real iMet-X4. InterMet does
not publish the X4's serial sentence format publicly — the datasheet only
confirms "Serial, UART TTL" at 1-5 Hz, not the field layout. This parser
assumes a common InterMet-style comma-separated ASCII sentence:

    <tag>,<ISO timestamp>,<pressure hPa>,<temperature C>,<humidity %RH>,<altitude m>[,<wind_speed m/s>[,<wind_dir deg>]]

If your unit's actual output differs, capture a few raw lines and fix the
indices in `_parse_line()` (or replace it entirely) to match. Easiest way
to capture raw lines once the device is connected:

    python3 -c "
    import serial
    s = serial.Serial('/dev/ttyUSB0', 115200, timeout=2)
    for _ in range(20):
        print(s.readline())
    "

(swap in your actual port — see SerialReader docstring below for how to
find it).
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("imet_serial")

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")


class SerialReader:
    """
    Background thread: opens `port` at `baudrate`, reads lines, parses
    them, and pushes reading dicts onto `out_queue` for `state.py` (or
    `Dashboard(data_queue=...)`) to drain.

    Auto-reconnects if the device is unplugged mid-session (rather than
    dying silently) and exposes `.connected` / `.last_error` so the API
    can report real device status to the frontend instead of fabricating
    data when nothing's actually attached.

    Finding the port:
      - macOS:  `ls /dev/tty.usb*` or `ls /dev/tty.SLAB*` (typically
        `/dev/tty.usbserial-XXXX` for FTDI, `/dev/tty.SLAB_USBtoUART`
        for CP210x — the X4 uses one of these families over USB)
      - Linux:  `ls /dev/ttyUSB*` or `dmesg | grep tty` after plugging in
      - Windows: Device Manager -> Ports (COM & LPT) -> look for the new
        COM port that appears when the X4 is plugged in
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 115200,
        out_queue: Optional[queue.Queue] = None,
        reconnect_interval: float = 3.0,
    ):
        self.port = port
        self.baudrate = baudrate
        self.queue = out_queue if out_queue is not None else queue.Queue()
        self.reconnect_interval = reconnect_interval

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.connected = False
        self.last_error: Optional[str] = None
        self.lines_parsed = 0
        self.lines_dropped = 0

    def start(self):
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="imet-serial-reader")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self):
        try:
            import serial  # pyserial — imported lazily so this module still
                            # loads fine in environments with no serial port
                            # (e.g. import-time checks, tests, cloud deploys)
        except ImportError:
            self.last_error = "pyserial is not installed (pip install pyserial)"
            logger.error(self.last_error)
            return

        while not self._stop.is_set():
            try:
                with serial.Serial(self.port, self.baudrate, timeout=1) as ser:
                    self.connected = True
                    self.last_error = None
                    logger.info("iMet-X4 connected on %s @ %d baud", self.port, self.baudrate)
                    while not self._stop.is_set():
                        raw = ser.readline()
                        if not raw:
                            continue  # read timeout, no data this second — normal
                        try:
                            line = raw.decode("ascii", errors="replace").strip()
                        except Exception:
                            continue
                        if not line:
                            continue
                        reading = _parse_line(line)
                        if reading is not None:
                            self.lines_parsed += 1
                            self.queue.put(reading)
                        else:
                            self.lines_dropped += 1
            except Exception as e:
                # Port missing, permission denied, device unplugged mid-flight, etc.
                self.connected = False
                self.last_error = str(e)
                logger.warning(
                    "iMet-X4 serial link down (%s); retrying in %.0fs",
                    e, self.reconnect_interval,
                )
                time.sleep(self.reconnect_interval)


def _parse_line(line: str) -> Optional[dict]:
    """Parse one ASCII sentence into a reading dict, or None if unparseable."""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < 6:
        return None

    try:
        timestamp = (
            datetime.fromisoformat(parts[1])
            if _ISO_RE.match(parts[1])
            else datetime.now(timezone.utc)
        )
        pressure = float(parts[2])
        temperature = float(parts[3])
        humidity = float(parts[4])
        altitude = float(parts[5])
    except (ValueError, IndexError):
        return None

    reading = {
        "timestamp": timestamp,
        "pressure": pressure,
        "temperature": temperature,
        "humidity": humidity,
        "altitude": altitude,
    }

    if len(parts) > 6:
        try:
            reading["wind_speed"] = float(parts[6])
        except ValueError:
            pass
    if len(parts) > 7:
        try:
            reading["wind_direction"] = float(parts[7])
        except ValueError:
            pass

    return reading
