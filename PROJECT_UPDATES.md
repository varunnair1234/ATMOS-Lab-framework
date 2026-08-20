# ATMOS-Lab-framework: Project Updates

Summary of everything added to the framework to support the METER ATMOS 22
and Trisonica Mini alongside the existing iMet-X4, plus hardware bring-up
progress to date. Organized for use as presentation source material.

---

## 1. Background

The framework originally supported one sensor: the **iMet-X4 Hub**
(radiosonde/UAS payload), read live over serial or analyzed post-flight
from a CSV, with a Dash-based live dashboard and a published static
snapshot on GitHub Pages.

This phase of work extended it to support two additional sensors:

- **METER ATMOS 22** — ultrasonic anemometer (wind speed, direction,
  gust, temperature, tilt), communicates over **SDI-12** (request/
  response, not streaming)
- **Trisonica Mini (LI-550)** — 3D ultrasonic anemometer (wind, temp,
  humidity, pressure, compass heading), streams continuously over serial

Goal: combine all three into one fused live data stream and dashboard,
usable either on the ground (bench testing) or airborne on a drone.

---

## 2. New sensor modules

| Module | What it does |
|---|---|
| `framework/atmos22_reader.py` | `ATMOS22SDI12Reader` — runs the SDI-12 `aM!`/`aC!` + `aD0!` measurement cycle against an ATMOS 22 over a direct USB-to-SDI-12 adapter. Parses six values per reading: wind speed, wind direction, gust, air temperature, X/Y tilt. Error sentinels (`-9999`, `-9990`) normalized to `None`. |
| `framework/trisonica_reader.py` | `TrisonicaMiniReader` — parses the Trisonica's continuous `<KEY> <value>` serial stream (wind, temperature, humidity, pressure, compass heading) using a regex that adapts to whatever fields are currently enabled on the sensor, rather than assuming a fixed column layout. |

Both readers reduce their raw readings to the same **canonical schema**
(`timestamp`, `temperature`, `humidity`, `pressure`, `wind_speed`,
`wind_direction`, `wind_gust`, …) that `Dashboard` and `FlightLogger`
already understood from the iMet-X4 — no changes needed to the plotting
or logging layers to support the new sensors.

---

## 3. Combining sensors — two architectures

### 3a. Direct-USB fusion (bench/ground testing)

`framework/multi_sensor.py`'s **`SensorHub`** runs multiple readers at
once (each on its own thread, own retry/reconnect logic) and fuses their
readings into one merged row using **last-observation-carried-forward**:
every time any sensor reports, the merged row keeps the most recent known
value for every field, with configurable source precedence for fields
more than one sensor can produce (e.g. wind speed from both the ATMOS 22
and the Trisonica).

Entry point: **`live_read_multi.py`**
```bash
python live_read_multi.py --x4-port COM5 --atmos22-port COM6 --trisonica-port COM7 --dashboard
```

### 3b. Radio relay (drone deployment)

For an actual airborne payload, a ground dashboard can't run on the
drone. Instead:

```
[Drone] iMet-X4 + ATMOS22 + Trisonica → Teensy 4.1 → radio transmitter
                                                            ⋮ wireless ⋮
[Ground] radio receiver → computer (USB) → live_read_radio.py → dashboard
```

- **`teensy_firmware/relay.ino`** — Teensy 4.1 firmware. Polls the ATMOS
  22 over bit-banged SDI-12, and relays lines tapped off the Trisonica's
  and iMet-X4's TX lines, each tagged by source (`$A22`, `$TSM`, `$X4`),
  over a shared radio UART. Does no parsing/fusion itself — that's left
  to the ground station, keeping the embedded side simple.
- **`framework/radio_link_reader.py`**'s **`RadioLinkReader`** — reads
  the ground radio receiver's one serial port, demuxes the tagged lines,
  routes each to the matching parser, and fuses them with the same
  precedence-order logic as `SensorHub`.
