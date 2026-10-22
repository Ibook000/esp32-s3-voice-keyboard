#pragma once
#include <stdint.h>
namespace EncoderConfig {
// 从轴端看，默认 A 相先变化为顺时针；实物方向相反时改成 true。
constexpr bool REVERSE_DIRECTION = false;
constexpr uint32_t BUTTON_DEBOUNCE_MS = 25;
constexpr uint32_t CONFIRM_PULSE_MS = 30;
constexpr uint32_t SCROLL_INTERVAL_MS = 15;
constexpr uint32_t FEEDBACK_MS = 1000;
constexpr int MAX_PENDING_STEPS = 32;
}  // namespace EncoderConfig
