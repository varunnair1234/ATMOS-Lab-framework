/*
 * relay.ino -- Teensy 4.1 firmware: polls/reads three sensor sources and
 * forwards each reading, tagged, over a shared radio link. Does NOT parse
 * or fuse anything -- that's deliberately left to the ground-station
 * framework.radio_link_reader in the Python framework. See that module's
 * docstring for why, and for the exact wire format this firmware produces.
 *
 * STATUS: skeleton. Two of three legs are filled in against confirmed specs;
 * one is a clearly-marked placeholder pending hardware details. See the
 * "STILL NEEDS" comments before flying this.
 *
 * Teensy 4.1 has 8 hardware UARTs (Serial1-Serial8) -- plenty for this,
 * so LI570Serial/X4Serial/RadioSerial below are all real hardware serial,
 * not software/bit-banged. Pin numbers are the DEFAULT RX/TX pins for each
 * port per the Teensy 4.1 pinout diagram; wire accordingly (or edit the
 * assignments below + Serial*.setRX()/.setTX() if you'd rather use the
 * alternate pin sets some of these ports support).
 *
 *   Serial1: RX0 pin 0,  TX0 pin 1
 *   Serial2: RX1 pin 7,  TX1 pin 8
 *   Serial3: RX2 pin 15, TX2 pin 14
 *
 * CAUTION -- Arduino-SDI-12 on Teensy 4.1: this library's timing-critical
 * bit-banging was written against AVR's timer/interrupt behavior and its
 * Teensy 4.x support has had mixed reports in the community (running at
 * 600 MHz vs. the 8/16 MHz boards it was originally tuned for). Bench-test
 * the SDI-12 leg against the real ATMOS 22 (e.g. compare against known-good
 * readings from framework/atmos22_reader.py over a direct USB-SDI-12
 * adapter) before trusting it in flight. If it's flaky, PJRC's forums and
 * the library's own issue tracker have Teensy-specific reports/patches
 * worth checking first.
 *
 * ALSO AVAILABLE ON 4.1, WORTH CONSIDERING: the built-in microSD slot. A
 * few lines writing every tagged line to SD before radio-forwarding it
 * gives you a complete backup log even if the radio link drops mid-flight
 * -- cheap insurance, not included in this skeleton yet but straightforward
 * to bolt on with the SD library if you want it.
 */

#include <SDI12.h>  // EnviroDIY Arduino-SDI-12 -- https://github.com/EnviroDIY/Arduino-SDI-12

// ---- Radio output --------------------------------------------------------
// STILL NEEDS: confirm what the shared radio module (repurposed from the
// iMet-X4) expects on its input -- raw passthrough at some baud (this
// assumes that, as the common case for SiK/RFD900-style radios) vs a
// command/packet framing. If it turns out to need framing, wrap the
// send_tagged() calls below instead of writing straight to RadioSerial.
#define RadioSerial Serial1     // pins 0(RX)/1(TX) -- confirm the radio module is wired here
#define RADIO_BAUD 57600        // <-- placeholder, match the radio module's actual configured rate

// ---- ATMOS 22 (SDI-12) ----------------------------------------------------
#define PIN_SDI12_DATA 2        // <-- SDI-12 data line, any free GPIO (bit-banged, not a UART pin)
#define ATMOS22_ADDRESS '0'
#define ATMOS22_POLL_INTERVAL_MS 10000UL  // matches the sensor's own ~10s internal average window

SDI12 sdi12(PIN_SDI12_DATA);
unsigned long last_atmos22_poll = 0;

// ---- LI-570 (Trisonica Mini via data logger) ------------------------------
// Tapped off the LI-570's radio port TX line -- confirm it's set to
// TTL-UART (not the EIA232 default) before wiring in, see LI-570 Menu
// Interface Mode / "Radio port" settings, or the hardware jumpers if your
// unit gates that behind solder jumpers rather than software.
#define LI570Serial Serial2     // pins 7(RX)/8(TX) -- LI-570's tapped TX wire goes to Teensy pin 7 (RX1)
#define LI570_BAUD 115200       // <-- confirm against the LI-570's radio port config (menu interface)

// ---- iMet-X4 --------------------------------------------------------------
// STILL NEEDS: confirm this tap point is actually TTL-level (before the
// X4's own USB-serial bridge chip), not RS-232/USB voltages, with a meter
// before wiring to a Teensy pin.
#define X4Serial Serial3        // pins 15(RX)/14(TX) -- X4's tapped TX wire goes to Teensy pin 15 (RX2)
#define X4_BAUD 115200          // per the iMet-X4 Hub manual (J1 output)

// --------------------------------------------------------------------------

void setup() {
  RadioSerial.begin(RADIO_BAUD);
  LI570Serial.begin(LI570_BAUD);
  X4Serial.begin(X4_BAUD);
  sdi12.begin();
}

void loop() {
  poll_atmos22();
  relay_line(LI570Serial, "$TSM ");
  relay_line(X4Serial, "$X4 ");
}

// Sends one already-tagged, newline-terminated line to the radio. Kept as
// its own function so it's the one place to change if the radio module
// turns out to need framing/checksums instead of raw passthrough.
void send_tagged(const String &tag_prefix, const String &body) {
  RadioSerial.print(tag_prefix);
  RadioSerial.println(body);
}

// Forwards one line at a time from a streaming source (LI-570, X4) under
// the given tag, without blocking -- only acts when a full line is ready,
// so this can be called every loop() iteration alongside the SDI-12 poll.
void relay_line(Stream &source, const String &tag_prefix) {
  static String li570_buf, x4_buf;
  String &buf = (&source == &LI570Serial) ? li570_buf : x4_buf;

  while (source.available()) {
    char c = source.read();
    if (c == '\n') {
      if (buf.length() > 0) {
        send_tagged(tag_prefix, buf);
        buf = "";
      }
    } else if (c != '\r') {
      buf += c;
    }
  }
}

// Runs one SDI-12 aM!/aD0! cycle against the ATMOS 22 on its own interval
// (it doesn't stream -- see framework/atmos22_reader.py's docstring for the
// same protocol implemented on the Python side for direct-USB use) and
// forwards the raw aD0! reply body under the $A22 tag, unmodified -- the
// ground-station radio_link_reader.py reuses ATMOS22SDI12Reader.parse_data_reply()
// against that exact text, so don't reformat/re-encode it here.
void poll_atmos22() {
  unsigned long now = millis();
  if (now - last_atmos22_poll < ATMOS22_POLL_INTERVAL_MS) return;
  last_atmos22_poll = now;

  String command = String(ATMOS22_ADDRESS) + "M!";
  String reply = sdi12.sendCommand(command);   // "attt n" -- wait time + value count
  if (reply.length() < 5) return;              // malformed/no response, skip this cycle

  int wait_s = reply.substring(1, 4).toInt();
  delay(wait_s * 1000UL);  // blocking is fine here -- SDI-12 measurement wait is normally a few seconds

  String data_command = String(ATMOS22_ADDRESS) + "D0!";
  String data_reply = sdi12.sendCommand(data_command);
  if (data_reply.length() < 2) return;

  // Strip the address echo the sensor prepends, matching what
  // ATMOS22SDI12Reader._read_reply() does on the direct-USB side, so the
  // ground-station parser sees the exact same reply-body format either way.
  String body = data_reply.substring(1);
  send_tagged("$A22 ", body);
}
