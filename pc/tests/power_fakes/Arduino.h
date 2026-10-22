#pragma once
#include "../fakes/Arduino.h"
struct TestSerial { void println(const char*) {} void flush() {} };
extern TestSerial Serial;
