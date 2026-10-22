#include "power_manager.h"

#include <Arduino.h>
#include <BLEDevice.h>
#include <driver/rtc_io.h>
#include <esp_sleep.h>

#include "board_config.h"
#include "keyboard_ble.h"
#include "keymap_ble.h"
#include "keymap_store.h"
#include "rotary_encoder.h"
#include "shortcut_keys.h"
#include "sleep_policy.h"
#include "status_display.h"
#include "voice_ble.h"

namespace {
constexpr int WAKE_PINS[] = {BoardPins::VOICE_PTT, BoardPins::KEY1, BoardPins::KEY2,
                             BoardPins::KEY3, BoardPins::ENC_SW};
SleepPolicy policy;
bool waking = false, previousConnected = false;
uint32_t releasedAt = 0, previousDetents = 0;
uint16_t previousRevision = 0;
bool held() {
  for (int pin : WAKE_PINS)
    if (digitalRead(pin) == LOW) return true;
  return false;
}
}  // namespace
namespace PowerManager {
void begin() {
  waking = esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_EXT1;
  for (int pin : WAKE_PINS) {
    rtc_gpio_deinit(static_cast<gpio_num_t>(pin));
    pinMode(pin, INPUT_PULLUP);
  }
  releasedAt = millis();
  policy.reset(millis());
  if (waking) Serial.println("[Power] Woke from deep sleep; release buttons before use.");
}
bool waitingForRelease() {
  if (!waking) return false;
  if (held()) releasedAt = millis();
  if (uint32_t(millis() - releasedAt) < 30) return true;
  waking = false;
  // 丢弃唤醒按压和等待期间的旋转积压，不把它们当成正常输入。
  ShortcutKeys::begin();
  RotaryEncoder::begin();
  policy.reset(millis());
  return false;
}
void update() {
  const auto now = millis();
  const auto encoder = RotaryEncoder::snapshot();
  const bool connected = KeyboardBLE::connected();
  const auto revision = KeymapStore::transaction().revision;
  const bool activity = held() || encoder.detents != previousDetents ||
                        connected != previousConnected || revision != previousRevision;
  previousDetents = encoder.detents;
  previousConnected = connected;
  previousRevision = revision;
  const bool blocked = waking || ShortcutKeys::anyPressed() || encoder.buttonPressed ||
                       VoiceBLE::busy() || KeymapBLE::busy();
  if (!policy.update(now, activity, blocked, KeymapStore::current().sleepMinutes)) {
    StatusDisplay::cancelSleep();
    return;
  }
  // 熄屏只发生在真正进入深睡前，不单独计时熄屏。等待屏幕任务确认。
  if (!StatusDisplay::prepareSleep()) return;
  if (held() || VoiceBLE::busy() || KeymapBLE::busy()) {
    policy.reset(now);
    StatusDisplay::cancelSleep();
    return;
  }
  uint64_t mask = 0;
  for (int pin : WAKE_PINS) mask |= 1ULL << pin;
  if (esp_sleep_enable_ext1_wakeup(mask, ESP_EXT1_WAKEUP_ANY_LOW) != ESP_OK) {
    Serial.println("[Power] Wakeup setup failed; staying awake.");
    policy.reset(now);
    StatusDisplay::cancelSleep();
    return;
  }
  KeyboardBLE::releaseAll();
  BLEDevice::stopAdvertising();
  BLEDevice::deinit(false);
  // KEY1..KEY4 没有外部上拉：保持 RTC 上拉，避免睡眠时悬空误唤醒。
  esp_sleep_pd_config(ESP_PD_DOMAIN_RTC_PERIPH, ESP_PD_OPTION_ON);
  for (int pin : WAKE_PINS) {
    const auto gpio = static_cast<gpio_num_t>(pin);
    rtc_gpio_init(gpio);
    rtc_gpio_set_direction(gpio, RTC_GPIO_MODE_INPUT_ONLY);
    rtc_gpio_pullup_en(gpio);
    rtc_gpio_pulldown_dis(gpio);
  }
  Serial.println("[Power] Entering deep sleep; press KEY1..KEY4 or encoder switch to wake.");
  Serial.flush();
  esp_deep_sleep_start();
}
}  // namespace PowerManager
