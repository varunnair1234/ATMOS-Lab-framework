"""
Shared in-memory ring buffer + background simulator task.

Single-process, single-worker state: the FastAPI app must run with
--workers 1 since this buffer isn't shared across processes.
"""

import asyncio
import os
from collections import deque
from typing import Optional

import pandas as pd

from framework.figures import RING_BUFFER_SIZE
from framework.simulate import FlightSimulator

REFRESH_INTERVAL_SECONDS = float(os.environ.get("REFRESH_INTERVAL_SECONDS", "1"))
SEED_POINTS = int(os.environ.get("SEED_POINTS", "120"))

buffer: deque = deque(maxlen=RING_BUFFER_SIZE)

_simulator = FlightSimulator()
_task: Optional[asyncio.Task] = None


async def _run():
    while True:
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)
        buffer.append(_simulator.step())


def startup():
    global _task
    if not buffer:
        for _ in range(SEED_POINTS):
            buffer.append(_simulator.step())
    if _task is None:
        _task = asyncio.get_event_loop().create_task(_run())


def shutdown():
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


def snapshot() -> pd.DataFrame:
    return pd.DataFrame(list(buffer))
