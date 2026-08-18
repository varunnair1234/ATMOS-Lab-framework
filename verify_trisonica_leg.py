"""
Bench-test helper for the Trisonica leg (see the build walkthrough) --
point this at the Teensy's own USB serial port (standing in for the radio
link, which isn't wired up yet at this stage) and confirm $TSM lines are
arriving and parsing into canonical rows correctly, before adding the
radio as an extra variable.

Usage:
    python verify_trisonica_leg.py --port COM7
    python verify_trisonica_leg.py --port /dev/ttyACM0 --baud 57600

Expects the Teensy running teensy_firmware/relay.ino with just the LI-570
leg wired in (ATMOS 22 and iMet-X4 lines, if the firmware is also sending
those, are printed too but not specially validated by this script).
"""

import argparse
import sys

import serial

from framework.radio_link_reader import RadioLinkReader, UnknownTagError

REQUIRED_FIELDS = ["wind_speed", "wind_direction"]  # bare minimum to call this leg "working"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", required=True, help="Teensy's own USB serial port.")
    parser.add_argument("--baud", type=int, default=57600, help="Match RADIO_BAUD in relay.ino (default 57600).")
    parser.add_argument("--count", type=int, default=5, help="Stop after this many good $TSM lines (default 5).")
    args = parser.parse_args()

    reader = RadioLinkReader(port=None)  # not used for its own connection, just for parse_line()

    print(f"Opening {args.port} @ {args.baud} baud... (Ctrl+C to stop)")
    ser = serial.Serial(args.port, args.baud, timeout=2.0)

    seen = 0
    try:
        while seen < args.count:
            raw = ser.readline()
            if not raw:
                print("  (no data in the last 2s -- check wiring/baud rate)")
                continue
            line = raw.decode("ascii", errors="replace").strip()
            if not line:
                continue

            print(f"raw:  {line}")
            try:
                tag, row = reader.parse_line(line)
            except UnknownTagError as e:
                print(f"  -> skipped (unrecognized tag): {e}")
                continue
            except ValueError as e:
                print(f"  -> skipped (parse error, may be a torn line): {e}")
                continue

            if tag != "TSM":
                print(f"  -> parsed OK but tag={tag!r}, not TSM -- not counted")
                continue

            missing = [f for f in REQUIRED_FIELDS if row.get(f) is None]
            if missing:
                print(f"  -> parsed but missing {missing} -- check which fields are enabled on the LI-570")
                continue

            print(f"  -> OK: wind_speed={row['wind_speed']}, wind_direction={row['wind_direction']}, "
                  f"temperature={row.get('temperature')}")
            seen += 1

    except KeyboardInterrupt:
        pass
    finally:
        ser.close()

    if seen >= args.count:
        print(f"\n{seen} good $TSM readings parsed -- this leg is working.")
    else:
        print(f"\nOnly {seen}/{args.count} good readings before stopping.")
        sys.exit(1)


if __name__ == "__main__":
    main()
