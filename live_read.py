"""
Stream and parse live data from an iMet-X4 Hub over serial (J1, 115200 baud).

Every session is logged to flights/ as it runs -- a canonical CSV
(timestamp, temperature, humidity, pressure, latitude, longitude, altitude)
that drops straight into quickstart.py/build_docs.py via DATA_CSV, plus a
raw CSV with every field the board is currently configured to report.

Usage:
    python live_read.py                 # auto-detect port, print parsed readings
    python live_read.py --port COM5     # use a specific port
    python live_read.py --dashboard     # stream into the live Dash dashboard
"""

import argparse
import queue
import sys
import threading

from framework.flight_log import FlightLogger
from framework.serial_reader import DEFAULT_BAUD, IMetX4SerialReader, find_imet_x4_port

CANONICAL_FIELDS = ["timestamp", "temperature", "humidity", "pressure",
                     "latitude", "longitude", "altitude"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port (e.g. COM5). Auto-detected if omitted.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--dashboard", action="store_true",
                         help="Launch the live Dash dashboard instead of printing to the console.")
    args = parser.parse_args()

    port = args.port or find_imet_x4_port()
    if port is None:
        sys.exit("No iMet-X4 serial port found. Plug in the device or pass --port COMx.")

    reader = IMetX4SerialReader(port=port, baud=args.baud)
    reader.connect()
    print(f"Connected to {port} @ {args.baud} baud")

    schema = reader.fetch_configuration()
    print(f"Fetched configuration: {len(schema.fields)} fields, delimiter={schema.delimiter!r}")
    for f in schema.fields:
        unit = f" ({f.unit})" if f.unit else ""
        print(f"  [{f.group}] {f.key}: {f.header}{unit}")
    print()

    logger = FlightLogger(canonical_fields=CANONICAL_FIELDS, raw_fields=schema.keys)
    print(f"Logging canonical readings -> {logger.canonical_path}")
    print(f"Logging raw readings       -> {logger.raw_path}")
    print()

    def on_reading(raw: dict):
        logger.write(raw, reader.to_canonical_row(raw))
        print(raw)

    try:
        if args.dashboard:
            from framework.dashboard import Dashboard

            data_queue = queue.Queue()
            reader.data_queue = data_queue

            def on_reading_with_logging(raw: dict):
                logger.write(raw, reader.to_canonical_row(raw))

            threading.Thread(
                target=reader.start, kwargs={"on_reading": on_reading_with_logging}, daemon=True
            ).start()
            Dashboard(data_queue=data_queue).run()
        else:
            reader.start(on_reading=on_reading)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()
        logger.close()
        print(f"\nSession complete: {logger.rows_written} readings written.")
        print(f"  Canonical: {logger.canonical_path}")
        print(f"  Raw:       {logger.raw_path}")
        print(f"\nRun the full report with:")
        print(f"  DATA_CSV={logger.canonical_path} python quickstart.py")


if __name__ == "__main__":
    main()
