#pragma once

#include <stddef.h>
#include <stdint.h>

// Keep wire values in sync with pc/voice_protocol.py and docs/voice-protocol.md.
namespace VoiceProtocol {
constexpr char SERVICE_UUID[] = "cba10000-7d2f-4a6b-9c11-18d5e0010001";
constexpr char CONTROL_UUID[] = "cba10001-7d2f-4a6b-9c11-18d5e0010001";
constexpr char AUDIO_UUID[] = "cba10002-7d2f-4a6b-9c11-18d5e0010001";
constexpr char STATUS_UUID[] = "cba10003-7d2f-4a6b-9c11-18d5e0010001";

constexpr uint8_t VERSION = 1;
constexpr uint8_t STATUS_TYPE = 0x10;
constexpr uint8_t AUDIO_TYPE = 0x20;
constexpr unsigned SAMPLE_RATE = 16000;
constexpr unsigned FRAME_DURATION_MS = 20;
constexpr size_t FRAME_SAMPLES = SAMPLE_RATE * FRAME_DURATION_MS / 1000;
constexpr size_t STATUS_BYTES = 20;
constexpr size_t FRAGMENT_HEADER_BYTES = 8;
constexpr size_t MAX_NOTIFICATION_BYTES = 244;
constexpr uint16_t MIN_MTU = 23;
constexpr uint16_t PREFERRED_MTU = 247;
}  // namespace VoiceProtocol
