# ATMOS 22 & Trisonica Mini Readers

Two new sensor modules for the ATMOS-Lab-framework, following the same
pattern as the existing `framework/serial_reader.py` (iMet-X4 driver):
connect over a serial port, parse each reading into named variables, and
reduce that down to a small canonical schema the rest of the repo
(`dashboard.py`, `flight_log.py`, `stats.py`, `plots.py`) already knows how
to plot and log.

---

## `framework/atmos22_reader.py` — METER ATMOS 22

**What it is:** an ultrasonic anemometer (wind speed, direction, gust,
plus air temperature and tilt). It doesn't stream data on its own — it
speaks **SDI-12**, a request/response protocol, so you talk to it through
a USB-to-SDI-12 adapter that shows up as a normal serial port.

**What the module does:**

1. Sends a measurement command (`aM!`, or `aC!` for a CRC-checked variant)
   to the sensor's address.
2. The sensor replies with how many seconds until the reading is ready and
   how many values it will return.
3. The module waits that long, then sends `aD0!` to fetch the values.
4. It parses the reply — SDI-12 packs values together with just a leading
   `+`/`-` sign as the separator (e.g. `+1.23+180+2.05...`) — into six
   named fields, in the order the ATMOS 22 always returns them:

   | Field | Meaning |
   |---|---|
   | `wind_speed_ms` | average wind speed since the last query (m/s) |
   | `wind_direction_deg` | average wind direction since the last query (°) |
   | `gust_wind_speed_ms` | max instantaneous speed in that window (m/s) |
   | `air_temperature_c` | air temperature (°C) |
   | `x_tilt_deg` / `y_tilt_deg` | sensor tilt, for checking it's still level (°) |

5. Error codes the sensor can return (`-9999` general error, `-9990`
   invalid wind reading) are converted to `None` rather than left as
   numbers a chart would plot as real data.

**Connection handling:** same as the iMet-X4 driver — if the adapter isn't
plugged in yet, or the link drops mid-session, it retries with backoff
forever rather than crashing, and exposes `.connected` / `.last_error` so
a dashboard can show real status.

**Polling:** the ATMOS 22 only updates its internal average every 10
seconds regardless of how often you ask, so the reader polls on a
configurable interval (`poll_interval_s`, default 10s) instead of
streaming continuously like the other two sensors.

**`to_canonical_row()` output:** `wind_speed`, `wind_direction`,
`wind_gust`, `temperature`, `tilt_x`, `tilt_y`, `timestamp`.

---

## `framework/trisonica_reader.py` — Trisonica Mini (LI-550)

**What it is:** a 3D ultrasonic anemometer that also reports temperature,
humidity, pressure, and compass heading. Unlike the ATMOS 22, it streams
continuously over a plain serial connection once powered on — no
request/response needed.

**What the module does:**

1. Reads one line at a time from the serial port. Each line looks like:

   ```
   S 00.08 S2 00.07 D 245 DV 033 U 00.06 V 00.03 W 00.05 T 21.40 C 346.68 H 17.92 DP 03.68 P 1006.05
   ```

   — space-separated `<KEY> <value>` pairs.

2. Parses every key/value pair present on the line with a regex, rather
   than assuming a fixed set of columns — the Trisonica's own config menu
   controls which fields it outputs, so this adapts to whatever you've
   enabled instead of breaking if you change the sensor's settings.

3. Known key meanings:

   | Key | Meaning |
   |---|---|
   | `S` | horizontal wind speed (m/s) |
   | `D` | horizontal wind direction (°) |
   | `U` / `V` / `W` | orthogonal wind vector components (m/s) |
   | `T` | air temperature (°C) |
   | `C` | compass heading (°) |
   | `H` | relative humidity (%) |
   | `P` | pressure (hPa, if enabled) |
   | `DP` | dew point (°C) |
   | `S2`, `DV`, `AX/AY/AZ`, `PI`, `RO`, `MX/MY/MZ`, `MD`, `TD` | secondary speed/angle, accelerometer, pitch/roll, magnetometer, magnetic/true direction — kept in the raw reading but not mapped to canonical fields |

**Connection handling:** same retry/backoff/status pattern as the other
two readers.

**Baud rate:** configurable (`baud=`, default 57600) since the sensor's
config menu can change its output rate — it must match whatever the
sensor is actually set to.

**`to_canonical_row()` output:** `wind_speed`, `wind_direction`,
`temperature`, `humidity`, `pressure`, `wind_u`, `wind_v`, `wind_w`,
`compass_heading`, `timestamp`. Any field the sensor isn't currently
configured to output comes through as `None` instead of raising an error.

---

## How they fit together

Both readers are designed to be used standalone *or* combined with the
iMet-X4 (and each other) through `framework/multi_sensor.py`'s
`SensorHub`, which merges whichever sensors you connect into one row per
update for the dashboard and flight log — see `live_read_multi.py` for the
CLI that wires this up.
