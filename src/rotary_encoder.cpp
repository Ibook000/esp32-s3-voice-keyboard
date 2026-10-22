#include "rotary_encoder.h"

#include <Arduino.h>

#include "board_config.h"
#include "encoder_config.h"
#include "keyboard_ble.h"
#include "keymap_store.h"
#include "rotary_decoder.h"
#include "shortcut_keys.h"

namespace {
portMUX_TYPE rotationMux = portMUX_INITIALIZER_UNLOCKED;
RotaryDecoder decoder;
volatile int16_t pending = 0;
volatile uint32_t detectedDetents = 0;
uint32_t visibleDetents = 0;
bool rawSwitch = false, switchPressed = false, switchArmed = false;
uint8_t lastMode = 0;
bool mutePending = false;
bool wasWheelReady = false, wasKeyboardReady = false, confirmPulse = false;
uint32_t switchChangedAt = 0, pulseStartedAt = 0, lastScrollAt = 0, feedbackAt = 0;
RotaryEncoder::Feedback feedback = RotaryEncoder::NONE;

uint8_t ARDUINO_ISR_ATTR phase() {
  return uint8_t((digitalRead(BoardPins::ENC_A) == HIGH ? 2 : 0) |
                 (digitalRead(BoardPins::ENC_B) == HIGH ? 1 : 0));
}
void ARDUINO_ISR_ATTR onPhaseChanged() {
  portENTER_CRITICAL_ISR(&rotationMux);
  const int8_t step = decoder.update(phase());
  if (step) {
    ++detectedDetents;
    const int next = pending + (EncoderConfig::REVERSE_DIRECTION ? -step : step);
    if (next >= -EncoderConfig::MAX_PENDING_STEPS && next <= EncoderConfig::MAX_PENDING_STEPS)
      pending = next;
  }
  portEXIT_CRITICAL_ISR(&rotationMux);
}
void show(RotaryEncoder::Feedback action, uint32_t now) {
  feedback = action;
  feedbackAt = now;
}
}  // namespace

namespace RotaryEncoder {
void begin() {
  pinMode(BoardPins::ENC_A, INPUT_PULLUP);
  pinMode(BoardPins::ENC_B, INPUT_PULLUP);
  pinMode(BoardPins::ENC_SW, INPUT_PULLUP);
  decoder.reset(phase());
  pending = 0;
  detectedDetents = visibleDetents = 0;
  rawSwitch = switchPressed = digitalRead(BoardPins::ENC_SW) == LOW;
  switchArmed = !rawSwitch;
  wasWheelReady = wasKeyboardReady = confirmPulse = false;
  switchChangedAt = lastScrollAt = millis();
  feedback = NONE;
  lastMode = KeymapStore::current().rotaryMode;
  mutePending = false;
  ShortcutKeys::setConfirmPressed(false);
  attachInterrupt(digitalPinToInterrupt(BoardPins::ENC_A), onPhaseChanged, CHANGE);
  attachInterrupt(digitalPinToInterrupt(BoardPins::ENC_B), onPhaseChanged, CHANGE);
}

void update() {
  const uint32_t now = millis();
  const uint8_t mode = KeymapStore::current().rotaryMode;
  const bool volume = mode == 1;
  if (mode != lastMode) {
    portENTER_CRITICAL(&rotationMux);
    pending = 0;
    decoder.reset(phase());
    portEXIT_CRITICAL(&rotationMux);
    wasWheelReady = wasKeyboardReady = confirmPulse = mutePending = false;
    ShortcutKeys::setConfirmPressed(false);
    lastMode = mode;
  }
  const bool wheelReady = volume ? KeyboardBLE::consumerReady() : KeyboardBLE::wheelReady();
  const bool keyboardReady = volume ? KeyboardBLE::consumerReady() : KeyboardBLE::ready();
  if (!keyboardReady) mutePending = false;
  // 断线期间和重连的第一轮清空旋转积压，避免连接后页面突然跳动。
  int16_t steps = 0;
  const uint32_t previousDetents = visibleDetents;
  portENTER_CRITICAL(&rotationMux);
  visibleDetents = detectedDetents;
  if (!wheelReady || !wasWheelReady) pending = 0;
  // 就绪状态变化时丢弃尚未走完的半格，防止把断线前的一半带到重连后。
  if (wheelReady != wasWheelReady) decoder.reset(phase());
  if (wheelReady && wasWheelReady && !mutePending &&
      uint32_t(now - lastScrollAt) >= (volume ? 30U : EncoderConfig::SCROLL_INTERVAL_MS)) {
    const int limit = volume ? 1 : 8;
    steps = pending > limit ? limit : pending < -limit ? -limit : pending;
    pending -= steps;
  }
  portEXIT_CRITICAL(&rotationMux);
  if (visibleDetents != previousDetents && !wheelReady) show(CONNECT_FIRST, now);
  if (steps) {
    // 标准鼠标相对滚轮：负值向下、正值向上；无需发送“松开滚轮”报告。
    if (volume ? KeyboardBLE::sendConsumer(steps > 0 ? KeyboardBLE::VOLUME_UP
                                                     : KeyboardBLE::VOLUME_DOWN)
               : KeyboardBLE::sendWheel(int8_t(-steps))) {
      lastScrollAt = now;
      show(volume ? (steps > 0 ? VOLUME_UP : VOLUME_DOWN) : (steps > 0 ? SCROLL_DOWN : SCROLL_UP),
           now);
    } else {
      // 本地提交失败的滚动不积压，避免稍后突然滚动；下一格可继续重试。
      show(CONNECT_FIRST, now);
    }
  }
  wasWheelReady = wheelReady;

  const bool raw = digitalRead(BoardPins::ENC_SW) == LOW;
  if (raw != rawSwitch) {
    rawSwitch = raw;
    switchChangedAt = now;
  }
  if (!keyboardReady || !wasKeyboardReady) {
    switchArmed = !raw && !switchPressed;
    confirmPulse = false;
    ShortcutKeys::setConfirmPressed(false);
  }
  if (uint32_t(now - switchChangedAt) >= EncoderConfig::BUTTON_DEBOUNCE_MS &&
      switchPressed != raw) {
    switchPressed = raw;
    if (!raw)
      switchArmed = true;
    else if (keyboardReady && wasKeyboardReady && switchArmed) {
      switchArmed = false;
      confirmPulse = !volume;
      mutePending = volume;
      pulseStartedAt = now;
      // 通过快捷键模块合并完整报告，避免 Enter 报告覆盖正按住的 Ctrl+C/V/Z。
      ShortcutKeys::setConfirmPressed(!volume);
      if (!volume) show(CONFIRM, now);
    } else if (!keyboardReady)
      show(CONNECT_FIRST, now);
  }
  if (confirmPulse && uint32_t(now - pulseStartedAt) >= EncoderConfig::CONFIRM_PULSE_MS) {
    confirmPulse = false;
    ShortcutKeys::setConfirmPressed(false);
  }
  if (mutePending && KeyboardBLE::sendConsumer(KeyboardBLE::MUTE)) {
    mutePending = false;
    show(MUTE, now);
  }
  wasKeyboardReady = keyboardReady;
}
Snapshot snapshot() {
  const bool recent =
      feedback != NONE && uint32_t(millis() - feedbackAt) < EncoderConfig::FEEDBACK_MS;
  return {recent ? feedback : NONE, switchPressed, visibleDetents};
}
}  // namespace RotaryEncoder
