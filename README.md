# ATMOS-Lab-framework

Capture, log, and analyze weather data from an InterMet **iMet-X4 Hub**
radiosonde/UAS payload, a **METER ATMOS 22** ultrasonic anemometer, and/or
a **Trisonica Mini** 3D anemometer — live over serial/SDI-12, or
post-flight from a CSV.

## Setup

```
pip install -r requirements.txt
```

## Sensors

| Sensor | Link | Module | Talks over |
|---|---|---|---|
| iMet-X4 Hub | `live_read.py` | `framework/serial_reader.py` | Serial, 115200 baud, streams continuously |
| METER ATMOS 22 | `live_read_multi.py` | `framework/atmos22_reader.py` | SDI-12 (via a USB-SDI-12 adapter), request/response |
| Trisonica Mini (LI-550) | `live_read_multi.py` | `framework/trisonica_reader.py` | Serial, 57600 baud (configurable), streams continuously |

Use any one sensor with `live_read.py`, or combine several in one session
with `live_read_multi.py`, which fuses their readings (via
`framework/multi_sensor.py`'s `SensorHub`) into a single canonical stream
for the dashboard and flight log — see "2. Multi-sensor live capture" below.

## Ways to use this

### 1. Live capture (single sensor) — `live_read.py`

Streams data from the X4's J1 port at 115200 baud. On connect it polls the
board's live configuration (`/CBD`, `/CJ4`-`/CJ9` — the same thing the
iMet-XS software does when you click "Fetch") and parses every field the
board is currently configured to publish, whatever peripherals happen to
be plugged into J4-J9.

```
python live_read.py                 # auto-detects the port
python live_read.py --port COM5     # or specify it
python live_read.py --dashboard     # stream into a live Dash dashboard instead of the console
```

Every session is logged to `flights/` as it runs:
- `imet_x4_<timestamp>.csv` — the canonical columns (`timestamp`,
  `temperature`, `humidity`, `pressure`, `latitude`, `longitude`,
  `altitude`) that the rest of this repo already knows how to plot.
- `imet_x4_<timestamp>_raw.csv` — every field the board reported that
  session (battery stats, GPS HDOP, individual sensor serial numbers,
  etc.), independent of whatever the board itself is logging to its own
  microSD card.

When you stop a session (Ctrl+C), it prints both file paths and the exact
command to turn that flight into a full report (see below).

If the serial link drops mid-session (USB hiccup, radio dropout), it
reconnects automatically with backoff (retrying the same COM port, falling
back to re-detecting the device if Windows reassigns a different one) and
keeps appending to the same log files rather than ending the session.

### 2. Multi-sensor live capture — `live_read_multi.py`

Fuses any combination of the iMet-X4, ATMOS 22, and Trisonica Mini into one
session — one dashboard, one canonical CSV, one raw CSV (with every
source's fields prefixed, e.g. `atmos22_wind_speed_ms`,
`trisonica_pressure`). Each source is independent underneath (own
port/baud, own retry/reconnect loop, own sample rate); `SensorHub` just
keeps the latest known value per field and emits a merged row whenever any
source reports, so temperature/humidity/pressure (from the X4 or the
Trisonica) and wind (from the ATMOS 22 or the Trisonica) show up in the
same row even though they arrive on different schedules. When both the
ATMOS 22 and the Trisonica are present, the ATMOS 22's wind readings take
precedence — see the `SensorHub` docstring to change that ordering.

```
python live_read_multi.py --x4-port COM5 --atmos22-port COM6 --trisonica-port COM7
python live_read_multi.py --x4-port COM5 --atmos22-port COM6 --dashboard
python live_read_multi.py --no-x4 --trisonica-port /dev/ttyUSB1   # wind/met only, no X4
```

The ATMOS 22 needs a USB-to-SDI-12 adapter between it and your computer —
`framework/atmos22_reader.py` talks standard SDI-12 (`aM!`/`aC!` + `aD0!`)
over whatever serial port that adapter exposes. See that module's
docstring for the exact byte-level settings (1200 baud, 7E1) and how to
override them if your adapter bridges to something else.

