#include "keymap_ble.h"

#include <Arduino.h>
#include <BLEService.h>
#include <freertos/queue.h>

#include <cstring>

#include "keymap_store.h"
#include "shortcut_keys.h"
namespace {
struct Packet {
  uint8_t size;
  uint8_t bytes[20];
};
QueueHandle_t queue = nullptr;
BLECharacteristic* characteristic = nullptr;
uint32_t lastCommand = 0;
bool wasSecure = false;
void publish() {
  uint8_t bytes[Keymap::RESPONSE_BYTES];
  KeymapStore::transaction().response(bytes);
  characteristic->setValue(bytes, sizeof(bytes));
}
class Callbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* source) override {
    const auto data = source->getValue();
    Packet packet = {};
    if (data.size() <= sizeof(packet.bytes)) {
      packet.size = data.size();
      std::memcpy(packet.bytes, data.data(), data.size());
    }
    if (queue) xQueueSend(queue, &packet, 0);
  }
} callbacks;
}  // namespace
namespace KeymapBLE {
bool busy() {
  return KeymapStore::transaction().staging() || (queue && uxQueueMessagesWaiting(queue));
}
void begin(BLEService* service) {
  queue = xQueueCreate(8, sizeof(Packet));
  characteristic = service->createCharacteristic(Keymap::UUID, BLECharacteristic::PROPERTY_WRITE);
  characteristic->setAccessPermissions(ESP_GATT_PERM_WRITE_ENCRYPTED);
  characteristic->setCallbacks(&callbacks);
  // 独立读特征：写回调排队期间也不会把命令字节误读为已保存的配置。
  characteristic =
      service->createCharacteristic(Keymap::STATUS_UUID, BLECharacteristic::PROPERTY_READ);
  characteristic->setAccessPermissions(ESP_GATT_PERM_READ_ENCRYPTED);
  publish();
}
void update(bool secure, bool canChange) {
  if (!queue || !characteristic) return;
  const auto now = millis();
  if (!secure || secure != wasSecure || uint32_t(now - lastCommand) > 15000) {
    KeymapStore::transaction().cancel();
    if (!secure) xQueueReset(queue);
  }
  wasSecure = secure;
  Packet packet;
  while (secure && xQueueReceive(queue, &packet, 0) == pdTRUE) {
    lastCommand = now;
    const auto revision = KeymapStore::transaction().revision;
    KeymapStore::transaction().apply(packet.bytes, packet.size, canChange, KeymapStore::persist);
    if (revision != KeymapStore::transaction().revision) ShortcutKeys::releaseAll();
    publish();
  }
}
}  // namespace KeymapBLE
