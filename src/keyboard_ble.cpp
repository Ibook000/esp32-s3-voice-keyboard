#include "keyboard_ble.h"

#include <Arduino.h>
#include <BLE2902.h>
#include <BLEDevice.h>
#include <BLEHIDDevice.h>
#include <BLESecurity.h>

#include <atomic>

#include "voice_ble.h"
#include "voice_protocol.h"

namespace {

// Standard 8-byte keyboard report: modifiers, reserved, six key usage IDs.
// Report ID is supplied by the GATT Report Reference, not in the payload.
uint8_t reportMap[] = {
    0x05,
    0x01,  // Usage Page (Generic Desktop)
    0x09,
    0x06,  // Usage (Keyboard)
    0xA1,
    0x01,  // Collection (Application)
    0x85,
    0x01,  // Report ID (1)
    0x05,
    0x07,  // Usage Page (Keyboard)
    0x19,
    0xE0,  // Usage Minimum (Left Control)
    0x29,
    0xE7,  // Usage Maximum (Right GUI)
    0x15,
    0x00,  // Logical Minimum (0)
    0x25,
    0x01,  // Logical Maximum (1)
    0x75,
    0x01,  // Report Size (1)
    0x95,
    0x08,  // Report Count (8)
    0x81,
    0x02,  // Input (modifier bits)
    0x95,
    0x01,
    0x75,
    0x08,
    0x81,
    0x01,  // Input (reserved byte)
    0x95,
    0x05,
    0x75,
    0x01,
    0x05,
    0x08,  // Usage Page (LEDs)
    0x19,
    0x01,
    0x29,
    0x05,
    0x91,
    0x02,  // Output (five LED bits)
    0x95,
    0x01,
    0x75,
    0x03,
    0x91,
    0x01,  // Output (padding)
    0x95,
    0x06,
    0x75,
    0x08,
    0x15,
    0x00,
    0x25,
    0x65,
    0x05,
    0x07,
    0x19,
    0x00,
    0x29,
    0x65,
    0x81,
    0x00,  // Input (six key usage IDs)
    0xC0,
    // Mouse / Pointer, Report ID 2: buttons + relative X/Y/Wheel (4 bytes).
    // 旋钮只发送 Wheel；X、Y 和按钮始终为零，不会移动或点击指针。
    0x05,
    0x01,
    0x09,
    0x02,
    0xA1,
    0x01,
    0x85,
    0x02,
    0x09,
    0x01,
    0xA1,
    0x00,
    0x05,
    0x09,
    0x19,
    0x01,
    0x29,
    0x03,
    0x15,
    0x00,
    0x25,
    0x01,
    0x95,
    0x03,
    0x75,
    0x01,
    0x81,
    0x02,
    0x95,
    0x01,
    0x75,
    0x05,
    0x81,
    0x01,
    0x05,
    0x01,
    0x09,
    0x30,
    0x09,
    0x31,
    0x09,
    0x38,
    0x15,
    0x81,
    0x25,
    0x7F,
    0x75,
    0x08,
    0x95,
    0x03,
    0x81,
    0x06,
    0xC0,
    0xC0,
    // Consumer Control / Report ID 3: one 16-bit usage (0 releases it).
    0x05,
    0x0C,
    0x09,
    0x01,
    0xA1,
    0x01,
    0x85,
    0x03,
    0x15,
    0x00,
    0x26,
    0xFF,
    0x03,
    0x19,
    0x00,
    0x2A,
    0xFF,
    0x03,
    0x75,
    0x10,
    0x95,
    0x01,
    0x81,
    0x00,
    0xC0,

};

BLEHIDDevice* hid = nullptr;
BLECharacteristic* input = nullptr;
BLE2902* subscription = nullptr;
BLECharacteristic* mouseInput = nullptr;
BLE2902* mouseSubscription = nullptr;
std::atomic<bool> wheelQueued{false};
BLECharacteristic* consumerInput = nullptr;
BLE2902* consumerSubscription = nullptr;
bool consumerHeld = false;
uint32_t consumerPressedAt = 0;
std::atomic<bool> linkConnected{false};
std::atomic<bool> authenticated{false};
std::atomic<bool> restartAdvertising{false};
std::atomic<uint8_t> hostLeds{0};

class ServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer*) override {
    VoiceBLE::setMtu(VoiceProtocol::MIN_MTU);
    authenticated.store(false);
    if (subscription) subscription->setNotifications(false);
    if (mouseSubscription) mouseSubscription->setNotifications(false);
    if (consumerSubscription) consumerSubscription->setNotifications(false);
    linkConnected.store(true);
    Serial.println("[BLE] Connected; waiting for authentication/subscription.");
  }

  void onDisconnect(BLEServer*) override {
    linkConnected.store(false);
    authenticated.store(false);
    hostLeds.store(0);
    if (subscription) subscription->setNotifications(false);
    if (mouseSubscription) mouseSubscription->setNotifications(false);
    if (consumerSubscription) consumerSubscription->setNotifications(false);
    // Restart from loop() after the stack finishes processing disconnect.
    restartAdvertising.store(true);
    Serial.println("[BLE] Disconnected.");
  }

  void onMtuChanged(BLEServer*, esp_ble_gatts_cb_param_t* param) override {
    VoiceBLE::setMtu(param->mtu.mtu);
  }
};

