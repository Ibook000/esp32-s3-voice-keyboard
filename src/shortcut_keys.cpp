#include "shortcut_keys.h"

#include <Arduino.h>

#include "keyboard_ble.h"
#include "keymap_store.h"
#include "shortcut_config.h"

namespace {

constexpr size_t KEY_COUNT = sizeof(ShortcutConfig::KEYS) / sizeof(ShortcutConfig::KEYS[0]);
static_assert(KEY_COUNT > 0 && KEY_COUNT <= 5, "Reserve one HID key slot for encoder confirmation");
constexpr uint8_t NO_REPORT = 0xFF;

struct Button {
  bool rawPressed;
  bool pressed;
  bool armed;  // 开机/重连时已按住的按钮，要先松开才能触发。
  uint32_t changedAt;
};

Button buttons[KEY_COUNT] = {};
bool confirmPressed = false;
bool wasReady = false;
uint8_t sentMask = NO_REPORT;

}  // namespace

namespace ShortcutKeys {

void begin() {
  const uint32_t now = millis();
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    pinMode(ShortcutConfig::KEYS[i].pin, INPUT_PULLUP);
    const bool pressed = digitalRead(ShortcutConfig::KEYS[i].pin) == LOW;
    buttons[i] = {pressed, pressed, !pressed, now};
  }
  confirmPressed = false;
  wasReady = false;
  sentMask = NO_REPORT;
}

void update() {
  const uint32_t now = millis();
  const bool ready = KeyboardBLE::ready();
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    auto& button = buttons[i];
    const bool raw = digitalRead(ShortcutConfig::KEYS[i].pin) == LOW;
    if (raw != button.rawPressed) {
      button.rawPressed = raw;
      button.changedAt = now;
    }
    // 无阻塞消抖；使用无符号时间差，millis() 回绕后仍可正常比较。
    if (uint32_t(now - button.changedAt) >= ShortcutConfig::DEBOUNCE_MS) {
      button.pressed = raw;
      if (!raw) button.armed = true;
    }
    // 断线期间的按键不排队，不在重连后突然执行复制/粘贴等操作。
    if (!ready || !wasReady) button.armed = !button.rawPressed && !button.pressed;
  }
  wasReady = ready;
  if (!ready) {
    confirmPressed = false;
    sentMask = NO_REPORT;
    return;
  }

  uint8_t mask = 0;
  uint8_t modifiers = 0;
  uint8_t keys[KEY_COUNT + 1] = {};
  size_t count = 0;
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    if (!buttons[i].pressed || !buttons[i].armed) continue;
    mask |= uint8_t(1U << i);
    modifiers |= KeymapStore::current().keys[i].modifiers;
    keys[count++] = KeymapStore::current().keys[i].key;
  }
  if (confirmPressed) {
    mask |= uint8_t(1U << KEY_COUNT);
    keys[count++] = KeyboardBLE::KEY_ENTER;
  }
  // 每份报告都包含当前所有按住的快捷键；最后一个松开时自动释放 Ctrl。
  // 提交失败时保留旧 mask，下一轮重试，防止丢失松键导致主机一直按住 Ctrl。
  if (mask != sentMask && KeyboardBLE::sendReport(modifiers, keys, count)) sentMask = mask;
}

void setConfirmPressed(bool pressed) { confirmPressed = pressed; }

bool anyPressed() {
  if (confirmPressed) return true;
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    if (buttons[i].pressed || digitalRead(ShortcutConfig::KEYS[i].pin) == LOW) return true;
  }
  return false;
}

uint8_t pressedMask() {
  uint8_t mask = 0;
  for (size_t i = 0; i < KEY_COUNT; ++i) {
    if (buttons[i].pressed) mask |= uint8_t(1U << i);
  }
  return mask;
}

void releaseAll() {
  confirmPressed = false;
  for (auto& button : buttons) button.armed = false;
  sentMask = KeyboardBLE::releaseAll() ? 0 : NO_REPORT;
}

}  // namespace ShortcutKeys
