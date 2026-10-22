#pragma once
class BLEService;
namespace KeymapBLE {
bool busy();
void begin(BLEService* service);
void update(bool secure, bool canChange);
}  // namespace KeymapBLE
