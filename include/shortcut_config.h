#pragma once

#include <stdint.h>

#include "board_config.h"
#include "keyboard_ble.h"

namespace ShortcutConfig {

constexpr uint32_t DEBOUNCE_MS = 25;

struct Binding {
  int pin;
  uint8_t modifiers;
  uint8_t key;  // HID Usage ID，不是 ASCII。
  const char* label;
};

// 出厂默认值；网页保存的 NVS 配置会覆盖这些默认绑定。
// KEY4/GPIO7 由语音模块单独处理；更换默认值不会清除已有用户配置。
constexpr Binding KEYS[] = {
    {BoardPins::KEY1, KeyboardBLE::MOD_LEFT_CTRL, KeyboardBLE::KEY_C, "复制"},
    {BoardPins::KEY2, KeyboardBLE::MOD_LEFT_CTRL, KeyboardBLE::KEY_V, "粘贴"},
    {BoardPins::KEY3, KeyboardBLE::MOD_LEFT_CTRL, KeyboardBLE::KEY_Z, "撤销"},
};

}  // namespace ShortcutConfig
