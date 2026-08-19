"""
Run this once before a flight, with the iMet-X4 connected directly over its
own USB port (not through the Teensy/radio relay), to capture its current
column schema and save it to a JSON file.

Why this is needed: framework.radio_link_reader parses the X4's data lines
after they've been passively tapped off its TX line and relayed by a Teensy
over a radio link -- a one-way path. IMetX4SerialReader.fetch_configuration()
(the normal way this repo learns the X4's column layout) is a two-way
handshake -- it sends `/CBD?`, `/CJ4?`...`/CJ9?` and reads the board's
replies -- which a one-way tap can't do. So: fetch it once here, over the
X4's own USB port where a real two-way link exists, save it, and have the
radio-side reader load it instead of asking the (unreachable, over radio)
board itself.

Re-run this whenever you change what's plugged into the X4's ports -- the
saved schema goes stale if the board's live config changes after you
captured it (a different sensor swapped into J4, say) but the radio relay
has no way to detect that on its own, since it's not the one asking.

Usage:
    python capture_x4_schema.py --port COM5
    python capture_x4_schema.py --port COM5 --out framework/x4_schema.json
"""

import argparse
import json

from framework.serial_reader import IMetX4SerialReader, DEFAULT_BAUD

DEFAULT_OUT = "framework/x4_schema.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="iMet-X4's own USB serial port (direct connection).")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--out", default=DEFAULT_OUT, help=f"Where to save the schema (default: {DEFAULT_OUT}).")
    args = parser.parse_args()

    reader = IMetX4SerialReader(port=args.port, baud=args.baud)
    print(f"Connecting to {args.port}...")
    reader.connect()

    print("Fetching configuration (/CBD?, /CJ4?-/CJ9?)...")
    schema = reader.fetch_configuration()
    reader.close()

    print(f"Captured {len(schema.fields)} fields, delimiter={schema.delimiter!r}:")
    for f in schema.fields:
        print(f"  {f.group:>5}  {f.key:<24} {f.header}" + (f" ({f.unit})" if f.unit else ""))

    with open(args.out, "w") as fh:
        json.dump(schema.to_dict(), fh, indent=2)
    print(f"\nSaved to {args.out}")
    print("Re-run this before any flight where the X4's port config has changed.")


if __name__ == "__main__":
    main()
