"""
Stream and parse live data from an iMet-X4 Hub over serial (J1, 115200 baud).

Every session is logged to flights/ as it runs -- a canonical CSV
(timestamp, temperature, humidity, pressure, latitude, longitude, altitude)
that drops straight into quickstart.py/build_docs.py via DATA_CSV, plus a
raw CSV with every field the board is currently configured to report.

Connecting never fails outright -- if no port is found (or --port is
wrong/the device isn't plugged in yet), it keeps retrying with backoff
rather than exiting, so `--dashboard` can be started before the device is
connected and will pick it up automatically once it appears.

Usage:
    python live_read.py                 # auto-detect port, print parsed readings
    python live_read.py --port COM5     # use a specific port
    python live_read.py --dashboard     # stream into the live Dash dashboard
"""

import argparse
import queue
import threading

from framework.flight_log import FlightLogger
from framework.serial_reader import DEFAULT_BAUD, IMetX4SerialReader

CANONICAL_FIELDS = ["timestamp", "temperature", "humidity", "pressure",
                     "latitude", "longitude", "altitude"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port (e.g. COM5). Auto-detected if omitted.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--dashboard", action="store_true",
                         help="Launch the live Dash dashboard instead of printing to the console.")
    args = parser.parse_args()

    # port=None is fine -- the reader will keep auto-detecting on every
    # retry (see IMetX4SerialReader._reconnect) rather than needing one to
    # already be present at startup.
    reader = IMetX4SerialReader(port=args.port, baud=args.baud)

    # FlightLogger needs the board's raw field list, which isn't known
    # until the device actually answers -- so it's created lazily, the
    # moment the schema is fetched (fires exactly once, even across
    # reconnects), rather than blocking startup on a connection that may
    # not exist yet.
    state = {"logger": None}

    def on_ready(schema):
        state["logger"] = FlightLogger(canonical_fields=CANONICAL_FIELDS, raw_fields=schema.keys)
        print(f"Logging canonical readings -> {state['logger'].canonical_path}")
        print(f"Logging raw readings       -> {state['logger'].raw_path}")
        for f in schema.fields:
            unit = f" ({f.unit})" if f.unit else ""
            print(f"  [{f.group}] {f.key}: {f.header}{unit}")
        print()

    def on_reading(raw: dict):
        logger = state["logger"]
        if logger is not None:
            logger.write(raw, reader.to_canonical_row(raw))
        if not args.dashboard:
            print(raw)

    try:
        if args.dashboard:
            from framework.dashboard import Dashboard

            data_queue = queue.Queue()
            reader.data_queue = data_queue

            # Runs in the background so the dashboard opens immediately
            # and shows a real "waiting for device" state (via reader=)
            # instead of blocking the whole script on the connection.
            threading.Thread(
                target=reader.start,
                kwargs={"on_reading": on_reading, "on_ready": on_ready},
                daemon=True,
            ).start()
            Dashboard(data_queue=data_queue, reader=reader).run()
        else:
            print("Waiting for iMet-X4… (Ctrl+C to stop)")
            reader.start(on_reading=on_reading, on_ready=on_ready)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
        logger = state["logger"]
        if logger is not None:
            logger.close()
            print(f"\nSession complete: {logger.rows_written} readings written.")
            print(f"  Canonical: {logger.canonical_path}")
            print(f"  Raw:       {logger.raw_path}")
            print(f"\nRun the full report with:")
            print(f"  DATA_CSV={logger.canonical_path} python quickstart.py")
        else:
            print("\nSession ended before a device ever connected — nothing logged.")


if __name__ == "__main__":
    main()