class SecurityCallbacks : public BLESecurityCallbacks {
  // No passkey entry UI: pair using Just Works and save bonding keys.
  uint32_t onPassKeyRequest() override { return 0; }
  void onPassKeyNotify(uint32_t) override {}
  bool onConfirmPIN(uint32_t) override { return true; }
  bool onSecurityRequest() override { return true; }

  void onAuthenticationComplete(esp_ble_auth_cmpl_t result) override {
    if (result.success) {
      // Bluedroid/Arduino 2.x 的 CCCD 对象不跨重启保存。Windows 已配对重连时
      // 可能沿用订阅而不重新写 CCCD，getNotifications() 因而一直是 false。
      // 仅在认证成功后恢复本地 HID 通知门控；主机后续写入仍可关闭它。
      // 参考 ESP32-BLE-Keyboard 0.3.2-beta 的 onConnect/onDisconnect 兼容处理。
      // 语音 CCCD 不做此恢复，仍必须由 PC 接收器显式订阅。
      if (subscription) subscription->setNotifications(true);
      if (mouseSubscription) mouseSubscription->setNotifications(true);
      if (consumerSubscription) consumerSubscription->setNotifications(true);
      authenticated.store(true);
      Serial.println("[BLE] Authentication successful; local HID notifications enabled.");
    } else {
      authenticated.store(false);
      if (subscription) subscription->setNotifications(false);
      if (mouseSubscription) mouseSubscription->setNotifications(false);
      if (consumerSubscription) consumerSubscription->setNotifications(false);
      Serial.printf("[BLE] Authentication failed: 0x%02X\n", result.fail_reason);
    }
  }
};

class LedCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* characteristic) override {
    const std::string value = characteristic->getValue();
    if (!value.empty()) {
      hostLeds.store(static_cast<uint8_t>(value[0]) & 0x1F);
    }
  }
};

ServerCallbacks serverCallbacks;
SecurityCallbacks securityCallbacks;
LedCallbacks ledCallbacks;
class WheelCallbacks : public BLECharacteristicCallbacks {
  void onStatus(BLECharacteristic*, Status status, uint32_t) override {
    wheelQueued.store(status == SUCCESS_NOTIFY);
  }
};
WheelCallbacks wheelCallbacks;

}  // namespace

