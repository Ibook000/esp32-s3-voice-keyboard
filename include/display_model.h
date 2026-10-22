#pragma once

#include <stddef.h>
#include <stdint.h>

#include "keymap.h"
#include "rotary_encoder.h"
#include "shortcut_config.h"
#include "voice_ble.h"

namespace DisplayModel {

// 仅传递数值快照，屏幕任务不直接读取录音/按键状态机的可变数据。
struct Snapshot {
  bool connected;
  bool keyboardReady;
  uint8_t pressedKeys;
  VoiceBLE::Snapshot voice;
  RotaryEncoder::Snapshot encoder;
  Keymap::Map keymap = Keymap::defaults();
};

constexpr uint8_t NO_PROGRESS = 255;
struct View {
  const char* connection;
  char footer[40];
  uint8_t pressedKeys;
  bool voiceActive;
  uint8_t progress;
  RotaryEncoder::Feedback encoderFeedback;
  Keymap::Map keymap;
};

View makeView(const Snapshot& snapshot);
bool sameView(const View& first, const View& second);
void formatShortcut(const ShortcutConfig::Binding& binding, char* output, size_t capacity);
void formatShortcut(const Keymap::Binding& binding, char* output, size_t capacity);
void formatShortcutCompact(const Keymap::Binding& binding, char* output, size_t capacity);

}  // namespace DisplayModel
