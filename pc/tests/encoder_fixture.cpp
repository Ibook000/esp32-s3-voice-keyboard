#include <cstdio>

#include "ima_adpcm.h"
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

int main() {
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
  _setmode(_fileno(stdout), _O_BINARY);
#endif
  uint16_t count;
  if (std::fread(&count, sizeof(count), 1, stdin) != 1 || count > VoiceCodec::SAMPLES) return 1;
  int16_t samples[VoiceCodec::SAMPLES];
  if (std::fread(samples, sizeof(int16_t), count, stdin) != count) return 2;
  uint8_t encoded[VoiceCodec::BLOCK_BYTES];
  const auto size = VoiceCodec::encode(samples, count, encoded);
  return std::fwrite(encoded, 1, size, stdout) == size ? 0 : 3;
}
