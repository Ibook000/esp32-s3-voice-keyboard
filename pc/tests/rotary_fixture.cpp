#include <Arduino.h>

#include <array>
#include <cstdlib>
#include <iostream>
#include <vector>

#include "board_config.h"
#include "encoder_config.h"
#include "keyboard_ble.h"
#include "keymap_store.h"
#include "rotary_decoder.h"
#include "rotary_encoder.h"
#include "shortcut_keys.h"
namespace {
struct Report {
  uint8_t modifiers;
  std::vector<uint8_t> keys;
};
std::array<int, 49> levels;
std::array<void (*)(), 49> handlers;
std::vector<int> configured;
std::vector<int> wheel;
std::vector<int> consumer;
Keymap::Map liveMap = Keymap::defaults();
std::vector<Report> reports;
uint32_t now = 1000;
bool keyReady = true, mouseReady = true, failWheel = false;
void check(bool condition, const char* message) {
  if (!condition) {
    std::cerr << message << '\n';
    std::exit(1);
  }
}
void tick(uint32_t ms = 0) {
  now += ms;
  RotaryEncoder::update();
  ShortcutKeys::update();
}
void reset(bool held = false, uint32_t start = 1000) {
  levels.fill(HIGH);
  handlers.fill(nullptr);
  configured.clear();
  wheel.clear();
  consumer.clear();
  liveMap = Keymap::defaults();
  reports.clear();
  now = start;
  keyReady = mouseReady = true;
  failWheel = false;
  if (held) levels[BoardPins::ENC_SW] = LOW;
  ShortcutKeys::begin();
  RotaryEncoder::begin();
  tick();
  reports.clear();
}
void phase(uint8_t state) {
  levels[BoardPins::ENC_A] = (state & 2) ? HIGH : LOW;
  levels[BoardPins::ENC_B] = (state & 1) ? HIGH : LOW;
  handlers[BoardPins::ENC_A]();
}
void detent(bool clockwise = true) {
  uint8_t state =
      (levels[BoardPins::ENC_A] == HIGH ? 2 : 0) | (levels[BoardPins::ENC_B] == HIGH ? 1 : 0);
  const uint8_t cw[] = {2, 0, 3, 1}, ccw[] = {1, 3, 0, 2};
  phase(clockwise ? cw[state] : ccw[state]);
  state = (levels[BoardPins::ENC_A] == HIGH ? 2 : 0) | (levels[BoardPins::ENC_B] == HIGH ? 1 : 0);
  phase(clockwise ? cw[state] : ccw[state]);
}
void press() {
  levels[BoardPins::ENC_SW] = LOW;
  tick();
  tick(25);
}
void release() {
  levels[BoardPins::ENC_SW] = HIGH;
  tick();
  tick(25);
}
void testDecoder() {
  RotaryDecoder d;
  d.reset(3);
  check(d.update(1) == 0 && d.update(0) == 1 && d.update(2) == 0 && d.update(3) == 1,
        "Need one action at each of two detents per pulse");
  check(d.update(2) == 0 && d.update(0) == -1 && d.update(1) == 0 && d.update(3) == -1,
        "Reverse decoding failed");
  d.reset(3);
  int sum = 0;
  for (uint8_t s : {1, 3, 1, 0, 1, 0, 2, 3}) sum += d.update(s);
  check(sum == 2, "Contact bounce must cancel without adding steps");
  d.reset(3);
  check(d.update(0) == 0 && d.update(2) == 0 && d.update(3) == 1,
        "Invalid two-bit jump must discard partial step");
  d.reset(1);
  check(d.update(0) == 0 && d.update(2) == 0 && d.update(3) == 1,
        "Booting between detents must not invent a step");
}
void testPinsAndRotation() {
  reset();
  check(handlers[11] && handlers[12] && !handlers[13],
        "Both encoder phases must use CHANGE interrupts");
  check(configured == std::vector<int>({8, 9, 10, 11, 12, 13}), "Wrong pull-up pin configuration");
  detent();
  tick(15);
  check(wheel == std::vector<int>({-1}), "Clockwise should scroll down one wheel unit");
  check(RotaryEncoder::snapshot().feedback == RotaryEncoder::SCROLL_DOWN,
        "Missing scroll-down OLED feedback");
  detent(false);
  tick(15);
  check(wheel == std::vector<int>({-1, 1}), "Counterclockwise should scroll up");
  tick(1000);
  check(RotaryEncoder::snapshot().feedback == RotaryEncoder::NONE, "Feedback must expire");
}
void testBurstBounded() {
  reset();
  for (int i = 0; i < 40; ++i) detent();
  for (int i = 0; i < 6; ++i) tick(15);
  int total = 0;
  for (int delta : wheel) {
    check(delta >= -8 && delta <= 8, "Wheel batch exceeds limit");
    total += delta;
  }
  check(total == -32 && RotaryEncoder::snapshot().detents == 40,
        "ISR backlog must be bounded while preserving diagnostic count");
}
void testReconnect() {
  reset();
  mouseReady = keyReady = false;
  tick();
  detent();
  detent();
  tick(15);
  check(wheel.empty(), "Disconnected rotation must not send");
  mouseReady = keyReady = true;
  tick(15);
  tick(15);
  check(wheel.empty(), "Reconnect must discard stale movement");
  detent();
  tick(15);
  check(wheel.size() == 1, "New movement after reconnect must work");
  reset();
  mouseReady = false;
  tick();
  phase(1);  // 断线时仅转到半格中间。
  mouseReady = true;
  tick();
  phase(0);
  tick(15);
  check(wheel.empty(), "Partial offline detent must not leak across reconnect");
  phase(2);
  phase(3);
  tick(15);
  check(wheel.size() == 1, "Complete new detent must work after dropping partial movement");
}
void testSwitchDebounce() {
  reset();
  levels[13] = LOW;
  tick();
  tick(10);
  levels[13] = HIGH;
  tick();
  tick(25);
  check(reports.empty(), "Short switch bounce must not confirm");
  press();
  check(
      reports.size() == 1 && reports.back().keys == std::vector<uint8_t>({KeyboardBLE::KEY_ENTER}),
      "Press must send Enter");
  tick(30);
  check(reports.back().keys.empty(), "Enter must release without waiting for physical release");
  const size_t count = reports.size();
  tick(2000);
  check(reports.size() == count, "Held switch must not repeat confirmation");
  release();
  press();
  check(reports.size() == count + 1, "Second click must confirm again");
}
void testHeldOnBootAndReconnect() {
  reset(true);
  tick(100);
  check(reports.empty(), "Held boot switch must be suppressed");
  release();
  press();
  tick(30);
  const size_t count = reports.size();
  keyReady = false;
  tick();
  keyReady = true;
  tick();
  tick(100);
  check(reports.size() == count + 1 && reports.back().keys.empty(),
        "Reconnect must send neutral without replaying held Enter");
  release();
  press();
  check(reports.back().keys == std::vector<uint8_t>({KeyboardBLE::KEY_ENTER}),
        "Release must re-arm confirmation");
}
void testShortcutCoexistence() {
  reset();
  levels[8] = LOW;
  tick();
  tick(25);
  check(reports.back().modifiers == KeyboardBLE::MOD_LEFT_CTRL, "Ctrl+C setup failed");
  press();
  check(
      reports.back().modifiers == KeyboardBLE::MOD_LEFT_CTRL &&
          reports.back().keys == std::vector<uint8_t>({KeyboardBLE::KEY_C, KeyboardBLE::KEY_ENTER}),
      "Enter must merge with held shortcut");
  tick(30);
  check(reports.back().modifiers == KeyboardBLE::MOD_LEFT_CTRL &&
            reports.back().keys == std::vector<uint8_t>({KeyboardBLE::KEY_C}),
        "Releasing Enter must preserve held Ctrl+C");
  const size_t count = reports.size();
  detent();
  tick(15);
  check(reports.size() == count, "Wheel must not overwrite keyboard report");
  ShortcutKeys::releaseAll();
  check(reports.back().modifiers == 0 && reports.back().keys.empty(),
        "Manual release must clear confirmation and modifiers");
}
void testWheelFailure() {
  reset();
  failWheel = true;
  detent();
  tick(15);
  check(wheel.empty(), "Rejected wheel must not be reported as sent");
  failWheel = false;
  tick(15);
  check(wheel.empty(), "Rejected movement must not replay unexpectedly");
  detent();
  tick(15);
  check(wheel.size() == 1, "Next detent must work after a submission failure");
}
void testVolumeMode() {
  reset();
  liveMap.rotaryMode = 1;
  tick();
  detent();
  tick(30);
  detent(false);
  tick(30);
  check(consumer == std::vector<int>({KeyboardBLE::VOLUME_UP, KeyboardBLE::VOLUME_DOWN}),
        "Volume direction mismatch");
  check(wheel.empty() && reports.empty(), "Volume mode must not scroll or type Enter");
  press();
  check(consumer.back() == KeyboardBLE::MUTE, "Press must toggle mute");
  const size_t count = consumer.size();
  tick(2000);
  check(consumer.size() == count, "Held switch must not repeatedly toggle mute");
  release();
  press();
  check(consumer.size() == count + 1, "Second click must toggle again");
}
void testModeChangeDropsBacklog() {
  reset();
  detent();
  liveMap.rotaryMode = 1;
  tick(30);
  tick(30);
  check(consumer.empty() && wheel.empty(), "Mode change must drop previous movement");
  keyReady = false;
  tick();
  detent();
  tick(30);
  keyReady = true;
  tick();
  tick(30);
  check(consumer.empty(), "Reconnect must discard offline volume input");
  liveMap.rotaryMode = 0;
  tick();
  detent();
  tick(30);
  check(wheel == std::vector<int>({-1}), "Switching back must restore scrolling");
}
void testTimeRollover() {
  reset(false, 0xFFFFFFF0);
  press();
  tick(30);
  check(reports.size() == 2 && reports.back().keys.empty(),
        "Confirm pulse must release across millis wrap");
  detent();
  tick(15);
  check(wheel.size() == 1, "Scrolling must continue across millis wrap");
}
}  // namespace
uint32_t millis() { return now; }
int digitalRead(int pin) { return levels.at(pin); }
void pinMode(int pin, int mode) {
  check(mode == INPUT_PULLUP, "Expected INPUT_PULLUP");
  configured.push_back(pin);
}
void attachInterrupt(int pin, void (*handler)(), int mode) {
  check(mode == CHANGE, "Expected CHANGE interrupt");
  handlers.at(pin) = handler;
}
namespace KeyboardBLE {
bool ready() { return keyReady; }
bool wheelReady() { return mouseReady; }
bool consumerReady() { return keyReady; }
bool sendConsumer(uint16_t usage) {
  if (!keyReady) return false;
  consumer.push_back(usage);
  return true;
}
bool sendWheel(int8_t delta) {
  if (!mouseReady || failWheel) return false;
  wheel.push_back(delta);
  return true;
}
bool sendReport(uint8_t modifiers, const uint8_t* keys, size_t count) {
  if (!keyReady) return false;
  Report report = {modifiers, {}};
  if (count) report.keys.assign(keys, keys + count);
  reports.push_back(report);
  return true;
}
bool releaseAll() { return sendReport(0, nullptr, 0); }
}  // namespace KeyboardBLE
int main() {
  testDecoder();
  testPinsAndRotation();
  testBurstBounded();
  testReconnect();
  testSwitchDebounce();
  testHeldOnBootAndReconnect();
  testShortcutCoexistence();
  testWheelFailure();
  testTimeRollover();
  testVolumeMode();
  testModeChangeDropsBacklog();
  std::cout << "11 encoder scenarios passed\n";
}

namespace KeymapStore {
const Keymap::Map& current() { return liveMap; }
}  // namespace KeymapStore
