#pragma once

// 仅供主机上的固件测试使用；PlatformIO 编译固件时不包含此目录。
#include <stddef.h>
#include <stdint.h>

constexpr int LOW = 0;
constexpr int HIGH = 1;
constexpr int INPUT_PULLUP = 2;

uint32_t millis();
int digitalRead(int pin);
void pinMode(int pin, int mode);

// 编码器原生测试在单线程内触发模拟 GPIO 中断；不模拟真实抢占调度。
#define ARDUINO_ISR_ATTR
constexpr int CHANGE = 3;
using portMUX_TYPE = int;
constexpr int portMUX_INITIALIZER_UNLOCKED = 0;
inline void portENTER_CRITICAL(portMUX_TYPE*) {}
inline void portEXIT_CRITICAL(portMUX_TYPE*) {}
inline void portENTER_CRITICAL_ISR(portMUX_TYPE*) {}
inline void portEXIT_CRITICAL_ISR(portMUX_TYPE*) {}
inline int digitalPinToInterrupt(int pin) { return pin; }
void attachInterrupt(int pin, void (*handler)(), int mode);
