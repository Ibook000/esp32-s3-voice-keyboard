#include "keymap_store.h"

#include <Preferences.h>
namespace {
Keymap::Transaction config;
constexpr size_t LEGACY_BYTES = 4 + Keymap::COUNT * Keymap::RECORD_BYTES;
constexpr size_t ROTARY_BYTES = LEGACY_BYTES + 1;
constexpr size_t BYTES = LEGACY_BYTES + 3;
}  // namespace
namespace KeymapStore {
void begin() {
  config = Keymap::Transaction();
  Preferences prefs;
  if (!prefs.begin("keymap", true)) return;
  uint8_t bytes[BYTES] = {};
  const size_t length = prefs.getBytesLength("config");
  const bool read = (length == BYTES || length == ROTARY_BYTES || length == LEGACY_BYTES) &&
                    prefs.getBytes("config", bytes, length) == length;
  prefs.end();
  Keymap::Map map;
  if (read && bytes[0] == Keymap::VERSION && bytes[1] == Keymap::COUNT &&
      Keymap::decodeMap(bytes + 4, LEGACY_BYTES - 4, map) &&
      (length == LEGACY_BYTES || bytes[LEGACY_BYTES] <= 1) &&
      (length != BYTES || (uint16_t(bytes[50]) | (uint16_t(bytes[51]) << 8)) <= 120)) {
    map.rotaryMode = length >= ROTARY_BYTES ? bytes[LEGACY_BYTES] : 0;
    map.sleepMinutes = length == BYTES ? uint16_t(bytes[50]) | (uint16_t(bytes[51]) << 8) : 10;
    config.active = map;
    config.revision = uint16_t(bytes[2]) | (uint16_t(bytes[3]) << 8);
  }
}
Keymap::Transaction& transaction() { return config; }
const Keymap::Map& current() { return config.active; }
bool persist(const Keymap::Map& map, uint16_t revision) {
  uint8_t bytes[BYTES] = {Keymap::VERSION, Keymap::COUNT, uint8_t(revision),
                          uint8_t(revision >> 8)};
  Keymap::encodeMap(map, bytes + 4);
  bytes[LEGACY_BYTES] = map.rotaryMode;
  bytes[50] = map.sleepMinutes;
  bytes[51] = map.sleepMinutes >> 8;
  Preferences prefs;
  if (!prefs.begin("keymap", false)) return false;
  const bool ok = prefs.putBytes("config", bytes, BYTES) == BYTES;
  prefs.end();
  return ok;
}
}  // namespace KeymapStore
