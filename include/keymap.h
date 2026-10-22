#pragma once
#include <stddef.h>
#include <stdint.h>

namespace Keymap {
constexpr uint8_t VERSION = 1, COUNT = 3, LABEL_BYTES = 12;
constexpr size_t RECORD_BYTES = 15, RESPONSE_BYTES = 11 + COUNT * RECORD_BYTES;
constexpr char UUID[] = "cba10004-7d2f-4a6b-9c11-18d5e0010001";
constexpr char STATUS_UUID[] = "cba10005-7d2f-4a6b-9c11-18d5e0010001";
enum Op : uint8_t {
  BEGIN = 1,
  STAGE = 2,
  COMMIT = 3,
  ABORT = 4,
  STAGE_ROTARY = 5,
  STAGE_SLEEP = 6
};
enum Result : uint8_t { OK, BAD_PACKET, CONFLICT, INCOMPLETE, BUSY, STORE_FAILED };
struct Binding {
  uint8_t modifiers, key;
  char label[LABEL_BYTES + 1];
};
struct Map {
  Binding keys[COUNT];
  uint16_t sleepMinutes = 10;  // 0=关闭，1..120=闲置后深睡分钟数。
  uint8_t rotaryMode = 0;      // 0=滚动/Enter，1=音量/静音。
};
Map defaults();
bool valid(const Binding& binding);
bool equal(const Map& a, const Map& b);
void encodeMap(const Map& map, uint8_t* output);
bool decodeMap(const uint8_t* input, size_t size, Map& map);

// 主循环独占事务；完整校验且持久化成功后才替换 active，失败保留原配置。
class Transaction {
 public:
  Map active = defaults();
  uint16_t revision = 0;
  using Persist = bool (*)(const Map&, uint16_t);
  void apply(const uint8_t* packet, size_t size, bool canChange, Persist persist);
  void cancel();
  bool staging() const { return transaction != 0; }
  void response(uint8_t* output) const;

 private:
  Map staged = {};
  uint8_t transaction = 0, mask = 0, ackTx = 0, ackOp = 0, ackIndex = 255;
  Result result = OK;
};
}  // namespace Keymap
