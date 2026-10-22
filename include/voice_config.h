#pragma once

#include "board_config.h"

// 按 2026-10-22 网表接线：BCLK=GPIO4、WS=GPIO5、SD=GPIO6。
// 任一麦克风引脚改为 -1 可禁用实际采音，测试音仍可使用。
constexpr int VOICE_I2S_BCLK = BoardPins::I2S_SCK;
constexpr int VOICE_I2S_WS = BoardPins::I2S_WS;
constexpr int VOICE_I2S_DATA = BoardPins::I2S_SD;
// U2.4（L/R）接 GND，选择左声道。
constexpr bool VOICE_I2S_RIGHT_CHANNEL = false;
// A 24-bit I2S microphone usually places its sample in the top 24 bits.
// 16 keeps the top 16 bits; 14 adds 4x gain. Reduce gain if audio clips.
constexpr int VOICE_MIC_SHIFT = 14;

// KEY4（VOICE_PTT）接 GPIO7 与 GND，内部上拉、低电平按下、25 ms 消抖。
// 按住录音、松开发送；改为 -1 可禁用实体按钮。
constexpr int VOICE_BUTTON_PIN = BoardPins::VOICE_PTT;
constexpr unsigned VOICE_MAX_SECONDS = 10;
