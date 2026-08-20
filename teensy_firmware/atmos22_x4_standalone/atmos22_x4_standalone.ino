/*
 * atmos22_x4_standalone.ino -- Arduino/Teensy sketch that polls a METER
 * ATMOS 22 over SDI-12 AND relays a tapped iMet-X4 TX line, printing both,
 * tagged, over its own USB serial connection. No radio, no Trisonica --
 * just these two legs, pulled out of teensy_firmware/relay.ino so they
 * can be bench-tested (or used permanently) without the full three-sensor
 * relay firmware or a radio link.
 *
 * Why this exists: framework/atmos22_reader.py normally talks to the
 * ATMOS 22 through a commercial USB-to-SDI-12 adapter, and the iMet-X4
 * normally plugs straight into a computer's own USB port. This sketch
 * lets one Arduino/Teensy stand in for both connections at once -- it
 * runs the SDI-12 conversation itself and passively forwards the X4's
 * serial stream, handing Python plain tagged text over ONE USB serial
 * port, which is exactly the wire format framework/radio_link_reader.py
 * already knows how to read (it demuxes tagged lines from ANY serial
 * port, radio receiver or not).
 *
 * Usage on the Python side -- point RadioLinkReader (or live_read_radio.py)
 * at THIS sketch's own USB serial port, same as if it were a radio
 * receiver. Run capture_x4_schema.py once beforehand (over the X4's own
 * direct USB port) if you want $X4 lines parsed -- see that script's
 * docstring for why; without it, $X4 lines are just skipped with a
 * warning, ATMOS22 parsing is unaffected either way:
 *
 *     python capture_x4_schema.py --port <X4's direct USB port>
 *     python live_read_radio.py --port <this board's USB port> --baud 115200 --x4-schema framework/x4_schema.json --dashboard
 *
 * ($TSM lines aren't sent by this sketch -- see teensy_firmware/relay.ino
 * if you want the Trisonica leg too.)
 *
 * Wire format (must match what framework/radio_link_reader.py expects):
 *
 *     $A22 +1.23+180+2.05+22.40+0.30-0.10
 *     $X4 <the X4's own CSV line, exactly as it comes off its TX pin>
 *
 * -- tag, space, then the source's own text verbatim. Don't reformat/
 * re-encode it here -- the ground-station parser expects this exact text.
 *
 * Wiring:
 *   - ATMOS 22: SDI-12 data line -> PIN_SDI12_DATA (bit-banged, any free
 *     GPIO, not a UART pin). Needs the ATMOS 22's own 12V excitation
 *     supply wired per the ATMOS 22 Integrator's Guide (doc 18195) --
 *     this sketch only handles the data/command side, not sensor power.
 *     ATMOS22_ADDRESS below must match your sensor's actual configured
 *     SDI-12 address -- confirmed as 'c' for the unit this was tested
 *     against; don't assume that's universal for a different unit still
 *     at its factory default ('0').
 *   - iMet-X4: TX line -> X4Serial RX pin (a real hardware UART -- see
 *     X4Serial's #define below for which physical pin that is on your
 *     board).
 *
 * CAUTION -- iMet-X4 tap voltage is UNVERIFIED. This taps the X4's TX
 * line directly into a board GPIO/UART pin, assuming it's TTL-level
 * (3.3V/5V logic). If it's actually RS-232 or USB-bridge voltage instead,
 * wiring it in can damage the board. Confirm with a multimeter (measure
 * the actual tap point, before the X4's own USB-serial bridge chip) --
 * see the iMet-X4 Hub manual for where that pad/pin actually is -- BEFORE
 * wiring this leg in. This is the same unresolved item flagged in
 * teensy_firmware/relay.ino; it applies equally here.
 *
 * Library: EnviroDIY Arduino-SDI-12 -- https://github.com/EnviroDIY/Arduino-SDI-12
 *
 * poll_atmos22() below uses the concurrent measurement command (aC!) and
 * reads D0/D1/D2 separately (wind, temperature, orientation) -- confirmed
 * working against real hardware; see git history for how this differs
 * from the simpler single aM!/aD0! approach tried first.
 */

#include <SDI12.h>

#define PIN_SDI12_DATA 2        // <-- SDI-12 data line, any free GPIO
#define ATMOS22_ADDRESS 'c'
#define ATMOS22_POLL_INTERVAL_MS 10000UL  // matches the sensor's own ~10s internal average window
#define USB_BAUD 115200          // just needs to match on the Python side (pyserial default read is baud-agnostic if you pass the same value; see live_read_radio.py --baud)

// iMet-X4 tap -- see the CAUTION above before wiring this in.
#define X4Serial Serial2         // pins 7(RX)/8(TX) on a Teensy 4.1 -- X4's tapped TX wire goes to the RX pin; edit if using a different board or pin set
#define X4_BAUD 115200           // per the iMet-X4 Hub manual (J1 output)

SDI12 sdi12(PIN_SDI12_DATA);
unsigned long last_poll = 0;

void setup() {
  Serial.begin(USB_BAUD);
  sdi12.begin();
  X4Serial.begin(X4_BAUD);
}

void loop() {
  poll_atmos22();
  relay_x4_line();
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

// Forwards one line at a time from the tapped X4 TX line, tagged $X4 --
// same non-blocking buffered-line approach as relay.ino's relay_line(),
// simplified here since there's only ever one streaming source (the X4)
// to buffer for, not two.
void relay_x4_line() {
  static String buf;
  while (X4Serial.available()) {
    char c = X4Serial.read();
    if (c == '\n') {
      if (buf.length() > 0) {
        Serial.print("$X4 ");
        Serial.println(buf);
        buf = "";
      }
    } else if (c != '\r') {
      buf += c;
    }
  }
}