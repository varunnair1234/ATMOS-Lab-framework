"""
Ground-station capture: reads the base station's radio receiver, demuxes
the Teensy's tagged multi-sensor relay (see teensy_firmware/relay.ino and
framework/radio_link_reader.py), and logs/dashboards the fused result --
same downstream (FlightLogger, Dashboard) as live_read.py and
live_read_multi.py, just fed by one radio link instead of direct sensor
connections.

Run capture_x4_schema.py once beforehand (over the X4's own USB port) if
you want $X4 lines parsed -- see that script's docstring for why.

Usage:
    python live_read_radio.py --port COM8
    python live_read_radio.py --port COM8 --x4-schema framework/x4_schema.json
    python live_read_radio.py --port COM8 --x4-schema framework/x4_schema.json --dashboard
"""

import argparse
import queue

from framework.flight_log import FlightLogger
from framework.radio_link_reader import RadioLinkReader, DEFAULT_BAUD, DEFAULT_TAG_PRECEDENCE

CANONICAL_FIELDS = [
    "timestamp", "temperature", "humidity", "pressure",
    "latitude", "longitude", "altitude",
    "wind_speed", "wind_direction", "wind_gust",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", help="Base station radio receiver's serial port. Auto-detected if omitted.")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--x4-schema", help="Path saved by capture_x4_schema.py. $X4 lines are skipped without this.")
    parser.add_argument("--tag-precedence", nargs="+", default=DEFAULT_TAG_PRECEDENCE,
                         help=f"Overlapping-field precedence order (default: {DEFAULT_TAG_PRECEDENCE}).")
    parser.add_argument("--dashboard", action="store_true")
    args = parser.parse_args()

    reader = RadioLinkReader(
        port=args.port, baud=args.baud,
        x4_schema_path=args.x4_schema, tag_precedence=args.tag_precedence,
    )

    state = {"logger": None}

    def on_ready():
        raw_fields = sorted(reader.to_raw_dict().keys())
        state["logger"] = FlightLogger(canonical_fields=CANONICAL_FIELDS, raw_fields=raw_fields)
        print(f"\nAll expected sources reporting. Logging canonical readings -> {state['logger'].canonical_path}")
        print(f"Logging raw readings       -> {state['logger'].raw_path}\n")

    def on_reading(_tag, _row, merged):
        logger = state["logger"]
        if logger is not None:
            logger.write(reader.to_raw_dict(), merged)
        if not args.dashboard:
            print(merged)

    try:
        if args.dashboard:
            from framework.dashboard import Dashboard

            data_queue = queue.Queue()
            raw_log_queue = queue.Queue()
            reader.data_queue = data_queue
            import threading
            t = threading.Thread(
                target=reader.start,
                kwargs={"on_reading": on_reading, "on_ready": on_ready, "on_raw_line": raw_log_queue.put},
                daemon=True,
            )
            t.start()
            Dashboard(data_queue=data_queue, raw_log_queue=raw_log_queue).run()
        else:
            print("Waiting for the radio link… (Ctrl+C to stop)")
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
            print("\nSession ended before all expected sources ever reported — nothing logged.")


if __name__ == "__main__":
    main()
