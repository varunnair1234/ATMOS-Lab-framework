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
 * CAUTION -- Arduino-SDI-12 on a Teensy 4.x specifically: this library's
 * timing-critical bit-banging was originally tuned for AVR boards'
 * timer/interrupt behavior, and Teensy 4.x support has had mixed
 * community reports (600 MHz vs. the 8/16 MHz boards it was written
 * against). Bench-test against known-good readings (e.g. compare with a
 * run of framework/atmos22_reader.py over a direct USB-SDI-12 adapter, if
 * you have one, or just sanity-check the numbers against actual
 * conditions) before trusting this in the field. If it's flaky, check the
 * library's own issue tracker and PJRC's forums for Teensy-specific
 * reports/fixes first, since this is a known rough edge, not necessarily
 * a wiring bug.
 *
 * Library: EnviroDIY Arduino-SDI-12 -- https://github.com/EnviroDIY/Arduino-SDI-12
 */

#include <SDI12.h>

#define PIN_SDI12_DATA 2        // <-- SDI-12 data line, any free GPIO
#define ATMOS22_ADDRESS '0'
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

// Same logic as relay.ino's poll_atmos22(), minus the radio hop -- prints
// straight to the board's own USB serial (Serial) instead of a separate
// RadioSerial.
void poll_atmos22() {
  unsigned long now = millis();
  if (now - last_poll < ATMOS22_POLL_INTERVAL_MS) return;
  last_poll = now;

  String command = String(ATMOS22_ADDRESS) + "M!";
  String reply = sdi12.sendCommand(command);   // "attt n" -- wait time + value count

  // DEBUG -- tagged $DBG (not $A22) so it never gets mistaken for real
  // data by the ground-station parser, which only recognizes $A22/$TSM/$X4.
  // Shows what the sensor CLAIMS it will send (n) vs. what aD0! actually
  // returns below -- if n says 6 but the body only has 3 values, that
  // points at a transmission/timing problem (see the Teensy 4.x SDI-12
  // caution at the top of this file), not the sensor deciding to send less.
  Serial.print("$DBG M!-reply=[");
  Serial.print(reply);
  Serial.print("] len=");
  Serial.println(reply.length());

  if (reply.length() < 5) {
    Serial.println("$DBG M! reply too short/malformed -- skipping this cycle");
    return;
  }

  int wait_s = reply.substring(1, 4).toInt();
  int n_values_expected = reply.substring(4).toInt();
  Serial.print("$DBG wait_s=");
  Serial.print(wait_s);
  Serial.print(" n_values_expected=");
  Serial.println(n_values_expected);

  delay(wait_s * 1000UL);  // blocking is fine here -- SDI-12 measurement wait is normally a few seconds

  String data_command = String(ATMOS22_ADDRESS) + "D0!";
  String data_reply = sdi12.sendCommand(data_command);

  Serial.print("$DBG D0!-reply=[");
  Serial.print(data_reply);
  Serial.print("] len=");
  Serial.println(data_reply.length());

  if (data_reply.length() < 2) {
    Serial.println("$DBG D0! reply too short/malformed -- skipping this cycle");
    return;
  }

  // Strip the address echo the sensor prepends -- matches what
  // ATMOS22SDI12Reader._read_reply() does on the direct-USB side, so the
  // ground-station parser sees the exact same reply-body format either way.
  String body = data_reply.substring(1);
  Serial.print("$A22 ");
  Serial.println(body);
}
