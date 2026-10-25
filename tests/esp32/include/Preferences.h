#pragma once

class Preferences {
 public:
  void begin(const char*, bool) {}
  bool getBool(const char*, bool defaultValue) { return defaultValue; }
  void putBool(const char*, bool) {}
};
