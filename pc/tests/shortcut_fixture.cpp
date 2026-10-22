#include <Arduino.h>

#include <array>
#include <cstdlib>
#include <initializer_list>
#include <iostream>
#include <utility>
#include <vector>

#include "board_config.h"
#include "keyboard_ble.h"
#include "keymap_store.h"
#include "shortcut_keys.h"

namespace {
Keymap::Map liveMap = Keymap::defaults();
struct Report {
  uint8_t modifiers;
  std::vector<uint8_t> keys;
};

std::array<int, 49> levels;
std::vector<std::pair<int, int>> modes;
std::vector<Report> reports;
uint32_t now = 1000;
bool ready = true;
bool failNext = false;

void check(bool ok, const char* message) {
  if (ok) return;
  std::cerr << message << '\n';
  std::exit(1);
}

void expect(size_t total, uint8_t modifiers, std::initializer_list<uint8_t> keys) {
  check(reports.size() == total, "Wrong report count");
  check(reports.back().modifiers == modifiers, "Wrong modifier state");
  check(reports.back().keys == std::vector<uint8_t>(keys), "Wrong held keys");
}

void reset(bool held = false) {
  liveMap = Keymap::defaults();
  levels.fill(HIGH);
  modes.clear();
  reports.clear();
  now = 1000;
  ready = true;
  failNext = false;
  if (held) levels[BoardPins::KEY1] = LOW;
  ShortcutKeys::begin();
  ShortcutKeys::update();
  expect(1, 0, {});
  check(ShortcutKeys::pressedMask() == (held ? 1 : 0), "OLED should see physical boot state");
  reports.clear();
}

void tick(uint32_t elapsed) {
  now += elapsed;
  ShortcutKeys::update();
}

void setButton(int pin, bool pressed) {
  levels[pin] = pressed ? LOW : HIGH;
  ShortcutKeys::update();
}

void press(int pin) {
  setButton(pin, true);
  tick(25);
}

void release(int pin) {
  setButton(pin, false);
  tick(25);
}

void testBindingsAndPullups() {
  const int pins[] = {BoardPins::KEY1, BoardPins::KEY2, BoardPins::KEY3};
  const uint8_t keys[] = {0x06, 0x19, 0x1D};  // HID C、V、Z。
  for (size_t i = 0; i < 3; ++i) {
    reset();
    check(modes == std::vector<std::pair<int, int>>{{8, INPUT_PULLUP},
                                                    {9, INPUT_PULLUP},
                                                    {10, INPUT_PULLUP}},
          "Only KEY1..KEY3 should be initialized with pull-ups");
    setButton(pins[i], true);
    check(ShortcutKeys::anyPressed(), "Raw press should block serial keyboard tests");
    tick(25);
    expect(1, 0x01, {keys[i]});
    check(ShortcutKeys::pressedMask() == (1U << i), "OLED should see debounced shortcut press");
    setButton(pins[i], false);
    check(ShortcutKeys::anyPressed(), "Debouncing release should still own the keyboard");
    tick(25);
    expect(2, 0, {});
    check(ShortcutKeys::pressedMask() == 0, "OLED should see released shortcut buttons");
    check(!ShortcutKeys::anyPressed(), "Released buttons should no longer be active");
  }
}

void testDebounceAndHold() {
  reset();
  setButton(BoardPins::KEY1, true);
  tick(10);
  setButton(BoardPins::KEY1, false);
  tick(5);
  setButton(BoardPins::KEY1, true);
  tick(24);
  check(reports.empty(), "Bouncing press must not send a shortcut");
  tick(1);
  expect(1, 0x01, {0x06});
  tick(1000);
  expect(1, 0x01, {0x06});  // 保持按下时无需反复提交同一份 HID 报告。
  setButton(BoardPins::KEY1, false);
  tick(24);
  expect(1, 0x01, {0x06});
  tick(1);
  expect(2, 0, {});
}

void testOverlappingButtons() {
  reset();
  press(BoardPins::KEY1);
  expect(1, 0x01, {0x06});
  press(BoardPins::KEY2);
  expect(2, 0x01, {0x06, 0x19});
  release(BoardPins::KEY1);
  expect(3, 0x01, {0x19});
  release(BoardPins::KEY2);
  expect(4, 0, {});
}

void testHeldAtBoot() {
  reset(true);
  tick(100);
  check(reports.empty(), "A button held at boot must not trigger a shortcut");
  release(BoardPins::KEY1);
  press(BoardPins::KEY1);
  expect(1, 0x01, {0x06});
}

void testReconnectDoesNotReplayHeldShortcut() {
  reset();
  press(BoardPins::KEY2);
  expect(1, 0x01, {0x19});
  ready = false;
  tick(100);
  ready = true;
  tick(0);
  expect(2, 0, {});
  tick(100);
  expect(2, 0, {});
  release(BoardPins::KEY2);
  press(BoardPins::KEY2);
  expect(3, 0x01, {0x19});
}

void testFailedPressAndReleaseAreRetried() {
  reset();
  failNext = true;
  press(BoardPins::KEY3);
  check(reports.empty(), "Fake transport should reject first press");
  tick(0);
  expect(1, 0x01, {0x1D});
  failNext = true;
  release(BoardPins::KEY3);
  expect(1, 0x01, {0x1D});
  tick(0);
  expect(2, 0, {});
}

void testManualReleaseDisarmsHeldButton() {
  reset();
  press(BoardPins::KEY1);
  ShortcutKeys::releaseAll();
  expect(2, 0, {});
  tick(100);
  expect(2, 0, {});
  release(BoardPins::KEY1);
  press(BoardPins::KEY1);
  expect(3, 0x01, {0x06});
}

void testMillisRollover() {
  reset();
  now = UINT32_MAX - 10;
  setButton(BoardPins::KEY1, true);
  tick(24);
  check(reports.empty(), "Wrapped clock must still honor debounce interval");
  tick(1);
  expect(1, 0x01, {0x06});
}
void testLiveKeymap() {
  reset();
  liveMap.keys[0].modifiers = KeyboardBLE::MOD_LEFT_CTRL | KeyboardBLE::MOD_LEFT_SHIFT;
  liveMap.keys[0].key = KeyboardBLE::KEY_A + 18;
  press(BoardPins::KEY1);
  expect(1, 3, {KeyboardBLE::KEY_A + 18});
  release(BoardPins::KEY1);
  expect(2, 0, {});
}

}  // namespace

uint32_t millis() { return now; }
int digitalRead(int pin) { return levels.at(pin); }
void pinMode(int pin, int mode) { modes.emplace_back(pin, mode); }

namespace KeyboardBLE {
bool ready() { return ::ready; }
bool sendReport(uint8_t modifiers, const uint8_t* keys, size_t count) {
  if (!::ready || failNext) {
    failNext = false;
    return false;
  }
  std::vector<uint8_t> values;
  if (count) values.assign(keys, keys + count);
  reports.push_back({modifiers, values});
  return true;
}
bool releaseAll() { return sendReport(0, nullptr, 0); }
}  // namespace KeyboardBLE

int main() {
  testLiveKeymap();
  testBindingsAndPullups();
  testDebounceAndHold();
  testOverlappingButtons();
  testHeldAtBoot();
  testReconnectDoesNotReplayHeldShortcut();
  testFailedPressAndReleaseAreRetried();
  testManualReleaseDisarmsHeldButton();
  testMillisRollover();
  std::cout << "9 shortcut scenarios passed\n";
}

namespace KeymapStore {
const Keymap::Map& current() { return liveMap; }
}  // namespace KeymapStore
