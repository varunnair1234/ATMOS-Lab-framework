/*
 * atmos22_standalone.ino -- Minimal Arduino/Teensy sketch that polls a
 * METER ATMOS 22 over SDI-12 and prints each reading, tagged, over its own
 * USB serial connection. No radio, no other sensors -- just this one leg,
 * pulled out of teensy_firmware/relay.ino so it can be bench-tested (or
 * used permanently) on its own.
 *
 * Why this exists: framework/atmos22_reader.py normally talks to the
 * ATMOS 22 through a commercial USB-to-SDI-12 adapter. This sketch lets
 * an Arduino/Teensy (running the Arduino-SDI-12 library) stand in for
 * that adapter instead -- it runs the SDI-12 conversation itself and
 * hands Python plain tagged text over USB serial, which is exactly the
 * wire format framework/radio_link_reader.py already knows how to read
 * (it demuxes tagged lines from ANY serial port, radio receiver or not).
 *
 * Usage on the Python side -- point RadioLinkReader (or live_read_radio.py)
 * at THIS sketch's own USB serial port, same as if it were a radio
 * receiver:
 *
 *     python live_read_radio.py --port <this board's USB port>
 *
 * ($X4 lines are simply never sent by this sketch, so no --x4-schema is
 * needed; $TSM lines aren't sent either -- see teensy_firmware/relay.ino
 * if you want more than just the ATMOS 22 leg.)
 *
 * Wire format (must match what framework/radio_link_reader.py expects,
 * and what ATMOS22SDI12Reader.parse_data_reply() parses):
 *
 *     $A22 +1.23+180+2.05+22.40+0.30-0.10
 *
 * -- the tag, a space, then the SDI-12 aD0! reply body verbatim (address
 * echo stripped), UNMODIFIED. Don't reformat/re-encode it here -- the
 * ground-station parser expects this exact text.
 *
 * Wiring: SDI-12 data line -> PIN_SDI12_DATA (bit-banged, any free GPIO,
 * not a UART pin). Needs the ATMOS 22's own 12V excitation supply wired
 * per the ATMOS 22 Integrator's Guide (doc 18195) -- this sketch only
 * handles the data/command side, not sensor power.
 *
 * Library: EnviroDIY Arduino-SDI-12 -- https://github.com/EnviroDIY/Arduino-SDI-12
 *
 * CAUTION -- on a Teensy 4.x specifically: this library's timing-critical
 * bit-banging was originally tuned for AVR boards' timer/interrupt
 * behavior, and Teensy 4.x support has had mixed community reports
 * (600 MHz vs. the 8/16 MHz boards it was written against). Bench-test
 * against known-good readings (e.g. compare with a run of
 * framework/atmos22_reader.py over a direct USB-SDI-12 adapter, if you
 * have one, or just sanity-check the numbers against actual conditions)
 * before trusting this in the field. If it's flaky, check the library's
 * own issue tracker and PJRC's forums for Teensy-specific reports/fixes
 * first, since this is a known rough edge, not necessarily a wiring bug.
 */

#include <SDI12.h>

#define PIN_SDI12_DATA 2        // <-- SDI-12 data line, any free GPIO
#define ATMOS22_ADDRESS 'c'
#define ATMOS22_POLL_INTERVAL_MS 10000UL  // matches the sensor's own ~10s internal average window
#define USB_BAUD 115200          // just needs to match on the Python side (pyserial default read is baud-agnostic if you pass the same value; see live_read_radio.py --baud)

SDI12 sdi12(PIN_SDI12_DATA);
unsigned long last_poll = 0;

void setup() {
  Serial.begin(USB_BAUD);
  sdi12.begin();
}

void loop() {
  poll_atmos22();
}

// Same logic as relay.ino's poll_atmos22(), minus the radio hop -- prints
// straight to the board's own USB serial (Serial) instead of a separate
// RadioSerial.
void poll_atmos22() {
  unsigned long now = millis();
  if (now - last_poll < ATMOS22_POLL_INTERVAL_MS) return;
  last_poll = now;

  // Use the concurrent measurement command so D0, D1, and D2 contain
  // wind, temperature, and orientation data respectively.
  String command = String(ATMOS22_ADDRESS) + "C!";

  sdi12.clearBuffer();
  sdi12.sendCommand(command);
  delay(30);

  String reply = "";
  while (sdi12.available()) {
    char c = sdi12.read();
    if ((c != '\n') && (c != '\r')) {
      reply += c;
      delay(10);
    }
  }
  sdi12.clearBuffer();

  // "atttnn" -- wait time + value count
  if (reply.length() < 6) return;              // malformed/no response, skip this cycle

  int wait_s = reply.substring(1, 4).toInt();
  int n_values_expected = reply.substring(4).toInt();
  Serial.print("$DBG wait_s=");
  Serial.print(wait_s);
  Serial.print(" n_values_expected=");
  Serial.println(n_values_expected);

  delay(wait_s * 1000UL);  // blocking is fine here -- SDI-12 measurement wait is normally a few seconds

  // D0: wind speed, wind direction, gust wind speed
  String data_command = String(ATMOS22_ADDRESS) + "D0!";

  sdi12.clearBuffer();
  sdi12.sendCommand(data_command);
  delay(30);

  String data_reply = "";
  while (sdi12.available()) {
    char c = sdi12.read();
    if ((c != '\n') && (c != '\r')) {
      data_reply += c;
      delay(10);
    }
  }
  sdi12.clearBuffer();

  if (data_reply.length() < 2) return;

  // D1: air temperature
  String temp_command = String(ATMOS22_ADDRESS) + "D1!";

  sdi12.clearBuffer();
  sdi12.sendCommand(temp_command);
  delay(30);

  String temp_reply = "";
  while (sdi12.available()) {
    char c = sdi12.read();
    if ((c != '\n') && (c != '\r')) {
      temp_reply += c;
      delay(10);
    }
  }
  sdi12.clearBuffer();

  if (temp_reply.length() < 2) return;

  // D2: X orientation, Y orientation, null value
  String tilt_command = String(ATMOS22_ADDRESS) + "D2!";

  sdi12.clearBuffer();
  sdi12.sendCommand(tilt_command);
  delay(30);

  String tilt_reply = "";
  while (sdi12.available()) {
    char c = sdi12.read();
    if ((c != '\n') && (c != '\r')) {
      tilt_reply += c;
      delay(10);
    }
  }
  sdi12.clearBuffer();

  if (tilt_reply.length() < 2) return;

  // Strip the address echo the sensor prepends -- matches what
  // ATMOS22SDI12Reader._read_reply() does on the direct-USB side, so the
  // ground-station parser sees the exact same reply-body format either way.
  String body = data_reply.substring(1);
  String temp_body = temp_reply.substring(1);
  String tilt_body = tilt_reply.substring(1);

  // D2 includes a final null value that ATMOS 22 always reports as 0.
  // Remove it so the output contains exactly the six values expected by
  // the ground-station parser.
  int null_pos = tilt_body.lastIndexOf('+');
  if (null_pos < 0) return;
  tilt_body = tilt_body.substring(0, null_pos);

  Serial.print("$A22 ");
  Serial.print(body);
  Serial.print(temp_body);
  Serial.println(tilt_body);
}