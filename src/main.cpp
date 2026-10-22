#include <Arduino.h>

#include "board_config.h"
#include "keyboard_ble.h"
#include "keymap_ble.h"
#include "keymap_store.h"
#include "power_manager.h"
#include "rotary_encoder.h"
#include "shortcut_keys.h"
#include "status_display.h"
#include "voice_ble.h"

namespace {
constexpr char DEVICE_NAME[] = "ibook Keyboard";
constexpr uint32_t SERIAL_BAUD = 115200;
constexpr uint32_t KEY_TEST_DELAY_MS = 3000;
constexpr uint32_t KEY_HOLD_MS = 30;
constexpr uint32_t LOOP_INTERVAL_MS = 5;

bool keyDiagnostics = false;
bool testPending = false;
uint32_t testStarted = 0;

// 诊断只读取四个按键，不模拟按键，也不主动开始录音；1 表示低电平按下。
uint8_t rawButtonMask() {
  const int pins[] = {BoardPins::KEY1, BoardPins::KEY2, BoardPins::KEY3, BoardPins::VOICE_PTT};
  uint8_t mask = 0;
  for (size_t i = 0; i < sizeof(pins) / sizeof(pins[0]); ++i) {
    if (digitalRead(pins[i]) == LOW) mask |= uint8_t(1U << i);
  }
  return mask;
}

void printButtonStatus() {
  const uint8_t raw = rawButtonMask();
  const auto voice = VoiceBLE::snapshot();
  Serial.printf(
      "[KEY] raw pressed: K1=%u K2=%u K3=%u K4=%u; debounced=0x%X; "
      "BLE connected=%u secure=%u ready=%u; voice subscribed=%u state=%u error=%u\n",
      unsigned(bool(raw & 1)), unsigned(bool(raw & 2)), unsigned(bool(raw & 4)),
      unsigned(bool(raw & 8)), unsigned(ShortcutKeys::pressedMask()),
      unsigned(KeyboardBLE::connected()), unsigned(KeyboardBLE::secure()),
      unsigned(KeyboardBLE::ready()), unsigned(voice.subscribed), unsigned(voice.state),
      unsigned(voice.error));
  const auto encoder = RotaryEncoder::snapshot();
  Serial.printf("[ENC] A=%d B=%d SW=%d; pressed=%u detents=%lu; wheel ready=%u action=%u\n",
                digitalRead(BoardPins::ENC_A), digitalRead(BoardPins::ENC_B),
                digitalRead(BoardPins::ENC_SW), unsigned(encoder.buttonPressed),
                static_cast<unsigned long>(encoder.detents), unsigned(KeyboardBLE::wheelReady()),
                unsigned(encoder.feedback));
}

void updateButtonDiagnostics() {
  if (!keyDiagnostics) return;
  static uint8_t previousRaw = 0xFF;
  static uint8_t previousPressed = 0xFF;
  static uint8_t previousVoice = 0xFF;
  static bool previousReady = false;
  static uint32_t previousDetents = 0;
  static bool previousEncoderPressed = false;
  const uint8_t raw = rawButtonMask();
  const uint8_t pressed = ShortcutKeys::pressedMask();
  const auto voice = VoiceBLE::snapshot();
  const bool ready = KeyboardBLE::ready();
  const auto encoder = RotaryEncoder::snapshot();
  if (encoder.detents == previousDetents && encoder.buttonPressed == previousEncoderPressed &&
      raw == previousRaw && pressed == previousPressed && voice.state == previousVoice &&
      ready == previousReady)
    return;
  previousDetents = encoder.detents;
  previousEncoderPressed = encoder.buttonPressed;
  previousRaw = raw;
  previousPressed = pressed;
  previousVoice = voice.state;
  previousReady = ready;
  printButtonStatus();
}

void processSerialCommand() {
  if (!Serial.available()) return;
  switch (Serial.read()) {
    case 'd':
      keyDiagnostics = !keyDiagnostics;
      Serial.printf("[KEY] Diagnostics %s (1=pressed, 0=released).\n",
                    keyDiagnostics ? "on" : "off");
      printButtonStatus();
      break;
    case 'v':
      VoiceBLE::startRecording();
      break;
    case 'b':
      VoiceBLE::stopRecording();
      break;
    case 'n':
      VoiceBLE::startRecording(true);
      break;
    case 'x':
      VoiceBLE::cancel();
      break;
    case 't':
      if (KeyboardBLE::ready() && !ShortcutKeys::anyPressed()) {
        testStarted = millis();
        testPending = true;
        Serial.println("Switch to a text editor; sending a in 3 seconds.");
      } else {
        Serial.println("Pair/connect first and release shortcut buttons before testing.");
      }
      break;
    case 's':
      Serial.printf("Connected: %s, ready: %s, LEDs: 0x%02X\n",
                    KeyboardBLE::connected() ? "yes" : "no", KeyboardBLE::ready() ? "yes" : "no",
                    KeyboardBLE::leds());
      Serial.printf("[OLED] Address: 0x%02X; SDA=%d SCL=%d\n", StatusDisplay::i2cAddress(),
                    BoardPins::OLED_SDA, BoardPins::OLED_SCL);
      printButtonStatus();
      break;
    case 'r':
      testPending = false;
      ShortcutKeys::releaseAll();
      break;
  }
}

void updateKeyboardTest() {
  // Allow switching from the serial monitor to a text editor before sending.
  if (!testPending || millis() - testStarted < KEY_TEST_DELAY_MS) return;
  testPending = false;
  if (ShortcutKeys::anyPressed()) {
    Serial.println("Test cancelled: a shortcut button is held.");
    return;
  }
  const uint8_t keys[] = {KeyboardBLE::KEY_A};
  if (KeyboardBLE::sendReport(0, keys, sizeof(keys))) {
    delay(KEY_HOLD_MS);
    KeyboardBLE::releaseAll();
    Serial.println("Test key sent.");
  } else {
    Serial.println("Test cancelled: keyboard disconnected or not ready.");
  }
}
}  // namespace

