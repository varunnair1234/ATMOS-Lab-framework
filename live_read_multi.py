"""
Stream and fuse live data from an iMet-X4 Hub, a METER ATMOS 22 (SDI-12),
and/or a Trisonica Mini -- any subset of the three -- into one session.

Each sensor you provide a port for is included; omit a sensor's --*-port
flag (and, for the X4, pass --no-x4) to leave it out entirely. Wind fields
prefer the ATMOS 22 over the Trisonica when both are present -- see
framework.multi_sensor.SensorHub's precedence-order docs to change that.

Like live_read.py, connecting never fails outright: each reader retries
with backoff until its device shows up, so --dashboard can be started
before any sensors are plugged in.

Usage:
    python live_read_multi.py --x4-port COM5 --atmos22-port COM6 --trisonica-port COM7
    python live_read_multi.py --x4-port COM5 --atmos22-port COM6 --dashboard
    python live_read_multi.py --no-x4 --trisonica-port /dev/ttyUSB1   # wind/met only, no X4
"""

import argparse
import queue
import threading

from framework.flight_log import FlightLogger
from framework.multi_sensor import SensorHub
from framework.serial_reader import DEFAULT_BAUD as X4_DEFAULT_BAUD, IMetX4SerialReader
from framework.atmos22_reader import ATMOS22SDI12Reader
from framework.trisonica_reader import DEFAULT_BAUD as TRISONICA_DEFAULT_BAUD, TrisonicaMiniReader

CANONICAL_FIELDS = [
    "timestamp", "temperature", "humidity", "pressure",
    "latitude", "longitude", "altitude",
    "wind_speed", "wind_direction", "wind_gust",
]


def build_sources(args) -> dict:
    """Order here is wind-field precedence (see SensorHub docstring):
    ATMOS 22 (dedicated anemometer) before Trisonica before the X4 (which
    doesn't report wind at all, so it never overrides anything)."""
    sources = {}

    if args.atmos22_port is not None or args.atmos22_auto:
        sources["atmos22"] = ATMOS22SDI12Reader(
            port=args.atmos22_port, address=args.atmos22_address,
        )

    if args.trisonica_port is not None or args.trisonica_auto:
        sources["trisonica"] = TrisonicaMiniReader(
            port=args.trisonica_port, baud=args.trisonica_baud,
        )

    if not args.no_x4:
        sources["imet_x4"] = IMetX4SerialReader(port=args.x4_port, baud=args.x4_baud)

    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("--x4-port", help="iMet-X4 serial port. Auto-detected if omitted.")
    parser.add_argument("--x4-baud", type=int, default=X4_DEFAULT_BAUD)
    parser.add_argument("--no-x4", action="store_true", help="Don't include the iMet-X4 in this session.")

    parser.add_argument("--atmos22-port", help="ATMOS 22 SDI-12 adapter serial port.")
    parser.add_argument("--atmos22-address", default="0", help="ATMOS 22 SDI-12 address (default '0').")
    parser.add_argument("--atmos22-auto", action="store_true",
                         help="Include an ATMOS 22 with auto-detected port (see atmos22_reader.find_sdi12_adapter_port).")

    parser.add_argument("--trisonica-port", help="Trisonica Mini serial port.")
    parser.add_argument("--trisonica-baud", type=int, default=TRISONICA_DEFAULT_BAUD)
    parser.add_argument("--trisonica-auto", action="store_true",
                         help="Include a Trisonica Mini with auto-detected port (see trisonica_reader.find_trisonica_port).")

    parser.add_argument("--dashboard", action="store_true",
                         help="Launch the live Dash dashboard instead of printing to the console.")
    args = parser.parse_args()

    sources = build_sources(args)
    if not sources:
        parser.error("No sensors selected -- pass at least one of --x4-port/--atmos22-port/"
                      "--atmos22-auto/--trisonica-port/--trisonica-auto, or drop --no-x4.")

    print("Session sources (wind precedence order): " + ", ".join(sources))

    hub = SensorHub(sources)

    # Same lazy-creation reasoning as live_read.py: FlightLogger needs to
    # know the raw field names, which for a fused session means "every
    # source's canonical fields, prefixed" -- known only once every source
    # has reported at least once (on_ready fires then, see SensorHub.start).
    state = {"logger": None}

    def on_ready():
        raw_fields = sorted(hub.to_raw_dict().keys())
        state["logger"] = FlightLogger(canonical_fields=CANONICAL_FIELDS, raw_fields=raw_fields)
        print(f"\nAll sources reporting. Logging canonical readings -> {state['logger'].canonical_path}")
        print(f"Logging raw readings       -> {state['logger'].raw_path}\n")

    def on_reading(_source_name, _raw_reading, merged_row):
        logger = state["logger"]
        if logger is not None:
            logger.write(hub.to_raw_dict(), merged_row)
        if not args.dashboard:
            print(merged_row)

    try:
        if args.dashboard:
            from framework.dashboard import Dashboard

            data_queue = queue.Queue()
            hub.data_queue = data_queue

            hub.start(on_reading=on_reading, on_ready=on_ready)
            # No single `reader=` to hand the dashboard for connection-status
            # text (there are several) -- it falls back to inferring status
            # from whether data has arrived, same as live-mode-without-reader
            # already does for a single sensor.
            Dashboard(data_queue=data_queue).run()
        else:
            print("Waiting for sensors… (Ctrl+C to stop)")
            hub.start(on_reading=on_reading, on_ready=on_ready)
            threading.Event().wait()  # start() launches threads and returns; block here instead
    except KeyboardInterrupt:
        pass
    finally:
        hub.close()
        logger = state["logger"]
        if logger is not None:
            logger.close()
            print(f"\nSession complete: {logger.rows_written} readings written.")
            print(f"  Canonical: {logger.canonical_path}")
            print(f"  Raw:       {logger.raw_path}")
            print(f"\nRun the full report with:")
            print(f"  DATA_CSV={logger.canonical_path} python quickstart.py")
        else:
            print("\nSession ended before every source ever reported — nothing logged.")


if __name__ == "__main__":
    main()
