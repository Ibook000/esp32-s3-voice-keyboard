#include "status_display.h"

#include <Arduino.h>
#include <U8g2lib.h>
#include <Wire.h>
#include <freertos/queue.h>

#include <atomic>

#include "board_config.h"
#include "display_config.h"
#include "display_model.h"
#include "display_renderer.h"
#include "keyboard_ble.h"
#include "keymap_store.h"
#include "shortcut_config.h"
#include "shortcut_keys.h"
#include "voice_ble.h"

namespace {
#if OLED_USE_SH1106
U8G2_SH1106_128X64_NONAME_F_HW_I2C display(U8G2_R0, U8X8_PIN_NONE, BoardPins::OLED_SCL,
                                           BoardPins::OLED_SDA);
#else
U8G2_SSD1306_128X64_NONAME_F_HW_I2C display(U8G2_R0, U8X8_PIN_NONE, BoardPins::OLED_SCL,
                                            BoardPins::OLED_SDA);
#endif
QueueHandle_t updates = nullptr;
std::atomic<uint8_t> detectedAddress{0};
std::atomic<bool> sleepRequested{false}, sleepReady{false};

bool responds(uint8_t address) {
  Wire.beginTransmission(address);
  return Wire.endTransmission() == 0;
}

uint8_t findDisplay() {
  if (DisplayConfig::ADDRESS) return responds(DisplayConfig::ADDRESS) ? DisplayConfig::ADDRESS : 0;
  if (responds(0x3C)) return 0x3C;
  if (responds(0x3D)) return 0x3D;
  return 0;
}

void run(void*) {
  // 只有此任务访问 Wire/U8g2，避免与主循环共享 I2C 对象或阻塞音频 ACK。
  if (!Wire.begin(BoardPins::OLED_SDA, BoardPins::OLED_SCL, DisplayConfig::BUS_HZ)) {
    Serial.println("[OLED] I2C initialization failed; keyboard continues without display.");
    vTaskDelete(nullptr);
    return;
  }
  Wire.setTimeOut(DisplayConfig::I2C_TIMEOUT_MS);
  bool reportedMissing = false;
  for (;;) {
    const uint8_t address = findDisplay();
    if (!address) {
      if (!reportedMissing)
        Serial.println("[OLED] Not found at 0x3C/0x3D; retrying in background.");
      reportedMissing = true;
      vTaskDelay(pdMS_TO_TICKS(DisplayConfig::RETRY_MS));
      continue;
    }
    reportedMissing = false;
    // U8g2 使用左移一位后的地址，Wire 扫描使用标准 7 位地址。
    display.setI2CAddress(address << 1);
    display.setBusClock(DisplayConfig::BUS_HZ);
    // U8g2 的 GPIO_INIT 会把 SDA/SCL 临时设为 OUTPUT；Arduino-ESP32 2.x 的
    // Wire.begin() 对已启动总线会直接返回，导致 I2C 引脚模式无法恢复。
    // 扫描结束后先释放总线，让 U8g2 在 GPIO_INIT 之后重新初始化硬件 I2C。
    // 后台重试也必须执行此步骤，不能只在第一次启动时释放。
    Serial.printf("[OLED] Found at 0x%02X; initializing display.\n", address);
    Wire.end();
    display.begin();
    Wire.setTimeOut(DisplayConfig::I2C_TIMEOUT_MS);
    display.setContrast(DisplayConfig::CONTRAST);
    detectedAddress.store(address);
    Serial.printf("[OLED] %s 128x64 at 0x%02X; SDA=%d SCL=%d.\n",
                  OLED_USE_SH1106 ? "SH1106" : "SSD1306", address, BoardPins::OLED_SDA,
                  BoardPins::OLED_SCL);

    DisplayModel::View previous = {};
    bool first = true;
    bool poweredDown = false;
    for (;;) {
      if (sleepRequested.load()) {
        if (!poweredDown) {
          display.setPowerSave(1);
          poweredDown = true;
        }
        sleepReady.store(true);
        vTaskDelay(pdMS_TO_TICKS(10));
        continue;
      }
      if (poweredDown) {
        display.setPowerSave(0);
        poweredDown = false;
        first = true;
        sleepReady.store(false);
      }
      DisplayModel::Snapshot snapshot;
      if (xQueueReceive(updates, &snapshot, pdMS_TO_TICKS(100)) != pdTRUE) continue;
      const auto view = DisplayModel::makeView(snapshot);
      if (!first && DisplayModel::sameView(previous, view)) continue;
      if (!responds(address)) {
        detectedAddress.store(0);
        Serial.println("[OLED] Disconnected; searching again.");
        break;
      }
      DisplayRenderer::draw(display, view);
      display.sendBuffer();
      previous = view;
      first = false;
    }
  }
}
}  // namespace

namespace StatusDisplay {

uint8_t i2cAddress() { return detectedAddress.load(); }
bool prepareSleep() {
  sleepRequested.store(true);
  return !updates || !detectedAddress.load() || sleepReady.load();
}
void cancelSleep() {
  sleepRequested.store(false);
  sleepReady.store(false);
}

void begin() {
  if (updates) return;
  updates = xQueueCreate(1, sizeof(DisplayModel::Snapshot));
  if (!updates) {
    Serial.println("[OLED] No memory for display queue; keyboard continues without display.");
    return;
  }
  if (xTaskCreate(run, "oled_display", 4096, nullptr, 1, nullptr) != pdPASS) {
    vQueueDelete(updates);
    updates = nullptr;
    Serial.println("[OLED] Could not create display task; keyboard continues without display.");
  }
}

void update() {
  if (!updates) return;
  static uint32_t lastUpdate = 0;
  const uint32_t now = millis();
  if (uint32_t(now - lastUpdate) < DisplayConfig::UPDATE_MS) return;
  lastUpdate = now;
  DisplayModel::Snapshot snapshot = {};
  snapshot.connected = KeyboardBLE::connected();
  snapshot.keyboardReady = KeyboardBLE::ready();
  snapshot.pressedKeys = ShortcutKeys::pressedMask();
  snapshot.voice = VoiceBLE::snapshot();
  snapshot.encoder = RotaryEncoder::snapshot();
  snapshot.keymap = KeymapStore::current();
  // 队列仅保留最新一帧，显示跟不上时丢旧帧，不反向拖慢蓝牙和录音。
  xQueueOverwrite(updates, &snapshot);
}

}  // namespace StatusDisplay
