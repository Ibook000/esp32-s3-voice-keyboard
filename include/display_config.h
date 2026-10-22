#pragma once

#include <stdint.h>

// 网表未注明控制器，默认常见的 SSD1306 128x64；SH1106 屏改成 1 后重新编译。
#ifndef OLED_USE_SH1106
#define OLED_USE_SH1106 0
#endif

namespace DisplayConfig {
constexpr uint32_t BUS_HZ = 400000;
constexpr uint16_t I2C_TIMEOUT_MS = 20;
constexpr uint32_t UPDATE_MS = 100;
constexpr uint32_t RETRY_MS = 5000;
constexpr uint8_t CONTRAST = 128;
// 7 位 I2C 地址；设为 0 时自动尝试 0x3C、0x3D。
constexpr uint8_t ADDRESS = 0;
}  // namespace DisplayConfig
