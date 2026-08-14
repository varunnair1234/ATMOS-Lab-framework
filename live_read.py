"""
Stream and parse live data from an iMet-X4 Hub over serial (J1, 115200 baud).

Every session is logged to flights/ as it runs -- a canonical CSV
(timestamp, temperature, humidity, pressure, latitude, longitude, altitude)
that drops straight into quickstart.py/build_docs.py via DATA_CSV, plus a
raw CSV with every field the board is currently configured to report.

Usage:
    python live_read.py                       # auto-detect port, print parsed readings
    python live_read.py --port COM5           # use a specific port
    python live_read.py --dashboard           # stream into the live Dash dashboard
    python live_read.py --dashboard_wireless --port COM7
        # stream into the live dashboard over the Ultra Long Range Radio Modem
        # RFD 900x-US (J3), using the schema cached from the last direct
        # J1/USB session -- see the note on --dashboard_wireless below.
"""

import argparse
import queue
import sys
import threading

from framework.flight_log import FlightLogger
from framework.serial_reader import (
    DEFAULT_BAUD,
    IMetX4SerialReader,
    find_imet_x4_port,
    load_schema,
    save_schema,
)

CANONICAL_FIELDS = ["timestamp", "temperature", "humidity", "pressure",
                     "latitude", "longitude", "altitude"]
DEFAULT_SCHEMA_CACHE = "flights/last_schema.json"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--port", help="Serial port (e.g. COM5). Auto-detected if omitted "
                                        "(required for --dashboard_wireless).")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--dashboard", action="store_true",
                         help="Launch the live Dash dashboard instead of printing to the console.")
    parser.add_argument("--dashboard_wireless", action="store_true",
                         help="Like --dashboard, but for a link that only carries data, not "
                              "commands (e.g. an RFD900x radio on J3). Skips fetch_configuration() "
                              "-- which the board can't answer over J3, only J1 -- and instead "
                              "loads the schema cached from the last direct J1/USB session.")
    parser.add_argument("--schema-file", default=DEFAULT_SCHEMA_CACHE,
                         help=f"Where a fetched schema is cached (default: {DEFAULT_SCHEMA_CACHE}). "
                              "--dashboard_wireless loads it from here.")
    args = parser.parse_args()
    wireless = args.dashboard_wireless

    if wireless and not args.port:
        sys.exit(
            "--dashboard_wireless needs --port pointing at the radio's ground-side COM port "
            "explicitly -- auto-detection is tuned for finding the X4 itself over USB and isn't "
            "reliable if a radio adapter and a direct connection could both be present."
        )

    port = args.port or find_imet_x4_port()
    if port is None:
        sys.exit("No iMet-X4 serial port found. Plug in the device or pass --port COMx.")

    reader = IMetX4SerialReader(port=port, baud=args.baud)
    reader.connect()
    print(f"Connected to {port} @ {args.baud} baud")

    if wireless:
        try:
            schema = load_schema(args.schema_file)
        except FileNotFoundError:
            sys.exit(
                f"No cached configuration at {args.schema_file}. Connect the X4 directly over "
                f"USB first (python live_read.py --port <its J1 port>) to fetch and cache its "
                f"configuration, then retry over the radio link."
            )
        reader.schema = schema
        print(f"Loaded cached configuration from {args.schema_file} "
              f"(captured over a direct J1/USB session -- J3/radio has no command channel "
              f"back to the board, see manual section 3.3.1): "
              f"{len(schema.fields)} fields, delimiter={schema.delimiter!r}")
    else:
        schema = reader.fetch_configuration()
        save_schema(schema, args.schema_file)
        print(f"Fetched configuration: {len(schema.fields)} fields, delimiter={schema.delimiter!r}")
        print(f"Cached to {args.schema_file} for future --dashboard_wireless sessions")

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
        if args.dashboard or wireless:
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
