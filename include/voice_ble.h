#pragma once

#include <stdint.h>
class BLEServer;

namespace VoiceBLE {
// 数值与 BLE 语音协议状态一致，显示模块只读取，不改变录音状态机。
enum State : uint8_t { IDLE, RECORDING, SENDING, COMPLETE, ERROR };

struct Snapshot {
  State state;
  uint8_t error;
  uint16_t frames;
  uint16_t sentFrames;
  uint16_t maxFrames;
  bool subscribed;
  bool buttonPressed;
};

// Create alongside HID before advertising. update() runs from loop().
void begin(BLEServer* server);
void update(bool encryptedConnection);
void setMtu(uint16_t mtu);
bool startRecording(bool testTone = false);
bool stopRecording();
bool cancel();
bool busy();
// 从 loop() 在 update() 之后取快照；采音帧数从原子变量读取。
Snapshot snapshot();
}  // namespace VoiceBLE
