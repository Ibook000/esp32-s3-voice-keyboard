#include <Arduino.h>
#include <BLEDevice.h>
#include <esp_sleep.h>

#include <array>
#include <cstdlib>
#include <iostream>

#include "keymap_store.h"
#include "power_manager.h"
#include "rotary_encoder.h"
#include "sleep_policy.h"
namespace {
std::array<int, 49> pins;
uint32_t now = 1000, detents = 0;
bool link = true, voiceBusy = false, configBusy = false, screenReady = true, requested = false,
     entered = false;
int wakeCause = 0, advStops = 0, shutdowns = 0, wakeError = 0;
uint64_t wakeMask = 0;
Keymap::Transaction config;
void check(bool c, const char* m) {
  if (!c) {
    std::cerr << m << '\n';
    std::exit(1);
  }
}
void reset() {
  pins.fill(HIGH);
  now = 1000;
  detents = 0;
  link = true;
  voiceBusy = configBusy = requested = entered = false;
  screenReady = true;
  wakeError = wakeCause = advStops = shutdowns = 0;
  config = Keymap::Transaction();
  config.active.sleepMinutes = 1;
  PowerManager::begin();
  PowerManager::update();
}
void tick(uint32_t ms) {
  now += ms;
  PowerManager::update();
}
}  // namespace
TestSerial Serial;
uint32_t millis() { return now; }
int digitalRead(int p) { return pins.at(p); }
void pinMode(int, int) {}
int esp_sleep_get_wakeup_cause() { return wakeCause; }
int esp_sleep_enable_ext1_wakeup(uint64_t mask, int mode) {
  check(mode == ESP_EXT1_WAKEUP_ANY_LOW, "Wake mode must be ANY_LOW");
  wakeMask = mask;
  return wakeError;
}
void esp_sleep_pd_config(int, int) {}
void esp_deep_sleep_start() { entered = true; }
void BLEDevice::stopAdvertising() { ++advStops; }
void BLEDevice::deinit(bool) { ++shutdowns; }
namespace KeyboardBLE {
bool connected() { return link; }
bool releaseAll() { return true; }
}  // namespace KeyboardBLE
namespace KeymapStore {
const Keymap::Map& current() { return config.active; }
Keymap::Transaction& transaction() { return config; }
}  // namespace KeymapStore
namespace KeymapBLE {
bool busy() { return configBusy; }
}  // namespace KeymapBLE
namespace VoiceBLE {
bool busy() { return voiceBusy; }
}  // namespace VoiceBLE
namespace ShortcutKeys {
bool anyPressed() { return false; }
void begin() {}
}  // namespace ShortcutKeys
namespace RotaryEncoder {
Snapshot snapshot() { return {NONE, false, detents}; }
void begin() { detents = 0; }
}  // namespace RotaryEncoder
namespace StatusDisplay {
bool prepareSleep() {
  requested = true;
  return screenReady;
}
void cancelSleep() { requested = false; }
}  // namespace StatusDisplay
int main() {
  reset();
  tick(59999);
  check(!entered && !requested, "Screen must stay on until deep sleep timeout");
  tick(1);
  check(entered && shutdowns == 1, "Idle deadline must enter sleep");
  check(wakeMask == ((1ULL << 7) | (1ULL << 8) | (1ULL << 9) | (1ULL << 10) | (1ULL << 13)),
        "Only four buttons and encoder switch wake");
  reset();
  voiceBusy = true;
  tick(120000);
  check(!entered, "Voice must prevent sleep");
  voiceBusy = false;
  tick(59999);
  check(!entered, "Voice work must refresh deadline");
  tick(1);
  check(entered, "Sleep must resume after voice idle");
  reset();
  configBusy = true;
  tick(120000);
  check(!entered, "Configuration must prevent sleep");
  configBusy = false;
  tick(60000);
  check(entered, "Configuration completion must allow later sleep");
  reset();
  pins[7] = LOW;
  tick(120000);
  check(!entered, "Held KEY4 must prevent sleep");
  pins[7] = HIGH;
  tick(60000);
  check(entered, "Released button must allow later sleep");
  reset();
  tick(59000);
  ++detents;
  tick(1000);
  tick(59000);
  check(!entered, "Rotation must reset idle deadline");
  tick(1000);
  check(entered, "Rotation idle timeout failed");
  reset();
  screenReady = false;
  tick(60000);
  check(requested && !entered && !shutdowns, "Must wait for display shutdown acknowledgement");
  pins[8] = LOW;
  tick(5);
  check(!requested && !entered, "Activity must cancel pending shutdown");
  reset();
  wakeError = 1;
  tick(60000);
  check(!entered && !requested && !shutdowns, "Failed wake configuration must keep device awake");
  reset();
  wakeCause = ESP_SLEEP_WAKEUP_EXT1;
  pins[13] = LOW;
  PowerManager::begin();
  check(PowerManager::waitingForRelease(), "Wake press must be swallowed");
  now += 100;
  check(PowerManager::waitingForRelease(), "Held wake button cannot pass through");
  pins[13] = HIGH;
  check(PowerManager::waitingForRelease(), "Release must settle");
  now += 30;
  check(!PowerManager::waitingForRelease(), "Release must enable new inputs");
  SleepPolicy policy;
  policy.reset(0xFFFFFF00U);
  check(!policy.update(0xFFFFFF00U, false, false, 1), "Rollover setup");
  check(policy.update(uint32_t(0xFFFFFF00U + 60000U), false, false, 1),
        "Millis wrap must preserve elapsed time");
  check(!policy.update(70000, true, false, 0), "Disabled sleep must never trigger");
  std::cout << "10 deep sleep scenarios passed\n";
}
