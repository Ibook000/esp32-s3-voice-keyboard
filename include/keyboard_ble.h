#pragma once

#include <stddef.h>
#include <stdint.h>

namespace KeyboardBLE {

// USB HID usage IDs, not ASCII. A..Z = 0x04..0x1D.
constexpr uint8_t KEY_A = 0x04;
constexpr uint8_t KEY_C = KEY_A + 2;
constexpr uint8_t KEY_V = KEY_A + 21;
constexpr uint8_t KEY_Z = KEY_A + 25;
constexpr uint8_t KEY_ENTER = 0x28;
constexpr uint8_t KEY_ESCAPE = 0x29;
constexpr uint8_t KEY_BACKSPACE = 0x2A;
constexpr uint8_t KEY_TAB = 0x2B;
constexpr uint8_t KEY_SPACE = 0x2C;

// Modifier bitmap: left Ctrl/Shift/Alt/GUI, right Ctrl/Shift/Alt/GUI.
constexpr uint8_t MOD_LEFT_CTRL = 0x01;
constexpr uint8_t MOD_LEFT_SHIFT = 0x02;
constexpr uint8_t MOD_LEFT_ALT = 0x04;
constexpr uint8_t MOD_LEFT_GUI = 0x08;

// Call once in setup(), then update() regularly from loop().
void begin(const char* deviceName = "ibook Keyboard");
void update();
bool connected();
bool secure();
// Authenticated with local HID notifications enabled (includes Windows reconnect recovery).
bool ready();

// Complete currently-held key state: up to 6 ordinary keys + modifier bitmap.
// Call from loop(), not interrupts/BLE callbacks. False means not ready or
// invalid input; true means submitted locally, not acknowledged by the host.
bool sendReport(uint8_t modifiers, const uint8_t* keys, size_t count);
bool releaseAll();

// 独立的鼠标相对滚轮报告（Report ID 2），正值向上、负值向下。
// 不改动键盘修饰键/普通键状态，不要求 PC Python 后台。
bool wheelReady();
bool sendWheel(int8_t delta);

// Consumer Control 报告 3：音量增减和静音，不影响普通键盘报告。
constexpr uint16_t VOLUME_UP = 0xE9, VOLUME_DOWN = 0xEA, MUTE = 0xE2;
bool consumerReady();
bool sendConsumer(uint16_t usage);

// Host LED bitmap: bit 0 Num Lock, bit 1 Caps Lock, bit 2 Scroll Lock.
uint8_t leds();

}  // namespace KeyboardBLE
