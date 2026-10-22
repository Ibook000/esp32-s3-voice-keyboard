#pragma once
#include "keymap.h"
namespace KeymapStore {
void begin();
Keymap::Transaction& transaction();
const Keymap::Map& current();
bool persist(const Keymap::Map& map, uint16_t revision);
}  // namespace KeymapStore
