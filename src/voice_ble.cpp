#include "voice_ble.h"

#include <Arduino.h>
#include <BLE2902.h>
#include <BLEServer.h>
#include <driver/i2s.h>
#include <freertos/queue.h>

#include <atomic>
#include <cmath>
#include <new>

#include "ima_adpcm.h"
#include "keymap_ble.h"
#include "voice_config.h"
#include "voice_protocol.h"

namespace {
constexpr unsigned RATE = VoiceProtocol::SAMPLE_RATE;
constexpr unsigned MAX_FRAMES = VOICE_MAX_SECONDS * RATE / VoiceCodec::SAMPLES;
constexpr uint32_t ACK_TIMEOUT_MS = 1000;
constexpr uint32_t SEND_TIMEOUT_MS = 5000;
constexpr unsigned MAX_RETRIES = 5;
constexpr uint32_t BUTTON_DEBOUNCE_MS = 25;
static_assert(VOICE_MAX_SECONDS > 0 && VOICE_MAX_SECONDS <= 10,
              "Recording buffer supports 1..10 seconds");
static_assert(VOICE_MIC_SHIFT >= 0 && VOICE_MIC_SHIFT <= 31, "Invalid microphone shift");
using VoiceBLE::COMPLETE;
using VoiceBLE::ERROR;
using VoiceBLE::IDLE;
using VoiceBLE::RECORDING;
using VoiceBLE::SENDING;
using VoiceBLE::State;
enum Error : uint8_t {
  VOICE_OK,
  NO_MIC,
  I2S_ERROR,
  NO_MEMORY,
  NOT_SUBSCRIBED,
  DISCONNECTED,
  ACK_TIMEOUT,
  CANCELLED,
  EMPTY,
  BAD_COMMAND
};
enum Op : uint8_t { STOP = 0, START = 1, TEST_TONE = 2, ACK = 3, OP_CANCEL = 4 };
struct Command {
  uint8_t op;
  uint16_t session;
  uint16_t sequence;
};

BLECharacteristic* audio = nullptr;
BLECharacteristic* status = nullptr;
BLE2902* audioCccd = nullptr;
BLE2902* statusCccd = nullptr;
QueueHandle_t commands = nullptr;
TaskHandle_t captureTask = nullptr;
uint8_t* blocks = nullptr;
std::atomic<uint16_t> mtu{VoiceProtocol::MIN_MTU};
std::atomic<bool> captureWanted{false};
std::atomic<bool> captureDone{true};
std::atomic<uint16_t> capturedFrames{0};
std::atomic<uint8_t> captureError{VOICE_OK};
std::atomic<bool> notifyQueued{false};
bool tone = false;
bool i2sInstalled = false;
State state = IDLE;
Error error = VOICE_OK;
uint16_t session = 0;
uint16_t txSequence = 0;
size_t txOffset = 0;
bool awaitingAck = false;
unsigned retries = 0;
uint32_t lastProgress = 0;
bool voiceLink = false;
bool buttonPressed = false;

bool micConfigured() { return VOICE_I2S_BCLK >= 0 && VOICE_I2S_WS >= 0 && VOICE_I2S_DATA >= 0; }

void publish() {
  uint8_t value[VoiceProtocol::STATUS_BYTES] = {VoiceProtocol::VERSION, VoiceProtocol::STATUS_TYPE,
                                                state, error};
  const auto frames = capturedFrames.load();
  VoiceCodec::put16(value + 4, session);
  VoiceCodec::put16(value + 6, frames);
  VoiceCodec::put16(value + 8, RATE);
  VoiceCodec::put16(value + 10, VoiceCodec::SAMPLES);
  const uint32_t samples = frames * VoiceCodec::SAMPLES;
  for (int i = 0; i < 4; ++i) value[12 + i] = samples >> (8 * i);
  VoiceCodec::put16(value + 16, VOICE_MAX_SECONDS);
  VoiceCodec::put16(value + 18, (tone ? 1 : 0) | (micConfigured() ? 2 : 0));
  status->setValue(value, sizeof(value));
  if (voiceLink && statusCccd->getNotifications()) status->notify();
}

void fail(Error why) {
  captureWanted.store(false);
  state = ERROR;
  error = why;
  publish();
  Serial.printf("[Voice] Error %u\n", unsigned(why));
}

bool setupI2s() {
  if (i2sInstalled) return true;
  i2s_config_t config = {};
  config.mode = static_cast<i2s_mode_t>(I2S_MODE_MASTER | I2S_MODE_RX);
  config.sample_rate = RATE;
  config.bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT;
  config.channel_format =
      VOICE_I2S_RIGHT_CHANNEL ? I2S_CHANNEL_FMT_ONLY_RIGHT : I2S_CHANNEL_FMT_ONLY_LEFT;
  config.communication_format = I2S_COMM_FORMAT_STAND_I2S;
  config.intr_alloc_flags = ESP_INTR_FLAG_LEVEL1;
  config.dma_buf_count = 4;
  config.dma_buf_len = VoiceCodec::SAMPLES;
  i2s_pin_config_t pins = {};
  pins.mck_io_num = I2S_PIN_NO_CHANGE;
  pins.bck_io_num = VOICE_I2S_BCLK;
  pins.ws_io_num = VOICE_I2S_WS;
  pins.data_out_num = I2S_PIN_NO_CHANGE;
  pins.data_in_num = VOICE_I2S_DATA;
  if (i2s_driver_install(I2S_NUM_0, &config, 0, nullptr) != ESP_OK) return false;
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) {
    i2s_driver_uninstall(I2S_NUM_0);
    return false;
  }
  i2sInstalled = true;
  return true;
}

