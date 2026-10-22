#include "display_renderer.h"

#include <U8g2lib.h>

#include <cstdio>

#include "display_model.h"
#include "shortcut_config.h"

namespace {
constexpr int TILE_WIDTH = 40;
constexpr int TILE_TOP = 16;
constexpr int TILE_HEIGHT = 28;
constexpr int TILE_GAP = 4;

void bluetoothIcon(U8G2& display, int x, int y) {
  display.drawLine(x + 3, y, x + 3, y + 10);
  display.drawLine(x + 3, y, x + 7, y + 3);
  display.drawLine(x + 7, y + 3, x, y + 8);
  display.drawLine(x, y + 2, x + 7, y + 7);
  display.drawLine(x + 7, y + 7, x + 3, y + 10);
}

// 超长配置向左对齐并裁在本键区域内，避免覆盖相邻快捷键。
int centeredX(int left, int width, int textWidth) {
  const int inset = (width - textWidth) / 2;
  return left + (inset < 2 ? 2 : inset);
}
}  // namespace

namespace DisplayRenderer {
void draw(U8G2& display, const DisplayModel::View& view) {
  display.clearBuffer();
  display.setDrawColor(1);
  display.setFontMode(1);
  display.setMaxClipWindow();

  // 0..13：品牌与蓝牙状态。固定状态区，连接变化不会挤动主体内容。
  if (view.encoderFeedback == RotaryEncoder::CONNECT_FIRST) {
    display.setFont(u8g2_font_wqy12_t_gb2312);
    display.drawUTF8(1, 11, "先连接蓝牙");
  } else {
    display.setFont(u8g2_font_6x10_tf);
    display.drawStr(1, 10, "ibook");
    // 像素直接画图标，不依赖字库是否包含 ▲、▼、↵。范围在 x=37..74，避开蓝牙区。
    switch (view.encoderFeedback) {
      case RotaryEncoder::SCROLL_UP:
        display.drawTriangle(43, 10, 49, 2, 55, 10);
        break;
      case RotaryEncoder::SCROLL_DOWN:
        display.drawTriangle(43, 2, 55, 2, 49, 10);
        break;
      case RotaryEncoder::VOLUME_UP:
      case RotaryEncoder::VOLUME_DOWN:
        display.drawStr(36, 10, view.encoderFeedback == RotaryEncoder::VOLUME_UP ? "VOL+" : "VOL-");
        break;
      case RotaryEncoder::MUTE:
        display.setFont(u8g2_font_wqy12_t_gb2312);
        display.drawUTF8(41, 11, "静音");
        break;
      case RotaryEncoder::CONFIRM:
        // 回车折返箭头：右侧竖线折向左，箭头指向左端；旁边标明“确定”。
        display.drawVLine(47, 2, 6);
        display.drawHLine(37, 7, 11);
        display.drawLine(37, 7, 41, 3);
        display.drawLine(37, 7, 41, 11);
        display.setFont(u8g2_font_wqy12_t_gb2312);
        display.drawUTF8(51, 11, "确定");
        break;
      default:
        break;
    }
  }
  bluetoothIcon(display, 78, 1);
  display.setFont(u8g2_font_wqy12_t_gb2312);
  display.drawUTF8(126 - display.getUTF8Width(view.connection), 11, view.connection);
  display.drawHLine(0, 13, 128);

  // 16..43：三个实体快捷键，与 KEY1/2/3 从左到右一一对应。
  const size_t count = sizeof(ShortcutConfig::KEYS) / sizeof(ShortcutConfig::KEYS[0]);
  static_assert(count == 3, "128x64 layout has three shortcut tiles and one voice strip");
  for (size_t i = 0; i < count; ++i) {
    const int x = int(i) * (TILE_WIDTH + TILE_GAP);
    const bool pressed = view.pressedKeys & (1U << i);
    display.setDrawColor(1);
    if (pressed)
      display.drawRBox(x, TILE_TOP, TILE_WIDTH, TILE_HEIGHT, 3);
    else
      display.drawRFrame(x, TILE_TOP, TILE_WIDTH, TILE_HEIGHT, 3);
    display.setDrawColor(pressed ? 0 : 1);
    display.setClipWindow(x + 2, TILE_TOP + 2, x + TILE_WIDTH - 2, TILE_TOP + TILE_HEIGHT - 2);

    char title[64];
    std::snprintf(title, sizeof(title), "%u %s", unsigned(i + 1), view.keymap.keys[i].label);
    display.setFont(u8g2_font_wqy12_t_gb2312);
    // 英文宽字母过长时优先显示完整名称，省略前面的编号。
    const char* shown =
        display.getUTF8Width(title) > TILE_WIDTH - 4 ? view.keymap.keys[i].label : title;
    display.drawUTF8(centeredX(x, TILE_WIDTH, display.getUTF8Width(shown)), 28, shown);

    char shortcut[32];
    DisplayModel::formatShortcut(view.keymap.keys[i], shortcut, sizeof(shortcut));
    display.setFont(u8g2_font_6x10_tf);
    if (display.getStrWidth(shortcut) > TILE_WIDTH - 4) {
      DisplayModel::formatShortcutCompact(view.keymap.keys[i], shortcut, sizeof(shortcut));
      if (display.getStrWidth(shortcut) > TILE_WIDTH - 4) display.setFont(u8g2_font_4x6_tr);
    }
    display.drawStr(centeredX(x, TILE_WIDTH, display.getStrWidth(shortcut)), 40, shortcut);
    display.setMaxClipWindow();
  }

  // 47..63：KEY4 独立状态条。反白编号表示实体按住/正在录音；进度独占底部。
  display.setDrawColor(1);
  display.drawHLine(0, 47, 128);
  if (view.voiceActive)
    display.drawRBox(0, 50, 15, 14, 2);
  else
    display.drawRFrame(0, 50, 15, 14, 2);
  display.setDrawColor(view.voiceActive ? 0 : 1);
  display.setFont(u8g2_font_6x10_tf);
  display.drawStr(5, 61, "4");
  display.setDrawColor(1);
  display.setFont(u8g2_font_wqy12_t_gb2312);
  // View 的统一文本仍保留数字前缀，屏幕用独立编号框呈现它。
  const char* text = view.footer;
  if (text[0] == '4' && text[1] == ' ') text += 2;
  display.drawUTF8(20, 59, text);
  if (view.progress != DisplayModel::NO_PROGRESS) {
    // 108 像素进度条：点状轨道 + 实心填充，百分比/秒数与进度条不会重叠。
    for (int x = 20; x < 128; x += 3) display.drawPixel(x, 63);
    const unsigned progress = view.progress > 100 ? 100 : view.progress;
    const unsigned width = 108U * progress / 100;
    if (width) display.drawBox(20, 62, width, 2);
  }
  display.setDrawColor(1);
}
}  // namespace DisplayRenderer