- **`capture_x4_schema.py`** — one-time pre-flight helper. The radio tap
  on the X4 is one-way, so it can't run the normal two-way handshake the
  X4 driver uses to learn its column layout; this captures that schema
  once (over the X4's own direct USB) and saves it to JSON for the radio
  path to load instead.
- Entry point: **`live_read_radio.py --port <radio receiver's port> --x4-schema framework/x4_schema.json --dashboard`**

**⚠️ Not yet flight-ready.** Two items in `relay.ino` are explicitly
flagged and unresolved:
1. The iMet-X4 tap point's actual voltage is unverified — needs a
   multimeter check before wiring to the Teensy (risk of damage if it
   isn't actually TTL-level).
2. What the shared radio module expects on its input is unconfirmed —
   firmware currently assumes raw passthrough at a placeholder baud rate.

---

## 4. Standalone ATMOS 22 bridge (no commercial adapter needed)

Rather than requiring a commercial USB-to-SDI-12 adapter, an
Arduino/Teensy running the **Arduino-SDI-12** library can run the SDI-12
conversation itself and stream the results over its own USB serial —
reusing the exact same parsing code and wire format as the full radio
relay, just without any radio hop.

- **`teensy_firmware/atmos22_x4_standalone/atmos22_x4_standalone.ino`** —
  polls the ATMOS 22 every ~10s and prints `$A22 <SDI-12 reply body>`
  over USB serial, and (as of this update) also relays a tapped iMet-X4
  TX line as `$X4 <line>` on the same USB connection — no Trisonica, no
  radio. Carries the same unresolved X4-tap-voltage caution as
  `relay.ino`, since it's the identical tap point.
- On the Python side, this board's own USB port is read exactly like a
  radio receiver: `python live_read_radio.py --port <board's port> --baud 115200`
- This is the path used in hardware bring-up so far (see §6).

---

## 5. Dashboard: Raw Serial debug panel

Added directly in response to a real "no data showing up" debugging
session (§6): a new **"Raw Serial" panel** in `Dashboard`, shown when
`live_read_radio.py --dashboard` is used, displaying the last 200 lines
received **verbatim** — including lines that failed to parse or carried
an unrecognized tag.

This matters because the normal live-data path only surfaces
successfully-parsed readings — which tells you nothing when the real
question is "is any data arriving over the wire at all." The panel
answers that directly, in the same window as the rest of the dashboard,
without needing a separate Arduino IDE Serial Monitor session.

Implementation: `RadioLinkReader.start()` gained an `on_raw_line`
callback firing for every line read off the serial port, before any
parsing/validation.

---

## 6. Hardware bring-up log (ATMOS 22 leg)

| Step | Result |
|---|---|
| iMet-X4 direct connection | ✅ Confirmed — live temperature/humidity/pressure streaming to dashboard |
| ATMOS 22 wired to Arduino/Teensy, sketch flashed | Attempted |
| First live serial check | ❌ "Nothing coming back" — no data visible |
| Added Raw Serial debug panel | Revealed real bytes *were* arriving: `$A22 +0.08+145.7+0.08` |
| Diagnosis | Only 3 of the expected 6 SDI-12 values present → parser silently rejected every reading (mismatched value count) |
| Added `$DBG` logging to the sketch | Surfaces the sensor's own `aM!` reply (claimed value count) vs. the actual `aD0!` reply, to separate "sensor problem" from "Teensy 4.x SDI-12 timing problem" (a known risk flagged in the sketch's own comments) |
| Re-flashed, re-tested | ✅ **Full 6-value replies now arriving reliably**: `$A22 +0.05+156.1+0.05+22.9-88.0+10.2` — wind speed, direction, gust, temperature, and both tilt axes all present and parsing correctly into the dashboard |

**Current state: the ATMOS 22 → Arduino → USB → `live_read_radio.py` →
dashboard path is confirmed working end to end**, standalone (not yet
combined with the X4 in one session).

### Known cosmetic issues from the latest test (not yet fixed)
- Wind Speed stat card rounds small values (e.g. `0.05`) down to `0.0` —
  display-only, the underlying data is correct.
- Humidity/Pressure charts show an odd artifact line when only the
  ATMOS 22 is connected (those fields are legitimately absent without an
  X4/Trisonica in the session).
- Tilt (`x_tilt_deg`/`y_tilt_deg`) is parsed and available but not yet
  shown anywhere on the dashboard.

---

## 7. Testing

- **39 automated tests**, all offline/mocked (no hardware required):
  12 pre-existing + 27 new covering SDI-12 parsing, Trisonica parsing,
  `SensorHub` fusion, and `RadioLinkReader` demuxing.
- Verified in a clean virtual environment: `pip install -r requirements.txt`
  installs cleanly with no missing dependencies, all modules import, all
  39 tests pass — confirms the repo is usable by someone cloning it fresh.

---

## 8. Repository / delivery status

All work merged into `main` on GitHub
(`github.com/varunnair1234/ATMOS-Lab-framework`) across several PRs:

1. Core ATMOS 22 / Trisonica / radio-relay integration (17 files)
2. Standalone ATMOS 22 Arduino bridge sketch
3. Raw Serial debug panel
4. ATMOS 22 debug logging (`$DBG` lines)

One unrelated PR ("Add llm insights panel") remains open with merge
conflicts, outside the scope of this work.

---

## 9. What's next

- Fix the two cosmetic dashboard issues noted in §6.
- Add tilt display to the dashboard.
- Build the missing piece to combine ATMOS22-via-Arduino with the X4 and
  Trisonica in a single `live_read_multi.py`-style session (currently
  each pathway runs standalone).
- Resolve the two flagged blockers in `relay.ino` before any real flight:
  X4 tap voltage verification, radio module framing.
- Bring up and test the Trisonica Mini leg the same way the ATMOS 22 was
  (currently untested against real hardware).
- Full end-to-end bench test: all three sensors → Teensy → radio →
  ground dashboard, before attempting an actual drone flight.