void capture(void*) {
  int32_t raw[VoiceCodec::SAMPLES];
  int16_t pcm[VoiceCodec::SAMPLES];
  for (;;) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    uint32_t sampleIndex = 0;
    int32_t previous = 0;
    int32_t filtered = 0;
    TickType_t wake = xTaskGetTickCount();
    if (!tone && (!setupI2s() || i2s_start(I2S_NUM_0) != ESP_OK)) {
      captureError.store(I2S_ERROR);
      captureWanted.store(false);
    }
    while (captureWanted.load() && capturedFrames.load() < MAX_FRAMES) {
      if (tone) {
        for (size_t i = 0; i < VoiceCodec::SAMPLES; ++i, ++sampleIndex) {
          pcm[i] = 5000 * std::sin(2.0 * 3.141592653589793 * 440 * sampleIndex / RATE);
        }
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(VoiceProtocol::FRAME_DURATION_MS));
      } else {
        size_t filled = 0;
        while (filled < sizeof(raw) && captureWanted.load()) {
          size_t got = 0;
          const esp_err_t result = i2s_read(I2S_NUM_0, reinterpret_cast<uint8_t*>(raw) + filled,
                                            sizeof(raw) - filled, &got, pdMS_TO_TICKS(100));
          if (result != ESP_OK || got == 0) {
            captureError.store(I2S_ERROR);
            captureWanted.store(false);
            break;
          }
          filled += got;
        }
        if (filled != sizeof(raw)) break;
        for (size_t i = 0; i < VoiceCodec::SAMPLES; ++i) {
          const int32_t sample = raw[i] >> VOICE_MIC_SHIFT;
          // Remove DC before conversion to signed PCM16.
          filtered = sample - previous + int32_t((int64_t(filtered) * 32604) >> 15);
          previous = sample;
          pcm[i] = constrain(filtered, -32768, 32767);
        }
      }
      const auto n = capturedFrames.load();
      VoiceCodec::encode(pcm, VoiceCodec::SAMPLES, blocks + n * VoiceCodec::BLOCK_BYTES);
      capturedFrames.store(n + 1);
    }
    if (!tone && i2sInstalled) i2s_stop(I2S_NUM_0);
    captureWanted.store(false);
    captureDone.store(true);
  }
}

bool enqueue(uint8_t op, uint16_t sid = 0, uint16_t seq = 0) {
  const Command cmd{op, sid, seq};
  return commands && xQueueSend(commands, &cmd, 0) == pdTRUE;
}

class ControlCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) override {
    const auto value = characteristic->getValue();
    if (value.empty()) return;
    const auto* p = reinterpret_cast<const uint8_t*>(value.data());
    if (p[0] == ACK && value.size() == 5) {
      enqueue(ACK, uint16_t(p[1]) | uint16_t(p[2]) << 8, uint16_t(p[3]) | uint16_t(p[4]) << 8);
    } else if (value.size() == 1 && p[0] <= OP_CANCEL && p[0] != ACK) {
      enqueue(p[0]);
    } else {
      enqueue(0xFF);
    }
  }
};
ControlCallbacks controlCallbacks;