void setup() {
#if ARDUINO_USB_CDC_ON_BOOT
  Serial.begin(SERIAL_BAUD);
#else
  // 网表中 UART0 引出至 J3；USB 诊断环境通过 J1 输出。
  Serial.begin(SERIAL_BAUD, SERIAL_8N1, BoardPins::UART0_RX, BoardPins::UART0_TX);
#endif
  PowerManager::begin();
  KeymapStore::begin();
  ShortcutKeys::begin();
  RotaryEncoder::begin();
  KeyboardBLE::begin(DEVICE_NAME);
  Serial.printf("Pair with '%s' in your Bluetooth settings.\n", DEVICE_NAME);
  Serial.println("Commands: t = type a after 3 seconds; s = status; r = release keys.");
  Serial.println("Voice: v = record mic; b = stop/send; n = record test tone; x = cancel.");
  Serial.println("Shortcuts: KEY1 = Ctrl+C; KEY2 = Ctrl+V; KEY3 = Ctrl+Z.");
  Serial.println("Encoder: rotate = scroll; press = Enter. A=11 B=12 SW=13.");
  Serial.println("Diagnostics: d = toggle button logging; 1 means pressed, 0 means released.");
  // 启动说明输出结束后再创建屏幕任务，减少 USB 串口并发打印交错。
  StatusDisplay::begin();
}

void loop() {
  KeyboardBLE::update();
  if (PowerManager::waitingForRelease()) {
    StatusDisplay::update();
    delay(LOOP_INTERVAL_MS);
    return;
  }
  RotaryEncoder::update();
  ShortcutKeys::update();
  VoiceBLE::update(KeyboardBLE::secure());
  const auto voice = VoiceBLE::snapshot();
  KeymapBLE::update(KeyboardBLE::secure(),
                    !ShortcutKeys::anyPressed() && !RotaryEncoder::snapshot().buttonPressed &&
                        voice.state != VoiceBLE::RECORDING && voice.state != VoiceBLE::SENDING);
  StatusDisplay::update();
  processSerialCommand();
  updateButtonDiagnostics();
  updateKeyboardTest();
  PowerManager::update();
  delay(LOOP_INTERVAL_MS);
}
