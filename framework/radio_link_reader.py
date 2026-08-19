"""
Ground-station side of the Teensy relay: reads ONE serial port -- the base
station's radio receiver, standing in for what used to be three separate
sensor connections -- demuxes tagged lines by source, parses each with the
SAME parsing code the direct-USB readers use, and fuses them into one
canonical row per framework.multi_sensor.SensorHub's precedence rules.

Expected wire format (each line, as relayed by the Teensy -- see
teensy_firmware/relay.ino): a short tag, a space, then the tagged source's
own raw line, UNMODIFIED:

    $A22 +1.23+180+2.05+22.40+0.30-0.10
    $TSM S 05.2 D 112 U -01.9 V 04.7 W 01.1 T 22.6
    $X4 <the X4's own CSV line, exactly as it comes off its TX pin>

Nothing about this format is standardized anywhere -- it's this repo's own
convention, chosen to need the least Teensy-side logic possible (tag +
forward, no on-device parsing). If your firmware ends up framing things
differently (checksums, binary framing, etc.), update parse_line() to match
-- the rest of this module (demux routing, fusion, canonical output)
doesn't depend on the exact wire format.

Why demux-on-the-ground instead of fuse-on-the-Teensy: keeps the embedded
side dumb (easier to get right and to debug in the field) and reuses the
same Python parsing/fusion code this repo already has for the direct-USB
case, rather than writing (and maintaining) the parsing logic twice in two
languages.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Callable, Optional

import serial
from serial.tools import list_ports

from framework.atmos22_reader import ATMOS22SDI12Reader
from framework.trisonica_reader import TrisonicaMiniReader
from framework.serial_reader import IMetX4SerialReader, PacketSchema

DEFAULT_BAUD = 57600  # placeholder -- match whatever the ground radio receiver's own serial port runs at

RECONNECT_INITIAL_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 15.0

# Precedence order for overlapping canonical fields (e.g. wind_speed from
# both $A22 and $TSM) -- same semantics as SensorHub's `sources` dict
# ordering. First-listed tag wins when it has a non-None value.
DEFAULT_TAG_PRECEDENCE = ["A22", "TSM", "X4"]


class UnknownTagError(ValueError):
    """A line arrived with a tag this reader doesn't know how to parse --
    surfaced distinctly from a plain parse failure so a caller can tell
    "the Teensy firmware and this reader have drifted out of sync" apart
    from "one bad/torn line came through", which is expected occasionally
    on a radio link and not worth alarming about."""


class RadioLinkReader:
    """
    Parameters
    ----------
    port : str
        Serial port for the base station's radio receiver.
    x4_schema_path : str, optional
        Path to a schema JSON file saved by capture_x4_schema.py. Required
        if you want $X4 lines parsed -- without it, $X4 lines are skipped
        (with a warning) rather than raising, since a field crew forgetting
        this file shouldn't take down wind/met parsing too.
    tag_precedence : list[str], optional
        Overlapping-field precedence order, see DEFAULT_TAG_PRECEDENCE.
    data_queue : queue.Queue, optional
        Thread-safe queue that receives merged canonical rows.
    """

    def __init__(
        self,
        port: Optional[str],
        baud: int = DEFAULT_BAUD,
        x4_schema_path: Optional[str] = None,
        tag_precedence: Optional[list] = None,
        data_queue=None,
        read_timeout: float = 1.0,
        reconnect_initial_delay: float = RECONNECT_INITIAL_DELAY_S,
        reconnect_max_delay: float = RECONNECT_MAX_DELAY_S,
    ):
        self.port_name = port
        self.baud = baud
        self.tag_precedence = tag_precedence or list(DEFAULT_TAG_PRECEDENCE)
        self.data_queue = data_queue
        self.read_timeout = read_timeout
        self.reconnect_initial_delay = reconnect_initial_delay
        self.reconnect_max_delay = reconnect_max_delay

        self._x4_reader: Optional[IMetX4SerialReader] = None
        if x4_schema_path is not None:
            with open(x4_schema_path) as fh:
                schema = PacketSchema.from_dict(json.load(fh))
            self._x4_reader = IMetX4SerialReader(port=None)
            self._x4_reader.schema = schema

        self._ser: Optional[serial.Serial] = None
        self._stop_event = threading.Event()
        self._last_by_tag: dict = {}
        self._lock = threading.Lock()

        self.latest: Optional[dict] = None
        self.connected = False
        self.last_error: Optional[str] = None

    # -- connection (same shape as the direct-USB readers) ------------------ #

    def connect(self):
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
                detected = find_radio_port()
                if detected is None:
                    self.last_error = "No radio receiver serial port found"
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
                print(f"[RadioLink] {verb} to {self.port_name} after {attempt} attempt(s).")
                return
            except (serial.SerialException, OSError) as e:
                self.last_error = str(e)
                print(f"[RadioLink] Connect attempt {attempt} failed ({e}); retrying in {delay:.0f}s...")
                if self._stop_event.wait(delay):
                    return
                delay = min(delay * 2, self.reconnect_max_delay)

    # -- demux + parse -------------------------------------------------------- #

    def parse_line(self, raw_line: str) -> tuple:
        """Returns (tag, canonical_row). Raises UnknownTagError for a tag
        this reader doesn't recognize, or ValueError for a recognized tag
        whose body didn't parse (torn/corrupted line -- routine on a radio
        link, caller should just skip and continue rather than treat it
        like a connection problem)."""
        tag, _, body = raw_line.strip().partition(" ")
        tag = tag.lstrip("$")

        if tag == "A22":
            reading = ATMOS22SDI12Reader.parse_data_reply(body)
            row = ATMOS22SDI12Reader.to_canonical_row(reading)
        elif tag == "TSM":
            reading = TrisonicaMiniReader.parse_line(body)
            row = TrisonicaMiniReader.to_canonical_row(reading)
        elif tag == "X4":
            if self._x4_reader is None:
                raise UnknownTagError(
                    "Got a $X4 line but no x4_schema_path was provided -- "
                    "run capture_x4_schema.py first and pass its output here."
                )
            reading = self._x4_reader.parse_line(body)
            row = self._x4_reader.to_canonical_row(reading)
        else:
            raise UnknownTagError(f"Unrecognized tag {tag!r} in line: {raw_line!r}")

        row["timestamp"] = datetime.now()
        return tag, row

    # -- fusion (same LOCF-merge semantics as SensorHub, single stream) ------ #

    def _merge_locked(self) -> dict:
        merged: dict = {}
        for tag in self.tag_precedence:
            for key, value in self._last_by_tag.get(tag, {}).items():
                if key == "timestamp":
                    continue
                if value is not None and merged.get(key) is None:
                    merged[key] = value
        merged["timestamp"] = max(
            (row.get("timestamp") for row in self._last_by_tag.values() if row.get("timestamp")),
            default=None,
        )
        return merged

    def to_raw_dict(self) -> dict:
        """Every tag's canonical fields, prefixed -- for a full-fidelity
        raw log alongside the merged row, same split as SensorHub.to_raw_dict()."""
        with self._lock:
            out = {}
            for tag, row in self._last_by_tag.items():
                for key, value in row.items():
                    out[f"{tag}_{key}"] = value
            return out

    # -- streaming ------------------------------------------------------------ #

    def start(self, on_reading: Optional[Callable] = None, on_ready: Optional[Callable] = None):
        """Blocking loop: connect (waiting/retrying indefinitely), then
        demux + fuse forever. on_ready() fires once ALL tags in
        tag_precedence have reported at least once -- same "don't say
        we're live until every source has actually spoken" semantics as
        SensorHub.start()."""
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
                print("[RadioLink] Serial connection dropped.")
                self.connected = False
                self.last_error = str(e)
                continue
            if not raw:
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue

            try:
                tag, row = self.parse_line(line)
            except UnknownTagError as e:
                print(f"[RadioLink] {e}")
                continue
            except ValueError:
                continue  # torn/corrupted line -- expected occasionally on a radio link

            with self._lock:
                self._last_by_tag[tag] = row
                merged = self._merge_locked()
                self.latest = merged
                all_ready = set(self.tag_precedence) <= set(self._last_by_tag)

            if on_reading is not None:
                on_reading(tag, row, merged)
            if self.data_queue is not None:
                self.data_queue.put(merged)
            if not ready_fired and all_ready and on_ready is not None:
                on_ready()
                ready_fired = True


def find_radio_port() -> Optional[str]:
    """Best-effort auto-detect -- matches common LoRa/SiK-style radio
    module descriptions. As with the other readers' auto-detect, unreliable
    with multiple USB-serial devices attached; pass port= explicitly for
    a real base station setup."""
    for p in list_ports.comports():
        desc = (p.description or "").lower()
        if any(s in desc for s in ("lora", "sik radio", "rfd900", "radio")):
            return p.device
    return None
