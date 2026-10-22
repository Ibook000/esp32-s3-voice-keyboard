#pragma once

#include <stdint.h>

namespace StatusDisplay {
// 从 setup() 创建显示任务；屏幕缺失或 I2C 异常不会阻止蓝牙运行。
void begin();
// 从 loop() 定期投递快照，不在这里刷 I2C 或等待显示任务。
void update();
// 仅深睡前关闭屏幕，由显示任务操作 I2C；等待确认期间可取消。
bool prepareSleep();
void cancelSleep();
// 当前已检测并初始化的 7 位 I2C 地址；未找到或断开时为 0。
uint8_t i2cAddress();
}  // namespace StatusDisplay
