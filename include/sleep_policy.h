#pragma once
#include <stdint.h>

// 不依赖 Arduino，便于验证计时回绕和录音/配置等阻塞条件。
class SleepPolicy {
 public:
  void reset(uint32_t now) { lastActivity = now; }
  bool update(uint32_t now, bool activity, bool blocked, uint16_t minutes) {
    if (activity || blocked || minutes != previousMinutes || !minutes) lastActivity = now;
    previousMinutes = minutes;
    return minutes && uint32_t(now - lastActivity) >= uint32_t(minutes) * 60000U;
  }

 private:
  uint32_t lastActivity = 0;
  uint16_t previousMinutes = 10;
};
