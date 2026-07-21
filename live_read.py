"""
Stream and parse live data from an iMet-X4 Hub over serial (J1, 115200 baud).

Usage:
    python live_read.py                 # auto-detect port, print parsed readings
    python live_read.py --port COM5     # use a specific port
    python live_read.py --dashboard     # stream into the live Dash dashboard
"""

import argparse
import queue
import sys
import threading

from framework.serial_reader import DEFAULT_BAUD, IMetX4SerialReader, find_imet_x4_port


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

    if args.dashboard:
        from framework.dashboard import Dashboard

        data_queue = queue.Queue()
        reader.data_queue = data_queue
        threading.Thread(target=reader.start, daemon=True).start()
        Dashboard(data_queue=data_queue).run()
    else:
        try:
            reader.start(on_reading=print)
        except KeyboardInterrupt:
            pass
        finally:
            reader.close()


if __name__ == "__main__":
    main()