namespace KeyboardBLE {

void begin(const char* deviceName) {
  if (hid != nullptr) {
    return;
  }

  Serial.printf("[BLE] Initializing controller as '%s'.\n", deviceName);
  BLEDevice::init(deviceName);
  Serial.println("[BLE] Controller initialized; creating services.");
  BLEDevice::setMTU(VoiceProtocol::PREFERRED_MTU);
  BLEDevice::setEncryptionLevel(ESP_BLE_SEC_ENCRYPT);
  BLEDevice::setSecurityCallbacks(&securityCallbacks);

  BLESecurity security;
  security.setAuthenticationMode(ESP_LE_AUTH_REQ_SC_BOND);
  security.setCapability(ESP_IO_CAP_NONE);
  security.setInitEncryptionKey(ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK);
  security.setRespEncryptionKey(ESP_BLE_ENC_KEY_MASK | ESP_BLE_ID_KEY_MASK);
  security.setKeySize(16);

  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(&serverCallbacks);
  hid = new BLEHIDDevice(server);
  input = hid->inputReport(1);
  subscription = static_cast<BLE2902*>(input->getDescriptorByUUID(BLEUUID(uint16_t(0x2902))));
  hid->outputReport(1)->setCallbacks(&ledCallbacks);
  mouseInput = hid->inputReport(2);
  mouseSubscription =
      static_cast<BLE2902*>(mouseInput->getDescriptorByUUID(BLEUUID(uint16_t(0x2902))));
  mouseInput->setCallbacks(&wheelCallbacks);
  uint8_t emptyMouse[4] = {};
  mouseInput->setValue(emptyMouse, sizeof(emptyMouse));
  consumerInput = hid->inputReport(3);
  consumerSubscription =
      static_cast<BLE2902*>(consumerInput->getDescriptorByUUID(BLEUUID(uint16_t(0x2902))));
  uint8_t emptyConsumer[2] = {};
  consumerInput->setValue(emptyConsumer, sizeof(emptyConsumer));
  hid->manufacturer()->setValue("ibook");
  // Local prototype identity, not an assigned commercial vendor/product ID.
  uint8_t pnp[] = {0x02, 0x00, 0x00, 0x01, 0x00, 0x00, 0x01};
  hid->deviceInfo()->getCharacteristic(BLEUUID(uint16_t(0x2A50)))->setValue(pnp, sizeof(pnp));
  hid->hidInfo(0x00, 0x02);  // Normally connectable; no remote wake support.
  hid->reportMap(reportMap, sizeof(reportMap));
  uint8_t emptyReport[8] = {};
  input->setValue(emptyReport, sizeof(emptyReport));
  hid->startServices();
  Serial.println("[BLE] HID service started; creating voice service.");
  VoiceBLE::begin(server);

  // Placeholder until a physical battery measurement is connected.
  hid->setBatteryLevel(100);

  BLEAdvertising* advertising = BLEDevice::getAdvertising();
  advertising->setAppearance(HID_KEYBOARD);
  // Fit both service UUIDs + keyboard appearance into the 31-byte advertisement.
  BLEAdvertisementData adv;
  adv.setFlags(0x06);
  adv.setAppearance(HID_KEYBOARD);
  adv.setCompleteServices(hid->hidService()->getUUID());
  adv.setCompleteServices(BLEUUID(VoiceProtocol::SERVICE_UUID));
  BLEAdvertisementData scan;
  scan.setName(deviceName);
  advertising->setAdvertisementData(adv);
  advertising->setScanResponseData(scan);
  advertising->setMinPreferred(0x06);
  advertising->setMaxPreferred(0x12);
  advertising->start();
  Serial.println("[BLE] Keyboard advertising started.");
  Serial.printf("[BLE] Address: %s\n", BLEDevice::getAddress().toString().c_str());
}

void update() {
  // 每次媒体键按下 20ms 后发送释放，长按旋钮不会重复切换静音。
  if (consumerHeld && (!consumerReady() || uint32_t(millis() - consumerPressedAt) >= 20)) {
    uint8_t released[2] = {};
    consumerInput->setValue(released, sizeof(released));
    if (consumerReady()) consumerInput->notify();
    consumerHeld = false;
  }
  if (restartAdvertising.exchange(false) && !linkConnected.load()) {
    uint8_t emptyReport[8] = {};
    input->setValue(emptyReport, sizeof(emptyReport));
    uint8_t emptyMouse[4] = {};
    mouseInput->setValue(emptyMouse, sizeof(emptyMouse));
    BLEDevice::startAdvertising();
    Serial.println("[BLE] Advertising restarted.");
  }

  static bool wasReady = false;
  const bool nowReady = ready();
  if (nowReady && !wasReady) {
    releaseAll();  // Clear any stale host key state after a reconnect.
    Serial.println("[BLE] Keyboard ready.");
  }
  wasReady = nowReady;
}

bool connected() { return linkConnected.load(); }

bool secure() { return linkConnected.load() && authenticated.load(); }

bool ready() {
  return linkConnected.load() && authenticated.load() && subscription != nullptr &&
         subscription->getNotifications();
}

bool sendReport(uint8_t modifiers, const uint8_t* keys, size_t count) {
  if (!ready() || count > 6 || (count != 0 && keys == nullptr)) {
    return false;
  }
  uint8_t report[8] = {modifiers, 0};
  for (size_t i = 0; i < count; ++i) {
    // Modifier keys belong in the first byte, not the ordinary-key array.
    if (keys[i] > 0x65 || (keys[i] != 0 && keys[i] < 0x04)) {
      return false;
    }
    report[i + 2] = keys[i];
  }
  input->setValue(report, sizeof(report));
  input->notify();
  return true;
}

bool releaseAll() { return sendReport(0, nullptr, 0); }

bool wheelReady() {
  return secure() && mouseSubscription != nullptr && mouseSubscription->getNotifications();
}
bool sendWheel(int8_t delta) {
  if (!wheelReady() || delta == 0 || delta == -128) return false;
  uint8_t report[4] = {0, 0, 0, static_cast<uint8_t>(delta)};
  wheelQueued.store(false);
  mouseInput->setValue(report, sizeof(report));
  mouseInput->notify();
  return wheelQueued.load();  // 本地提交结果，不能当成 Windows 接收确认。
}

bool consumerReady() {
  return secure() && consumerSubscription && consumerSubscription->getNotifications();
}
bool sendConsumer(uint16_t usage) {
  if (!consumerReady() || consumerHeld ||
      (usage != VOLUME_UP && usage != VOLUME_DOWN && usage != MUTE))
    return false;
  uint8_t report[2] = {uint8_t(usage), uint8_t(usage >> 8)};
  consumerInput->setValue(report, sizeof(report));
  consumerInput->notify();
  consumerHeld = true;
  consumerPressedAt = millis();
  return true;
}
uint8_t leds() { return hostLeds.load(); }

}  // namespace KeyboardBLE
