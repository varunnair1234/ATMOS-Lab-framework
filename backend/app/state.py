"""
Shared in-memory ring buffer + live data source.

DATA_SOURCE controls where readings come from:
  - "serial"    (default) — real iMet-X4 over a USB/serial connection.
                 Requires IMET_SERIAL_PORT to be set. If it's unset, or the
                 device isn't attached/responding, the buffer simply stays
                 empty and `status()` reports why — no synthetic numbers
                 are ever generated in this mode.
  - "simulator" — synthetic flight data, for local UI development only.
                 Opt in explicitly with DATA_SOURCE=simulator.

Note: a serial port is a physical connection to whatever machine the
process is running on. This only makes sense run locally, next to the
actual hardware — a cloud deployment (Render, etc.) has no USB port to
read, so DATA_SOURCE=serial there will just report "not connected" forever.

Single-process, single-worker state: the FastAPI app must run with
--workers 1 since this buffer isn't shared across processes.
"""

import asyncio
import os
import queue as pyqueue
from collections import deque
from typing import Optional

import pandas as pd

from framework.figures import RING_BUFFER_SIZE

DATA_SOURCE = os.environ.get("DATA_SOURCE", "serial").lower()  # "serial" | "simulator"
SERIAL_PORT = os.environ.get("IMET_SERIAL_PORT")  # e.g. /dev/ttyUSB0, /dev/tty.usbserial-XXXX, COM3
SERIAL_BAUD = int(os.environ.get("IMET_BAUD_RATE", "115200"))
REFRESH_INTERVAL_SECONDS = float(os.environ.get("REFRESH_INTERVAL_SECONDS", "1"))
SEED_POINTS = int(os.environ.get("SEED_POINTS", "120"))  # simulator mode only

buffer: deque = deque(maxlen=RING_BUFFER_SIZE)

_serial_reader = None  # framework.serial_reader.SerialReader, once started
_simulator = None      # framework.simulate.FlightSimulator, only in simulator mode
_task: Optional[asyncio.Task] = None


async def _run_simulator():
    from framework.simulate import FlightSimulator

    global _simulator
    _simulator = FlightSimulator()
    for _ in range(SEED_POINTS):
        buffer.append(_simulator.step())
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
        buffer.append(_simulator.step())


async def _run_serial():
    global _serial_reader

    if not SERIAL_PORT:
        # Nothing configured. Leave the buffer empty forever rather than
        # falling back to fake data — status() will explain why.
        return

    from framework.serial_reader import SerialReader

    q: pyqueue.Queue = pyqueue.Queue()
    _serial_reader = SerialReader(SERIAL_PORT, SERIAL_BAUD, out_queue=q)
    _serial_reader.start()

    while True:
        await asyncio.sleep(0.25)
        while True:
            try:
                buffer.append(q.get_nowait())
            except pyqueue.Empty:
                break


def startup():
    global _task
    if _task is not None:
        return
    if DATA_SOURCE == "simulator":
        _task = asyncio.get_event_loop().create_task(_run_simulator())
    else:
        _task = asyncio.get_event_loop().create_task(_run_serial())


def shutdown():
    global _task
    if _serial_reader is not None:
        _serial_reader.stop()
    if _task is not None:
        _task.cancel()
        _task = None


def snapshot() -> pd.DataFrame:
    return pd.DataFrame(list(buffer))


def status() -> dict:
    """Report where data is (or isn't) coming from, for the frontend to
    show an honest 'waiting for device' state instead of guessing."""
    if DATA_SOURCE == "simulator":
        return {"source": "simulator", "connected": True, "error": None, "port": None}

    if not SERIAL_PORT:
        return {
            "source": "serial",
            "connected": False,
            "error": "IMET_SERIAL_PORT is not set — no device configured.",
            "port": None,
        }

    if _serial_reader is None:
        return {"source": "serial", "connected": False, "error": "Starting…", "port": SERIAL_PORT}

    return {
        "source": "serial",
        "connected": _serial_reader.connected,
        "error": _serial_reader.last_error,
        "port": _serial_reader.port,
    }
