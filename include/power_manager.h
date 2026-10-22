#pragma once
namespace PowerManager {
void begin();
// 深睡唤醒后的第一次按下只唤醒，必须全部松开并稳定后再运行输入模块。
bool waitingForRelease();
void update();
}  // namespace PowerManager
