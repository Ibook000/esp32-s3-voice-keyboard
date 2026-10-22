#pragma once

// 接线依据：hardware/Netlist_Schematic1_2026-10-22.tel。
// 所有数值均为芯片 GPIO 编号，不是 U1（ESP32-S3-WROOM-1）的模块焊盘号。
// 完整网络、模块焊盘和 GPIO 对照见 docs/gpio-map.md。
namespace BoardPins {

// U2 INMP441：U1.4/5/6 -> GPIO4/5/6；U2.4（L/R）接 GND，使用左声道。
constexpr int I2S_SCK = 4;
constexpr int I2S_WS = 5;
constexpr int I2S_SD = 6;

// KEY4 是录音键：U1.7 -> GPIO7，另一端接 GND，固件启用内部上拉。
constexpr int VOICE_PTT = 7;

// 三个独立快捷键，另一端均接 GND；组合键映射见 shortcut_config.h。
constexpr int KEY1 = 8;   // U1.12；按键另一端接 GND。
constexpr int KEY2 = 9;   // U1.17。
constexpr int KEY3 = 10;  // U1.18。
// EC11E15244G1：旋转发送鼠标滚轮，按下发送 Enter 确认。
constexpr int ENC_A = 11;   // U1.19；R8 上拉到 3V3。
constexpr int ENC_B = 12;   // U1.20；R9 上拉到 3V3。
constexpr int ENC_SW = 13;  // U1.21；R10 上拉到 3V3，按下接 GND。
// OLED 由 status_display.cpp 使用硬件 I2C 驱动。
constexpr int OLED_SDA = 15;  // U1.8 -> OLED1.4。
constexpr int OLED_SCL = 16;  // U1.9 -> OLED1.3。

// J3 调试串口；TX/RX 均从主控视角命名。
constexpr int UART0_TX = 43;  // U1.37 -> J3.2。
constexpr int UART0_RX = 44;  // U1.36 -> J3.3。

// J1 原生 USB 为固定功能引脚；仅记录连接，不在这里切换 USB 工作模式。
constexpr int USB_DM = 19;  // U1.13，经 R6 接 USB D-。
constexpr int USB_DP = 20;  // U1.14，经 R5 接 USB D+。
constexpr int BOOT = 0;     // U1.27 -> SW5；SW6 接 EN，不是 GPIO。

// J4 的 3..9 脚依次为以下 GPIO；1/2 脚为 GND，10 脚为 3V3。
constexpr int EXPANSION_PINS[] = {38, 39, 40, 41, 42, 2, 1};

}  // namespace BoardPins
