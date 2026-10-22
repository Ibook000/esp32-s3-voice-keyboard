#include "keymap.h"

#include <cstring>

#include "shortcut_config.h"

namespace Keymap {
Map defaults() {
  Map map = {};
  static_assert(sizeof(ShortcutConfig::KEYS) / sizeof(ShortcutConfig::KEYS[0]) == COUNT,
                "Web configuration supports exactly three shortcut keys");
  for (unsigned i = 0; i < COUNT; ++i) {
    map.keys[i].modifiers = ShortcutConfig::KEYS[i].modifiers;
    map.keys[i].key = ShortcutConfig::KEYS[i].key;
    std::strncpy(map.keys[i].label, ShortcutConfig::KEYS[i].label, LABEL_BYTES);
  }
  return map;
}
bool valid(const Binding& b) {
  if (b.key < 4 || b.key > 0x65 || b.label[LABEL_BYTES] != 0 || b.label[0] == ' ') return false;
  unsigned units = 0, i = 0;
  // 显示名称最多 2 个汉字或 4 个 ASCII 字符；严格检查 UTF-8 和控制字符。
  while (b.label[i]) {
    const uint8_t c = b.label[i];
    if (c >= 32 && c < 127) {
      ++units;
      ++i;
    } else {
      unsigned n = c >= 0xC2 && c <= 0xDF ? 2 : c >= 0xE0 && c <= 0xEF ? 3 : 0;
      if (!n || i + n > LABEL_BYTES) return false;
      uint32_t cp = c & (n == 2 ? 0x1F : 0x0F);
      for (unsigned j = 1; j < n; ++j) {
        const uint8_t next = b.label[i + j];
        if ((next & 0xC0) != 0x80) return false;
        cp = (cp << 6) | (next & 0x3F);
      }
      if ((n == 3 && cp < 0x800) || cp < 0xA0 || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
      units += 2;
      i += n;
    }
    if (units > 4 || i > LABEL_BYTES) return false;
  }
  return units > 0 && b.label[i - 1] != ' ';
}
bool equal(const Map& a, const Map& b) {
  if (a.rotaryMode != b.rotaryMode || a.sleepMinutes != b.sleepMinutes) return false;
  for (unsigned i = 0; i < COUNT; ++i)
    if (a.keys[i].modifiers != b.keys[i].modifiers || a.keys[i].key != b.keys[i].key ||
        std::strcmp(a.keys[i].label, b.keys[i].label))
      return false;
  return true;
}
void encodeMap(const Map& map, uint8_t* out) {
  for (unsigned i = 0; i < COUNT; ++i) {
    const auto& b = map.keys[i];
    auto* p = out + i * RECORD_BYTES;
    p[0] = b.modifiers;
    p[1] = b.key;
    p[2] = std::strlen(b.label);
    std::memset(p + 3, 0, LABEL_BYTES);
    std::memcpy(p + 3, b.label, p[2]);
  }
}
bool decodeMap(const uint8_t* input, size_t size, Map& map) {
  if (size != COUNT * RECORD_BYTES) return false;
  Map candidate = {};
  for (unsigned i = 0; i < COUNT; ++i) {
    const auto* p = input + i * RECORD_BYTES;
    auto& b = candidate.keys[i];
    if (p[2] > LABEL_BYTES) return false;
    b.modifiers = p[0];
    b.key = p[1];
    std::memcpy(b.label, p + 3, p[2]);
    if (std::strlen(b.label) != p[2] || !valid(b)) return false;
  }
  map = candidate;
  return true;
}
void Transaction::cancel() {
  transaction = 0;
  mask = 0;
}
void Transaction::apply(const uint8_t* p, size_t size, bool canChange, Persist persist) {
  result = BAD_PACKET;
  ackTx = size > 2 ? p[2] : 0;
  ackOp = size > 1 ? p[1] : 0;
  ackIndex = size > 3 ? p[3] : 255;
  if (size < 3 || p[0] != VERSION || !p[2]) return;
  if (p[1] == BEGIN && size == 3) {
    if (transaction && transaction != p[2]) {
      result = CONFLICT;
      return;
    }
    transaction = p[2];
    mask = 0;
    staged = active;
    result = OK;
    return;
  }
  if (transaction != p[2]) {
    result = CONFLICT;
    return;
  }
  if (p[1] == ABORT && size == 3) {
    cancel();
    result = OK;
    return;
  }
  if (p[1] == STAGE && size >= 7 && p[3] < COUNT && p[6] <= LABEL_BYTES &&
      size == size_t(7 + p[6])) {
    Binding b = {};
    b.modifiers = p[4];
    b.key = p[5];
    std::memcpy(b.label, p + 7, p[6]);
    if (std::strlen(b.label) != p[6] || !valid(b)) return;
    staged.keys[p[3]] = b;
    mask |= 1U << p[3];
    result = OK;
    return;
  }
  if (p[1] == STAGE_ROTARY && size == 4 && p[3] <= 1) {
    staged.rotaryMode = p[3];
    result = OK;
    return;
  }
  if (p[1] == STAGE_SLEEP && size == 5) {
    const uint16_t minutes = uint16_t(p[3]) | (uint16_t(p[4]) << 8);
    if (minutes > 120) return;
    staged.sleepMinutes = minutes;
    result = OK;
    return;
  }
  if (p[1] != COMMIT || size != 3) return;
  if (mask != 7) {
    result = INCOMPLETE;
    return;
  }
  if (!canChange) {
    result = BUSY;
    return;
  }
  if (!equal(active, staged)) {
    const uint16_t next = revision + 1;
    if (!persist || !persist(staged, next)) {
      result = STORE_FAILED;
      return;
    }
    active = staged;
    revision = next;
  }
  cancel();
  result = OK;
}
void Transaction::response(uint8_t* out) const {
  out[0] = VERSION;
  out[1] = result;
  out[2] = ackTx;
  out[3] = ackOp;
  out[4] = ackIndex;
  out[5] = mask;
  out[6] = revision;
  out[7] = revision >> 8;
  encodeMap(active, out + 8);
  out[53] = active.rotaryMode;
  out[54] = active.sleepMinutes;
  out[55] = active.sleepMinutes >> 8;
}
}  // namespace Keymap