class AudioCallbacks : public BLECharacteristicCallbacks {
  void onStatus(BLECharacteristic*, Status result, uint32_t) override {
    notifyQueued.store(result == SUCCESS_NOTIFY);
  }
};
AudioCallbacks audioCallbacks;

void start(bool testTone) {
  if (state == RECORDING || state == SENDING || !captureDone.load()) return;
  if (!blocks || !captureTask) {
    fail(NO_MEMORY);
    return;
  }
  if (!voiceLink || !audioCccd->getNotifications()) {
    fail(NOT_SUBSCRIBED);
    return;
  }
  if (!testTone && !micConfigured()) {
    fail(NO_MIC);
    return;
  }
  ++session;
  tone = testTone;
  error = VOICE_OK;
  capturedFrames.store(0);
  captureError.store(VOICE_OK);
  captureDone.store(false);
  captureWanted.store(true);
  state = RECORDING;
  publish();
  xTaskNotifyGive(captureTask);
  Serial.printf("[Voice] Recording session %u%s\n", session, tone ? " (test tone)" : "");
}

void updateButton() {
  // Debounced optional push-to-talk button. Never record just because it is
  // already held at startup: a new falling edge is required.
  if (VOICE_BUTTON_PIN >= 0) {
    static bool rawLast = digitalRead(VOICE_BUTTON_PIN) == LOW;
    static bool pressed = rawLast;
    static uint32_t changedAt = millis();
    const bool raw = digitalRead(VOICE_BUTTON_PIN) == LOW;
    if (raw != rawLast) {
      rawLast = raw;
      changedAt = millis();
    }
    if (raw != pressed && millis() - changedAt >= BUTTON_DEBOUNCE_MS) {
      pressed = raw;
      if (pressed)
        VoiceBLE::startRecording();
      else
        VoiceBLE::stopRecording();
    }
    buttonPressed = pressed;
  }
}

void processCommand(const Command& cmd) {
  if (cmd.op == START || cmd.op == TEST_TONE)
    start(cmd.op == TEST_TONE);
  else if (cmd.op == STOP && state == RECORDING)
    captureWanted.store(false);
  else if (cmd.op == OP_CANCEL)
    fail(CANCELLED);
  else if (cmd.op == ACK && state == SENDING && awaitingAck && cmd.session == session &&
           cmd.sequence == txSequence) {
    ++txSequence;
    txOffset = 0;
    awaitingAck = false;
    retries = 0;
    lastProgress = millis();
    if (txSequence == capturedFrames.load()) {
      state = COMPLETE;
      publish();
      Serial.println("[Voice] Transfer complete.");
    }
  } else if (cmd.op == 0xFF)
    fail(BAD_COMMAND);
}

void transmit() {
  const uint32_t now = millis();
  if (awaitingAck) {
    if (now - lastProgress < ACK_TIMEOUT_MS) return;
    if (++retries > MAX_RETRIES) {
      fail(ACK_TIMEOUT);
      return;
    }
    awaitingAck = false;
    txOffset = 0;
    lastProgress = now;
  }
  // At most one fragment per loop; retain the same bytes until locally queued.
  const size_t limit = min(size_t(mtu.load() - 3), VoiceProtocol::MAX_NOTIFICATION_BYTES);
  const size_t remaining = VoiceCodec::BLOCK_BYTES - txOffset;
  const size_t length = min(remaining, limit - VoiceProtocol::FRAGMENT_HEADER_BYTES);
  uint8_t packet[VoiceProtocol::MAX_NOTIFICATION_BYTES] = {VoiceProtocol::VERSION,
                                                           VoiceProtocol::AUDIO_TYPE};
  VoiceCodec::put16(packet + 2, session);
  VoiceCodec::put16(packet + 4, txSequence);
  VoiceCodec::put16(packet + 6, txOffset);
  memcpy(packet + VoiceProtocol::FRAGMENT_HEADER_BYTES,
         blocks + txSequence * VoiceCodec::BLOCK_BYTES + txOffset, length);
  audio->setValue(packet, length + VoiceProtocol::FRAGMENT_HEADER_BYTES);
  notifyQueued.store(false);
  audio->notify();
  if (!notifyQueued.load()) {
    if (now - lastProgress > SEND_TIMEOUT_MS) fail(ACK_TIMEOUT);
    return;
  }
  txOffset += length;
  lastProgress = now;
  if (txOffset == VoiceCodec::BLOCK_BYTES) awaitingAck = true;
}
}  // namespace

