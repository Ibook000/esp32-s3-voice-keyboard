#include <cstdlib>
#include <cstring>
#include <iostream>

#include "display_model.h"

namespace {
void check(bool ok, const char* message) {
  if (ok) return;
  std::cerr << message << '\n';
  std::exit(1);
}

void textIs(const char* actual, const char* expected) {
  check(std::strcmp(actual, expected) == 0, "Unexpected OLED text");
}

DisplayModel::Snapshot idle() {
  DisplayModel::Snapshot snapshot = {};
  snapshot.voice.state = VoiceBLE::IDLE;
  snapshot.voice.maxFrames = 500;
  return snapshot;
}

void testConnectionStates() {
  auto snapshot = idle();
  textIs(DisplayModel::makeView(snapshot).connection, "未连接");
  snapshot.connected = true;
  textIs(DisplayModel::makeView(snapshot).connection, "配对中");
  snapshot.keyboardReady = true;
  textIs(DisplayModel::makeView(snapshot).connection, "已连接");
}

void testConfiguredShortcuts() {
  const char* expected[] = {"Ctrl+C", "Ctrl+V", "Ctrl+Z"};
  const char* labels[] = {"复制", "粘贴", "撤销"};
  for (size_t i = 0; i < 3; ++i) {
    char text[32];
    DisplayModel::formatShortcut(ShortcutConfig::KEYS[i], text, sizeof(text));
    textIs(text, expected[i]);
    textIs(ShortcutConfig::KEYS[i].label, labels[i]);
  }
}

void testRecordingTimeAndProgress() {
  auto snapshot = idle();
  snapshot.voice.state = VoiceBLE::RECORDING;
  snapshot.voice.frames = 123;
  const auto view = DisplayModel::makeView(snapshot);
  textIs(view.footer, "4 录音 2.4s");
  check(view.progress == 24, "Recording progress should use maximum recording length");
  check(view.voiceActive, "Recording footer should be highlighted");
}

void testSendingProgressAndBounds() {
  auto snapshot = idle();
  snapshot.voice.state = VoiceBLE::SENDING;
  snapshot.voice.frames = 200;
  snapshot.voice.sentFrames = 57;
  auto view = DisplayModel::makeView(snapshot);
  textIs(view.footer, "4 发送 28%");
  check(view.progress == 28, "Sending progress should use actual clip length");
  snapshot.voice.sentFrames = 201;
  check(DisplayModel::makeView(snapshot).progress == 100, "Progress must not exceed display width");
  snapshot.voice.frames = 0;
  check(DisplayModel::makeView(snapshot).progress == 0, "Empty clip must not divide by zero");
}

void testIdleAndComplete() {
  auto snapshot = idle();
  auto view = DisplayModel::makeView(snapshot);
  textIs(view.footer, "4 先启动PC");
  check(view.progress == DisplayModel::NO_PROGRESS, "Idle should not show a progress bar");
  snapshot.voice.subscribed = true;
  textIs(DisplayModel::makeView(snapshot).footer, "4 按住录音");
  snapshot.voice.state = VoiceBLE::COMPLETE;
  view = DisplayModel::makeView(snapshot);
  textIs(view.footer, "4 发送完成");  // 设备没有电脑识别结果，不能显示“识别完成”。
  check(view.progress == 100, "Completed clip should show full progress");
}

void testVoiceErrors() {
  auto snapshot = idle();
  snapshot.voice.state = VoiceBLE::ERROR;
  snapshot.voice.error = 4;
  textIs(DisplayModel::makeView(snapshot).footer, "4 先启动PC");
  snapshot.voice.subscribed = true;
  textIs(DisplayModel::makeView(snapshot).footer, "4 按住录音");
  snapshot.voice.error = 2;
  textIs(DisplayModel::makeView(snapshot).footer, "4 检查麦克风");
  snapshot.voice.error = 6;
  textIs(DisplayModel::makeView(snapshot).footer, "4 传输超时");
  snapshot.voice.error = 255;
  textIs(DisplayModel::makeView(snapshot).footer, "4 错误 255");
}

void testUnchangedFramesAndPressedKeys() {
  auto snapshot = idle();
  snapshot.voice.state = VoiceBLE::RECORDING;
  snapshot.voice.frames = 120;
  const auto first = DisplayModel::makeView(snapshot);
  snapshot.voice.frames = 121;
  check(DisplayModel::sameView(first, DisplayModel::makeView(snapshot)),
        "Do not refresh when visible time and percentage have not changed");
  snapshot.encoder.feedback = RotaryEncoder::SCROLL_DOWN;
  check(!DisplayModel::sameView(first, DisplayModel::makeView(snapshot)),
        "Encoder feedback must trigger refresh");
  snapshot.encoder.feedback = RotaryEncoder::NONE;
  snapshot.keymap.keys[0].key = 22;
  check(!DisplayModel::sameView(first, DisplayModel::makeView(snapshot)),
        "Changed keymap must refresh OLED");
  snapshot.keymap = Keymap::defaults();
  snapshot.pressedKeys = 5;
  auto view = DisplayModel::makeView(snapshot);
  check(view.pressedKeys == 5 && !DisplayModel::sameView(first, view),
        "Changed shortcut highlights must trigger a refresh");
  snapshot.voice.state = VoiceBLE::IDLE;
  snapshot.voice.buttonPressed = true;
  check(DisplayModel::makeView(snapshot).voiceActive, "Physical KEY4 press must be visible");
}

void testShortcutFormattingBounds() {
  auto binding = ShortcutConfig::KEYS[0];
  binding.modifiers |= KeyboardBLE::MOD_LEFT_SHIFT;
  char text[32];
  DisplayModel::formatShortcut(binding, text, sizeof(text));
  textIs(text, "Ctrl+Shift+C");
  binding.key = KeyboardBLE::KEY_ENTER;
  DisplayModel::formatShortcut(binding, text, sizeof(text));
  textIs(text, "Ctrl+Shift+Enter");
  char small[5];
  DisplayModel::formatShortcut(binding, small, sizeof(small));
  textIs(small, "Ctrl");
  DisplayModel::formatShortcut(binding, nullptr, 0);
}
}  // namespace

int main() {
  testConnectionStates();
  testConfiguredShortcuts();
  testRecordingTimeAndProgress();
  testSendingProgressAndBounds();
  testIdleAndComplete();
  testVoiceErrors();
  testUnchangedFramesAndPressedKeys();
  testShortcutFormattingBounds();
  std::cout << "8 display scenarios passed\n";
}
