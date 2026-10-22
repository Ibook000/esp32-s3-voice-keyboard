#pragma once

#include <stdint.h>

namespace ShortcutKeys {

// 从 setup() 初始化 GPIO，在 loop() 中持续扫描；不在中断或 BLE 回调中调用。
void begin();
void update();
// 旋钮确认脉冲与实体快捷键合并到同一份报告中。
void setConfirmPressed(bool pressed);

// 原始按下或消抖后仍按下都视为占用，供串口按键测试避让实体按键。
bool anyPressed();

// 消抖后的实体按下状态；bit0/1/2 对应 KEY1/2/3，仅从 loop() 读取。
uint8_t pressedMask();

// 释放全部按键；仍按住的实体按钮需松开后再次按下才生效。
void releaseAll();

}  // namespace ShortcutKeys
