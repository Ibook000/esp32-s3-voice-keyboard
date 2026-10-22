#pragma once

#include <stddef.h>
#include <stdint.h>

#include "voice_protocol.h"

// Independent IMA ADPCM blocks, low nibble first, with CRC16-CCITT-FALSE.
// Wire format: count:u16, predictor:i16, step_index:u8, reserved:u8,
// ceil((count-1)/2) encoded bytes, crc:u16. All integers little endian.
namespace VoiceCodec {
constexpr size_t SAMPLES = VoiceProtocol::FRAME_SAMPLES;
constexpr size_t BLOCK_BYTES = 6 + SAMPLES / 2 + 2;
constexpr int STEPS[] = {
    7,     8,     9,     10,    11,    12,    13,    14,    16,    17,    19,   21,    23,
    25,    28,    31,    34,    37,    41,    45,    50,    55,    60,    66,   73,    80,
    88,    97,    107,   118,   130,   143,   157,   173,   190,   209,   230,  253,   279,
    307,   337,   371,   408,   449,   494,   544,   598,   658,   724,   796,  876,   963,
    1060,  1166,  1282,  1411,  1552,  1707,  1878,  2066,  2272,  2499,  2749, 3024,  3327,
    3660,  4026,  4428,  4871,  5358,  5894,  6484,  7132,  7845,  8630,  9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767};
constexpr int INDEX[] = {-1, -1, -1, -1, 2, 4, 6, 8};

inline void put16(uint8_t* p, uint16_t n) {
  p[0] = n;
  p[1] = n >> 8;
}
inline uint16_t crc16(const uint8_t* data, size_t size) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < size; ++i) {
    crc ^= uint16_t(data[i]) << 8;
    for (int b = 0; b < 8; ++b) {
      crc = (crc & 0x8000) ? (crc << 1) ^ 0x1021 : crc << 1;
    }
  }
  return crc;
}

inline size_t encode(const int16_t* samples, size_t count, uint8_t* out) {
  if (!samples || !out || count == 0 || count > SAMPLES) return 0;
  const size_t bytes = 6 + count / 2;
  for (size_t i = 0; i < bytes + 2; ++i) out[i] = 0;
  put16(out, count);
  int predictor = samples[0];
  put16(out + 2, static_cast<uint16_t>(predictor));
  int index = 0;
  if (count > 1) {
    int delta = int(samples[1]) - predictor;
    if (delta < 0) delta = -delta;
    while (index < 88 && STEPS[index] < delta) ++index;
  }
  out[4] = index;
  for (size_t i = 1; i < count; ++i) {
    const int step = STEPS[index];
    int delta = int(samples[i]) - predictor;
    uint8_t code = delta < 0 ? 8 : 0;
    if (delta < 0) delta = -delta;
    int diff = step >> 3;
    if (delta >= step) {
      code |= 4;
      delta -= step;
      diff += step;
    }
    if (delta >= (step >> 1)) {
      code |= 2;
      delta -= step >> 1;
      diff += step >> 1;
    }
    if (delta >= (step >> 2)) {
      code |= 1;
      diff += step >> 2;
    }
    predictor += (code & 8) ? -diff : diff;
    if (predictor > 32767) predictor = 32767;
    if (predictor < -32768) predictor = -32768;
    index += INDEX[code & 7];
    if (index < 0) index = 0;
    if (index > 88) index = 88;
    out[6 + (i - 1) / 2] |= code << (((i - 1) & 1) * 4);
  }
  put16(out + bytes, crc16(out, bytes));
  return bytes + 2;
}
}  // namespace VoiceCodec
