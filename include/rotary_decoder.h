#pragma once
#include <stdint.h>

// EC11E15244G1 为 30 卡点/15 脉冲：00、11 都是卡点，每两次合法相变输出一格。
// 相邻触点反弹会相互抵消；两相同时跳变时丢弃不完整半步，不猜测方向。
class RotaryDecoder {
 public:
  void reset(uint8_t state) {
    previous = state & 3;
    partial = 0;
  }
  inline __attribute__((always_inline)) int8_t update(uint8_t state) {
    state &= 3;
    const uint8_t changed = previous ^ state;
    if (!changed) return 0;
    if (changed == 3) {
      reset(state);
      return 0;
    }
    partial += ((previous & 1) ^ ((state >> 1) & 1)) ? 1 : -1;
    previous = state;
    if (state != 0 && state != 3) return 0;
    const int8_t result = partial == 2 ? 1 : partial == -2 ? -1 : 0;
    partial = 0;
    return result;
  }

 private:
  uint8_t previous = 3;
  int8_t partial = 0;
};
