#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "keymap.h"
namespace {
Keymap::Map persisted = Keymap::defaults();
uint16_t storedRevision = 0;
bool storageWorks = true;
bool persist(const Keymap::Map& map, uint16_t revision) {
  if (!storageWorks) return false;
  persisted = map;
  storedRevision = revision;
  return true;
}
}  // namespace
int main() {
  Keymap::Transaction engine;
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line == "reboot") {
      engine = Keymap::Transaction();
      engine.active = persisted;
      engine.revision = storedRevision;
    } else {
      std::istringstream input(line);
      std::string hex;
      int canChange = 1, store = 1;
      input >> hex >> canChange >> store;
      storageWorks = store;
      std::vector<uint8_t> packet;
      if (hex != "-")
        for (size_t i = 0; i < hex.size(); i += 2)
          packet.push_back(std::stoi(hex.substr(i, 2), nullptr, 16));
      engine.apply(packet.data(), packet.size(), canChange, persist);
    }
    uint8_t bytes[Keymap::RESPONSE_BYTES];
    engine.response(bytes);
    for (auto byte : bytes)
      std::cout << std::hex << std::setfill('0') << std::setw(2) << unsigned(byte);
    std::cout << std::endl;
  }
}
