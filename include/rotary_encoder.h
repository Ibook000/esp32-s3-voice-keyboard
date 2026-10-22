#pragma once
#include <stdint.h>
namespace RotaryEncoder {
enum Feedback : uint8_t {
  NONE,
  SCROLL_UP,
  SCROLL_DOWN,
  CONFIRM,
  CONNECT_FIRST,
  VOLUME_UP,
  VOLUME_DOWN,
  MUTE
};
struct Snapshot {
  Feedback feedback;
  bool buttonPressed;
  uint32_t detents;  // 本次启动累计检测到的有效卡点数，只用于诊断，不是绝对位置。
};
void begin();
// loop() 中先调用此函数，再调用 ShortcutKeys::update()。
void update();
Snapshot snapshot();
}  // namespace RotaryEncoder
