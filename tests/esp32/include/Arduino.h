#pragma once

#include <algorithm>
#include <cctype>
#include <cstdarg>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <sstream>
#include <string>

constexpr int LOW = 0;
constexpr int HIGH = 1;
constexpr int INPUT = 0;
constexpr int OUTPUT = 1;
constexpr int INPUT_PULLUP = 2;

extern unsigned long mockMillis;

class String {
 public:
  String() = default;
  String(const char* value) : value_(value == nullptr ? "" : value) {}
  String(const std::string& value) : value_(value) {}

  std::size_t length() const { return value_.length(); }
  char charAt(std::size_t index) const { return value_.at(index); }

  int indexOf(char needle, std::size_t from = 0) const {
    const std::size_t found = value_.find(needle, from);
    return found == std::string::npos ? -1 : static_cast<int>(found);
  }

  String substring(std::size_t from) const { return value_.substr(from); }
  String substring(std::size_t from, std::size_t to) const {
    return value_.substr(from, to - from);
  }

  bool startsWith(const char* prefix) const {
    return value_.rfind(prefix, 0) == 0;
  }

  long toInt() const {
    char* end = nullptr;
    const long parsed = std::strtol(value_.c_str(), &end, 10);
    return end == value_.c_str() ? 0 : parsed;
  }

  void trim() {
    const auto isSpace = [](unsigned char value) {
      return std::isspace(value) != 0;
    };
    const auto first = std::find_if_not(value_.begin(), value_.end(), isSpace);
    const auto last = std::find_if_not(value_.rbegin(), value_.rend(), isSpace)
                          .base();
    value_ = first < last ? std::string(first, last) : std::string();
  }

  void toUpperCase() {
    std::transform(
        value_.begin(),
        value_.end(),
        value_.begin(),
        [](unsigned char value) { return std::toupper(value); });
  }

  const char* c_str() const { return value_.c_str(); }

  bool operator==(const String& other) const { return value_ == other.value_; }
  bool operator==(const char* other) const { return value_ == other; }
  bool operator!=(const char* other) const { return !(*this == other); }

 private:
  std::string value_;

  friend std::ostream& operator<<(std::ostream& stream, const String& value);
};

inline std::ostream& operator<<(std::ostream& stream, const String& value) {
  return stream << value.value_;
}

inline bool operator==(const char* left, const String& right) {
  return right == left;
}

inline bool operator!=(const char* left, const String& right) {
  return !(left == right);
}

class FakeSerial {
 public:
  void begin(unsigned long) {}
  int available() const { return 0; }
  String readStringUntil(char) { return String(); }

  void clear() { output.clear(); }

  template <typename Value>
  void print(const Value& value) {
    std::ostringstream stream;
    stream << value;
    output += stream.str();
  }

  void println() { output += '\n'; }

  template <typename Value>
  void println(const Value& value) {
    print(value);
    println();
  }

  int printf(const char* format, ...) {
    char buffer[256];
    va_list args;
    va_start(args, format);
    const int written = std::vsnprintf(buffer, sizeof(buffer), format, args);
    va_end(args);
    if (written > 0) {
      const std::size_t length = std::min(
          static_cast<std::size_t>(written),
          sizeof(buffer) - 1);
      output.append(buffer, length);
    }
    return written;
  }

  std::string output;
};

extern FakeSerial Serial;

inline unsigned long millis() { return mockMillis; }
inline void delay(unsigned long) {}
inline void delayMicroseconds(unsigned int) {}
inline void pinMode(int, int) {}
inline void digitalWrite(int, int) {}
inline int digitalRead(int) { return HIGH; }
inline unsigned long pulseIn(int, int, unsigned long) { return 0; }
inline void ledcAttach(int, int, int) {}
inline void ledcWrite(int, int) {}
inline bool isDigit(char value) {
  return std::isdigit(static_cast<unsigned char>(value)) != 0;
}