The Trisonica Mini streams continuously once powered, same as the X4, but
its baud rate and which fields it outputs are both set from its own config
menu — `framework/trisonica_reader.py` parses whatever key/value pairs
show up rather than assuming a fixed column list, and `--trisonica-baud`
lets you match your sensor's configured rate.

### 3. Post-flight report — `quickstart.py`

Runs the full stats + time-series pipeline (histograms, correlation
matrix, wind rose, vertical profile, time series) and opens a dashboard
over the result.

```
python quickstart.py                                   # synthetic demo flight
DATA_CSV=flights/imet_x4_20260720_142233.csv python quickstart.py   # a real captured flight
```

PNGs are written to `outputs/` by default; set `OUTPUT_DIR` to change
that (useful so a real flight's report doesn't overwrite the committed
demo images). Panels that need data the X4 doesn't produce — wind, or
altitude with no GPS fix — are skipped automatically rather than erroring.

### 4. Published snapshot — `build_docs.py` + GitHub Pages

Same `DATA_CSV` convention, but exports a single static HTML file
(`docs/index.html`) instead of opening a live server. This is what
`.github/workflows/deploy-pages.yml` runs on every push to `main` to
publish the dashboard to GitHub Pages.

```
python build_docs.py
```

## `framework/` modules

| Module | Owns |
|---|---|
| `serial_reader.py` | Talks to the X4 over serial: connects, polls its live `/CBD`/`/CJ4`-`/CJ9` config to build a field schema, parses each line into named variables, and reduces a reading down to the canonical schema (`to_canonical_row`). |
| `atmos22_reader.py` | `ATMOS22SDI12Reader` — talks to the ATMOS 22 over SDI-12 (`aM!`/`aC!` + `aD0!`), on a poll interval rather than a stream, and reduces a reading down to the canonical schema. |
| `trisonica_reader.py` | `TrisonicaMiniReader` — parses the Trisonica's `<KEY> <value>` serial stream into named variables (whatever fields are currently enabled on the sensor) and reduces a reading down to the canonical schema. |
| `multi_sensor.py` | `SensorHub` — runs several readers at once and fuses their canonical rows (last-observation-carried-forward, configurable precedence) into one merged stream for the dashboard/flight log. |
| `flight_log.py` | `FlightLogger` — writes a live session to the canonical + raw CSVs described above. |
| `dashboard.py` | `Dashboard` — the Dash app, in either post-flight (`dataframe=`) or live (`data_queue=`) mode. |
| `plots.py` | `TimeSeriesPlot` — matplotlib time-series figures (temperature/humidity, pressure/altitude, wind, 4-panel overview). |
| `stats.py` | `StatsPlot` — summary table, histograms, correlation heatmap, wind rose, vertical profile. |

`plots.py`/`stats.py`/`dashboard.py` all read the same canonical column
names, so anything that produces a DataFrame in that schema — synthetic,
a real logged flight, or a CI-supplied CSV — works with all of them
unchanged.

## Where output lands

- `flights/` — per-session live-capture logs (gitignored).
- `outputs/` — demo report PNGs (committed).
- `docs/` — generated by `build_docs.py`, published by CI (gitignored).

## Tests

```
pytest tests/
```

`tests/test_serial_reader.py` validates the parser against a real captured
line from the hardware, entirely offline (no serial port needed) — it's
what CI runs on every push/PR via `.github/workflows/test.yml`.

## Reference

- iMet-X4: InterMet's *iMet-X4 Hub User Guide and Manual*, doc 252100.0200 Rev 12.
- ATMOS 22: METER Group's *ATMOS 22 Integrator's Guide*, doc 18195.
- Trisonica Mini: LI-COR's *LI-550 TriSonica Mini* documentation (licor.com/products/trisonica) and the sensor's own built-in `help` menu.