namespace VoiceBLE {
void begin(BLEServer* server) {
  commands = xQueueCreate(16, sizeof(Command));
  blocks = new (std::nothrow) uint8_t[MAX_FRAMES * VoiceCodec::BLOCK_BYTES];
  if (blocks && xTaskCreate(capture, "voice_capture", 4096, nullptr, 2, &captureTask) != pdPASS) {
    captureTask = nullptr;
    delete[] blocks;
    blocks = nullptr;
  }
  BLEService* service = server->createService(BLEUUID(VoiceProtocol::SERVICE_UUID), 20);
  auto* control =
      service->createCharacteristic(VoiceProtocol::CONTROL_UUID, BLECharacteristic::PROPERTY_WRITE);
  control->setAccessPermissions(ESP_GATT_PERM_WRITE_ENCRYPTED);
  control->setCallbacks(&controlCallbacks);
  audio =
      service->createCharacteristic(VoiceProtocol::AUDIO_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  audio->setCallbacks(&audioCallbacks);
  audio->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED);
  audioCccd = new BLE2902();
  audioCccd->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED | ESP_GATT_PERM_WRITE_ENCRYPTED);
  audio->addDescriptor(audioCccd);
  status = service->createCharacteristic(
      VoiceProtocol::STATUS_UUID,
      BLECharacteristic::PROPERTY_READ | BLECharacteristic::PROPERTY_NOTIFY);
  status->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED);
  statusCccd = new BLE2902();
  statusCccd->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED | ESP_GATT_PERM_WRITE_ENCRYPTED);
  status->addDescriptor(statusCccd);
  if (!commands || !blocks || !captureTask) {
    state = ERROR;
    error = NO_MEMORY;
  }
  publish();
  KeymapBLE::begin(service);
  service->start();
  if (VOICE_BUTTON_PIN >= 0) pinMode(VOICE_BUTTON_PIN, INPUT_PULLUP);
  Serial.printf("[Voice] Buffer: %u bytes; microphone %s; free heap: %u\n",
                MAX_FRAMES * VoiceCodec::BLOCK_BYTES,
                micConfigured() ? "configured" : "not configured", ESP.getFreeHeap());
}

void setMtu(uint16_t value) {
  mtu.store(value < VoiceProtocol::MIN_MTU ? VoiceProtocol::MIN_MTU : value);
}
bool startRecording(bool testTone) { return enqueue(testTone ? TEST_TONE : START); }
bool stopRecording() { return enqueue(STOP); }
bool cancel() { return enqueue(OP_CANCEL); }
bool busy() {
  return state == RECORDING || state == SENDING || !captureDone.load() ||
         (commands && uxQueueMessagesWaiting(commands));
}

Snapshot snapshot() {
  return {state,        error,      capturedFrames.load(),
          txSequence,   MAX_FRAMES, voiceLink && audioCccd && audioCccd->getNotifications(),
          buttonPressed};
}

void update(bool encryptedConnection) {
  voiceLink = encryptedConnection;
  if (!voiceLink && (state == RECORDING || state == SENDING)) fail(DISCONNECTED);
  if (state == SENDING && !audioCccd->getNotifications()) fail(NOT_SUBSCRIBED);

  updateButton();

  Command cmd;
  while (commands && xQueueReceive(commands, &cmd, 0) == pdTRUE) {
    processCommand(cmd);
  }

  if (state == RECORDING && captureDone.load()) {
    if (captureError.load() != VOICE_OK) {
      fail(static_cast<Error>(captureError.load()));
    } else if (!capturedFrames.load()) {
      fail(EMPTY);
    } else {
      state = SENDING;
      txSequence = 0;
      txOffset = 0;
      retries = 0;
      awaitingAck = false;
      lastProgress = millis();
      publish();
      Serial.printf("[Voice] Sending %u frames.\n", capturedFrames.load());
    }
  }
  if (state == SENDING) transmit();
}
}  // namespace VoiceBLE
