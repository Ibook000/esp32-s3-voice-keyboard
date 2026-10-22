#include "display_model.h"

#include <cstdio>
#include <cstring>

#include "voice_protocol.h"

namespace {
uint8_t percent(uint16_t value, uint16_t total) {
  if (total == 0) return 0;
  const unsigned result = unsigned(value) * 100 / total;
  return result > 100 ? 100 : result;
}
}  // namespace

namespace DisplayModel {

View makeView(const Snapshot& snapshot) {
  View view = {};
  view.keymap = snapshot.keymap;
  view.connection = !snapshot.connected ? "未连接" : snapshot.keyboardReady ? "已连接" : "配对中";
  view.pressedKeys = snapshot.pressedKeys;
  view.voiceActive = snapshot.voice.buttonPressed || snapshot.voice.state == VoiceBLE::RECORDING;
  view.progress = NO_PROGRESS;
  view.encoderFeedback = snapshot.encoder.feedback;
  switch (snapshot.voice.state) {
    case VoiceBLE::RECORDING: {
      const unsigned tenths =
          unsigned(snapshot.voice.frames) * VoiceProtocol::FRAME_DURATION_MS / 100;
      std::snprintf(view.footer, sizeof(view.footer), "4 录音 %u.%us", tenths / 10, tenths % 10);
      view.progress = percent(snapshot.voice.frames, snapshot.voice.maxFrames);
      break;
    }
    case VoiceBLE::SENDING:
      view.progress = percent(snapshot.voice.sentFrames, snapshot.voice.frames);
      std::snprintf(view.footer, sizeof(view.footer), "4 发送 %u%%", unsigned(view.progress));
      break;
    case VoiceBLE::COMPLETE:
      std::snprintf(view.footer, sizeof(view.footer), "4 发送完成");
      view.progress = 100;
      break;
    case VoiceBLE::ERROR: {
      const char* reason = nullptr;
      switch (snapshot.voice.error) {
        case 1:
        case 2:
          reason = "检查麦克风";
          break;
        case 4:
          reason = snapshot.voice.subscribed ? "按住录音" : "先启动PC";
          break;
        case 5:
          reason = !snapshot.connected         ? "蓝牙断开"
                   : snapshot.voice.subscribed ? "按住录音"
                                               : "先启动PC";
          break;
        case 6:
          reason = "传输超时";
          break;
        case 7:
          reason = "录音取消";
          break;
        case 8:
          reason = "录音太短";
          break;
      }
      if (reason)
        std::snprintf(view.footer, sizeof(view.footer), "4 %s", reason);
      else
        std::snprintf(view.footer, sizeof(view.footer), "4 错误 %u",
                      unsigned(snapshot.voice.error));
      break;
    }
    default:
      std::snprintf(view.footer, sizeof(view.footer), "%s",
                    snapshot.voice.subscribed ? "4 按住录音" : "4 先启动PC");
      break;
  }
  return view;
}

bool sameView(const View& first, const View& second) {
  return std::strcmp(first.connection, second.connection) == 0 &&
         std::strcmp(first.footer, second.footer) == 0 && first.pressedKeys == second.pressedKeys &&
         first.voiceActive == second.voiceActive && first.progress == second.progress &&
         first.encoderFeedback == second.encoderFeedback &&
         Keymap::equal(first.keymap, second.keymap);
}

void formatShortcut(const ShortcutConfig::Binding& binding, char* output, size_t capacity) {
  if (!capacity) return;
  const char* ctrl = binding.modifiers & 0x11 ? "Ctrl+" : "";
  const char* shift = binding.modifiers & 0x22 ? "Shift+" : "";
  const char* alt = binding.modifiers & 0x44 ? "Alt+" : "";
  const char* win = binding.modifiers & 0x88 ? "Win+" : "";
  char key[12];
  const char* name = key;
  if (binding.key >= 4 && binding.key <= 29)
    std::snprintf(key, sizeof(key), "%c", 'A' + binding.key - 4);
  else if (binding.key >= 30 && binding.key <= 39)
    std::snprintf(key, sizeof(key), "%u", (binding.key - 29) % 10);
  else if (binding.key >= 58 && binding.key <= 69)
    std::snprintf(key, sizeof(key), "F%u", binding.key - 57);
  else {
    switch (binding.key) {
      case 40:
        name = "Enter";
        break;
      case 41:
        name = "Esc";
        break;
      case 42:
        name = "Bksp";
        break;
      case 43:
        name = "Tab";
        break;
      case 44:
        name = "Space";
        break;
      case 73:
        name = "Ins";
        break;
      case 74:
        name = "Home";
        break;
      case 75:
        name = "PgUp";
        break;
      case 76:
        name = "Del";
        break;
      case 77:
        name = "End";
        break;
      case 78:
        name = "PgDn";
        break;
      case 79:
        name = "Right";
        break;
      case 80:
        name = "Left";
        break;
      case 81:
        name = "Down";
        break;
      case 82:
        name = "Up";
        break;
      default:
        std::snprintf(key, sizeof(key), "0x%02X", unsigned(binding.key));
        break;
    }
  }
  std::snprintf(output, capacity, "%s%s%s%s%s", ctrl, shift, alt, win, name);
}

void formatShortcut(const Keymap::Binding& binding, char* output, size_t capacity) {
  const ShortcutConfig::Binding fixed = {0, binding.modifiers, binding.key, binding.label};
  formatShortcut(fixed, output, capacity);
}
void formatShortcutCompact(const Keymap::Binding& binding, char* output, size_t capacity) {
  const Keymap::Binding plain = {0, binding.key, {0}};
  char key[16];
  formatShortcut(plain, key, sizeof(key));
  const char* name = key;
  if (!std::strcmp(key, "Enter"))
    name = "Ent";
  else if (!std::strcmp(key, "Space"))
    name = "Spc";
  else if (!std::strcmp(key, "Right"))
    name = "Rt";
  std::snprintf(output, capacity, "%s%s%s%s%s%s", binding.modifiers & 0x11 ? "C" : "",
                binding.modifiers & 0x22 ? "S" : "", binding.modifiers & 0x44 ? "A" : "",
                binding.modifiers & 0x88 ? "W" : "", binding.modifiers ? "+" : "", name);
}

}  // namespace DisplayModel
