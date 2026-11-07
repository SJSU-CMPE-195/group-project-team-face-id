#include <LiquidCrystal.h>
#include <Preferences.h>
#include <esp_system.h>

// =============================================
// ESP32 Relay Control — 2003 Accord Face Access
// Raspberry Pi link:  UART2 (GPIO16 RX / GPIO17 TX)
// 4 relays:           imitate the car's own lock/key switches
// HC-SR04 sensor:     Face-auth trigger (person walks up)
// 1602A LCD:          Status display
//
// Relay contacts switch a car wire to that circuit's ground, exactly like a
// button press or key turn. Car wires go on NO and COM only, never NC.
// =============================================

// Pi link (Serial2). Pi GPIO14/TXD -> GPIO16, Pi GPIO15/RXD <- GPIO17, GND-GND
const int PI_RX = 16;
const int PI_TX = 17;
const unsigned long PI_BAUD = 115200;
HardwareSerial& piLink = Serial2;

// Relay inputs (trigger jumpers on H = active HIGH, 10k pull-down to DC-)
const int DISARM_RELAY = 26;  // driver WHITE (gray key-cylinder plug)
const int UNLOCK_RELAY = 27;  // passenger PINK/BLUE (moved from 25, the LCD uses it)
const int LOCK_RELAY   = 32;  // passenger PINK/BLACK
const int ARM_RELAY    = 33;  // driver WHITE/RED (gray key-cylinder plug)
const int RELAY_PINS[] = {DISARM_RELAY, UNLOCK_RELAY, LOCK_RELAY, ARM_RELAY};

// Pulse lengths tested on the car (ARM matches DISARM until tested)
const unsigned long DISARM_MS = 1000;
const unsigned long UNLOCK_MS = 500;
const unsigned long LOCK_MS   = 500;
const unsigned long ARM_MS    = 1000;
const unsigned long GAP_MS    = 500;

// 1602 LCD in 4-bit parallel mode (RW tied to GND)
const int LCD_RS = 15;  // moved from 19 (that line was shorted to GND)
const int LCD_E  = 23;
const int LCD_D4 = 13;
const int LCD_D5 = 25;
const int LCD_D6 = 18;  // moved from 17, now UART2 TX to the Pi
const int LCD_D7 = 4;

// HC-SR04 ultrasonic sensor (moved off 32/33, now the LOCK/ARM relays).
// ECHO is 5 V: wire it through a 1k/2k divider. GPIO34 is input-only.
const int TRIG_PIN = 21;
const int ECHO_PIN = 34;

// Pi scans time out after 20 s; give it margin before giving up
const unsigned long AUTH_TIMEOUT_MS = 30000;

// Presence detection: 20-30 cm is a dead band so it doesn't flicker
const float PRESENT_CM = 20.0;
const float GONE_CM = 30.0;
const int PRESENCE_READINGS = 3;
const unsigned long SENSOR_INTERVAL_MS = 100;
const unsigned long ECHO_TIMEOUT_US = 25000;

// After a failed result, wait for the person to leave (or this long) before scanning again
const unsigned long REARM_MS = 10000;
// How long a result stays on the LCD before going back to "Ready"
const unsigned long RESULT_SHOW_MS = 3000;

// Bench testing from a laptop. Auth replies are only ever accepted from the Pi.
const bool ALLOW_USB_COMMANDS = true;

// Plain LOCK (what the Pi sends automatically) and USB ARM are ignored, so our
// system never locks or arms this car on its own. SECURE is sent only for a
// PIN-authorized phone Lock and runs lock + arm. The physical key is unaffected.
const bool ALLOW_LOCK = false;

LiquidCrystal lcd(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7);

// Last completed lock sequence, saved so a reset doesn't forget it. The car
// gives no feedback, so this is what we last told it, not a measured state.
Preferences lockStore;
bool locked = false;

// Relay sequences: one relay at a time, pin -1 is a pause with everything off
struct Step {
  int pin;
  unsigned long ms;
};
const Step ENTRY_STEPS[] = {
  {DISARM_RELAY, DISARM_MS}, {-1, GAP_MS}, {UNLOCK_RELAY, UNLOCK_MS}
};
const Step SECURE_STEPS[] = {
  {LOCK_RELAY, LOCK_MS}, {-1, GAP_MS}, {ARM_RELAY, ARM_MS}
};
const Step DISARM_STEPS[] = {{DISARM_RELAY, DISARM_MS}};
const Step ARM_STEPS[] = {{ARM_RELAY, ARM_MS}};

enum Sequence { SEQ_NONE, SEQ_ENTRY, SEQ_SECURE, SEQ_DISARM, SEQ_ARM };

Sequence activeSequence = SEQ_NONE;
const Step* sequenceSteps = nullptr;
int sequenceLength = 0;
int sequenceIndex = 0;
unsigned long stepStartedAt = 0;

// Authentication state machine
enum AuthState { IDLE, WAITING_AUTH };

AuthState authState = IDLE;
unsigned long authStartedAt = 0;
uint32_t authBootNonce = 0;
uint32_t authRequestCounter = 0;
char pendingRequestId[17] = "";

// Presence sensor
bool personPresent = false;
int nearCount = 0;
int farCount = 0;
unsigned long lastSensorRead = 0;
bool sensorArmed = true;
unsigned long disarmedAt = 0;
bool rearmWhenGoneOnly = false;

// LCD
bool lcdReady = false;
char lcdLines[2][17] = {"", ""};
const char* resultText = nullptr;
unsigned long resultShownAt = 0;

// Serial line buffers
const size_t MAX_LINE_LENGTH = 64;
char piLine[MAX_LINE_LENGTH + 1];
size_t piLineLength = 0;
bool piLineOverflow = false;
char usbLine[MAX_LINE_LENGTH + 1];
size_t usbLineLength = 0;
bool usbLineOverflow = false;

void allRelaysOff() {
  for (int pin : RELAY_PINS) {
    digitalWrite(pin, LOW);
  }
}

void setup() {
  // Relays first: drive LOW before enabling outputs so nothing clicks at boot
  for (int pin : RELAY_PINS) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  allRelaysOff();

  Serial.begin(115200);
  piLink.begin(PI_BAUD, SERIAL_8N1, PI_RX, PI_TX);
  authBootNonce = esp_random();

  lockStore.begin("accord", false);
  locked = lockStore.getBool("locked", false);

  // Distance sensor
  pinMode(TRIG_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_PIN, INPUT);

  // RS must read HIGH here. LOW means the RS line is shorted to GND:
  // don't drive into it, and keep the car working without the LCD.
  pinMode(LCD_RS, INPUT_PULLUP);
  delay(5);
  if (digitalRead(LCD_RS) == LOW) {
    Serial.printf("LCD RS (GPIO%d) reads LOW - shorted to GND, LCD disabled\n", LCD_RS);
  } else {
    delay(100);  // let the LCD controller finish its own power-up
    lcd.begin(16, 2);
    lcdReady = true;
  }

  report("=== Accord Face Access ===");
  Serial.println("Commands: lock, unlock, stop, status (USB also: disarm, arm)");
  if (!ALLOW_LOCK) {
    Serial.println("Plain LOCK/ARM ignored (ALLOW_LOCK = false); SECURE (phone lock) locks and arms");
  }
  Serial.println("Walking up sends START_AUTH UNLOCK <request_id> to the Pi");
  report(locked ? "Restored state: Locked" : "Restored state: Unlocked");
  updateLcd();
}

// Status lines go to the Pi (it ignores what it doesn't parse) and the USB log
void report(const char* text) {
  piLink.println(text);
  Serial.println(text);
}

// ── Relay sequences ──────────────────────────────────────────────────────

const char* sequenceName(Sequence sequence) {
  switch (sequence) {
    case SEQ_ENTRY:  return "ENTRY";
    case SEQ_SECURE: return "SECURE";
    case SEQ_DISARM: return "DISARM";
    case SEQ_ARM:    return "ARM";
    default:         return "NONE";
  }
}

void beginStep() {
  allRelaysOff();
  int pin = sequenceSteps[sequenceIndex].pin;
  if (pin >= 0) {
    digitalWrite(pin, HIGH);
  }
  stepStartedAt = millis();
}

void abortSequence() {
  allRelaysOff();
  if (activeSequence == SEQ_NONE) {
    return;
  }
  char line[24];
  snprintf(line, sizeof(line), "ABORT %s", sequenceName(activeSequence));
  report(line);
  activeSequence = SEQ_NONE;
}

// The newest command wins: the Pi sends STOP then LOCK back to back, and a
// compensating LOCK right after UNLOCK, so a running sequence is replaced.
void startSequence(Sequence sequence) {
  abortSequence();
  switch (sequence) {
    case SEQ_ENTRY:  sequenceSteps = ENTRY_STEPS;  sequenceLength = 3; break;
    case SEQ_SECURE: sequenceSteps = SECURE_STEPS; sequenceLength = 3; break;
    case SEQ_DISARM: sequenceSteps = DISARM_STEPS; sequenceLength = 1; break;
    case SEQ_ARM:    sequenceSteps = ARM_STEPS;    sequenceLength = 1; break;
    default: return;
  }
  activeSequence = sequence;
  sequenceIndex = 0;
  char line[24];
  snprintf(line, sizeof(line), "START %s", sequenceName(sequence));
  report(line);
  beginStep();
}

void finishSequence() {
  allRelaysOff();
  Sequence finished = activeSequence;
  activeSequence = SEQ_NONE;
  if (finished == SEQ_ENTRY || finished == SEQ_SECURE) {
    locked = finished == SEQ_SECURE;
    lockStore.putBool("locked", locked);
  }
  char line[24];
  snprintf(line, sizeof(line), "DONE %s", sequenceName(finished));
  report(line);
}

void runSequence() {
  if (activeSequence == SEQ_NONE) {
    return;
  }
  if (millis() - stepStartedAt < sequenceSteps[sequenceIndex].ms) {
    return;
  }
  sequenceIndex++;
  if (sequenceIndex >= sequenceLength) {
    finishSequence();
  } else {
    beginStep();
  }
}

// ── LCD ──────────────────────────────────────────────────────────────────

// Writes a row padded to 16 characters, only when its text changed
void lcdWrite(int row, const char* text) {
  char padded[17];
  snprintf(padded, sizeof(padded), "%-16.16s", text);
  if (strcmp(padded, lcdLines[row]) == 0) {
    return;
  }
  strcpy(lcdLines[row], padded);
  if (lcdReady) {
    lcd.setCursor(0, row);
    lcd.print(padded);
  }
}

void showResult(const char* text) {
  resultText = text;
  resultShownAt = millis();
}

void updateLcd() {
  const char* status = "Ready";
  if (activeSequence == SEQ_ENTRY) {
    status = "Unlocking...";
  } else if (activeSequence == SEQ_SECURE) {
    status = "Locking...";
  } else if (activeSequence == SEQ_DISARM) {
    status = "Disarming...";
  } else if (activeSequence == SEQ_ARM) {
    status = "Arming...";
  } else if (authState == WAITING_AUTH) {
    status = "Scanning...";
  } else if (resultText != nullptr && millis() - resultShownAt < RESULT_SHOW_MS) {
    status = resultText;
  }
  lcdWrite(0, status);
  lcdWrite(1, locked ? "Doors: LOCKED" : "Doors: UNLOCKED");
}

// ── Presence sensor ──────────────────────────────────────────────────────

// Returns distance in cm, or -1 if no echo (nothing in range)
float readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long us = pulseIn(ECHO_PIN, HIGH, ECHO_TIMEOUT_US);
  if (us == 0) return -1;
  return us / 58.0;
}

// Don't scan again until the person leaves or REARM_MS passes
void disarmSensor() {
  sensorArmed = false;
  rearmWhenGoneOnly = false;
  disarmedAt = millis();
}

void rearmSensor() {
  if (!sensorArmed) {
    sensorArmed = true;
    rearmWhenGoneOnly = false;
    Serial.println("Re-armed");
  }
}

void pollSensor() {
  // pulseIn blocks up to 25 ms, so never read while relays run or a scan runs
  if (authState != IDLE || activeSequence != SEQ_NONE) {
    return;
  }
  if (millis() - lastSensorRead < SENSOR_INTERVAL_MS) {
    return;
  }
  lastSensorRead = millis();

  float cm = readDistanceCm();
  if (cm > 0 && cm <= PRESENT_CM) {
    farCount = 0;
    if (nearCount < PRESENCE_READINGS) nearCount++;
  } else if (cm < 0 || cm >= GONE_CM) {
    nearCount = 0;
    if (farCount < PRESENCE_READINGS) farCount++;
  } else {
    nearCount = 0;
    farCount = 0;
  }

  if (!personPresent && nearCount >= PRESENCE_READINGS) {
    personPresent = true;
    Serial.printf("Presence detected (%.0f cm)\n", cm);
  } else if (personPresent && farCount >= PRESENCE_READINGS) {
    personPresent = false;
    report("Person gone");
    rearmSensor();
  }

  // After a successful entry, someone standing at the door isn't rescanned
  if (!sensorArmed && !rearmWhenGoneOnly && millis() - disarmedAt >= REARM_MS) {
    rearmSensor();
  }
  if (personPresent && sensorArmed) {
    requestAuth();
  }
}

// ── Authentication flow ──────────────────────────────────────────────────

void sendAuthMessage(const char* command, const char* requestId) {
  piLink.print(command);
  piLink.print(" ");
  piLink.println(requestId);
  Serial.printf("-> Pi: %s %s\n", command, requestId);
}

void invalidateAuthRequest() {
  authState = IDLE;
  pendingRequestId[0] = '\0';
}

bool authRequestExpired() {
  return authState == WAITING_AUTH
      && millis() - authStartedAt >= AUTH_TIMEOUT_MS;
}

void cancelPendingAuth(const char* reason) {
  if (authState != WAITING_AUTH) {
    return;
  }
  sendAuthMessage("AUTH_CANCEL", pendingRequestId);
  Serial.print("Authentication cancelled: ");
  Serial.println(reason);
  invalidateAuthRequest();
}

void expireAuthRequest() {
  cancelPendingAuth("timeout");
  disarmSensor();
  showResult("Denied");
}

void createRequestId() {
  authRequestCounter++;
  snprintf(
    pendingRequestId,
    sizeof(pendingRequestId),
    "%08lX%08lX",
    static_cast<unsigned long>(authBootNonce),
    static_cast<unsigned long>(authRequestCounter)
  );
}

// The car gives no lock feedback, so every walk-up asks for an unlock
void requestAuth() {
  if (authState != IDLE) {
    return;
  }
  authState = WAITING_AUTH;
  authStartedAt = millis();
  createRequestId();
  resultText = nullptr;
  sendAuthMessage("START_AUTH UNLOCK", pendingRequestId);
}

void finishDeniedAuth(const char* lcdText) {
  invalidateAuthRequest();
  disarmSensor();
  showResult(lcdText);
}

void acceptAuthorizedReply() {
  // ACK as soon as the sequence starts: the Pi waits only 2 s for it
  startSequence(SEQ_ENTRY);
  sendAuthMessage("AUTH_ACK", pendingRequestId);
  invalidateAuthRequest();
  disarmSensor();
  rearmWhenGoneOnly = true;
  showResult("Welcome");
}

void handleAuthReply(const char* cmd, const char* requestId) {
  if (authState != WAITING_AUTH) {
    Serial.printf("Ignored %s: no authentication pending\n", cmd);
    return;
  }
  // A reply at or after the deadline cannot revive an expired request.
  if (authRequestExpired()) {
    expireAuthRequest();
    Serial.printf("Ignored expired reply: %s\n", cmd);
    return;
  }
  if (strcmp(requestId, pendingRequestId) != 0) {
    Serial.printf("Ignored %s: request ID does not match\n", cmd);
    return;
  }
  if (strcmp(cmd, "AUTH_PENDING") == 0) {
    authStartedAt = millis();
    Serial.println("Pi is scanning...");
  } else if (strcmp(cmd, "AUTHORIZED") == 0) {
    Serial.println("Access granted");
    acceptAuthorizedReply();
  } else {
    Serial.printf("Access not granted: %s\n", cmd);
    const char* text = "Error";
    if (strcmp(cmd, "UNAUTHORIZED") == 0) text = "Access denied";
    else if (strcmp(cmd, "DENIED") == 0) text = "Denied";
    else if (strcmp(cmd, "BUSY") == 0) text = "Busy";
    finishDeniedAuth(text);
  }
}

void checkAuthTimeout() {
  if (authRequestExpired()) {
    Serial.println("No reply from Pi; authentication timed out");
    expireAuthRequest();
  }
}

// ── Serial commands ──────────────────────────────────────────────────────

bool isAuthReplyCommand(const char* command) {
  return strcmp(command, "AUTH_PENDING") == 0 || strcmp(command, "AUTHORIZED") == 0
      || strcmp(command, "UNAUTHORIZED") == 0 || strcmp(command, "DENIED") == 0
      || strcmp(command, "BUSY") == 0 || strcmp(command, "ERROR") == 0;
}

bool isRequestId(const char* requestId) {
  if (strlen(requestId) != 16) {
    return false;
  }
  for (size_t i = 0; i < 16; i++) {
    char value = requestId[i];
    if (!isDigit(value) && (value < 'A' || value > 'F')) {
      return false;
    }
  }
  return true;
}

void reportStatus() {
  char line[64];
  snprintf(
    line,
    sizeof(line),
    "STATUS %s SEQ:%s AUTH:%s",
    locked ? "LOCKED" : "UNLOCKED",
    sequenceName(activeSequence),
    authState == WAITING_AUTH ? "WAITING" : "IDLE"
  );
  report(line);
}

// Uppercases and trims the line in place
void handleLine(char* line, bool fromPi) {
  char* start = line;
  while (*start == ' ' || *start == '\t') start++;
  size_t length = strlen(start);
  while (length > 0 && (start[length - 1] == ' ' || start[length - 1] == '\t')) {
    start[--length] = '\0';
  }
  for (size_t i = 0; i < length; i++) {
    start[i] = toupper(static_cast<unsigned char>(start[i]));
  }
  if (length == 0) {
    return;
  }

  if (strcmp(start, "UNLOCK") == 0 || strcmp(start, "ENTRY") == 0) {
    cancelPendingAuth("manual UNLOCK");
    startSequence(SEQ_ENTRY);
    disarmSensor();

  } else if (strcmp(start, "SECURE") == 0) {
    // Phone Lock (PIN-authorized on the Pi): lock the doors and arm the alarm
    cancelPendingAuth("manual SECURE");
    startSequence(SEQ_SECURE);
    disarmSensor();

  } else if (strcmp(start, "LOCK") == 0) {
    cancelPendingAuth("manual LOCK");
    if (ALLOW_LOCK) {
      startSequence(SEQ_SECURE);
    } else {
      report("Locking disabled; LOCK ignored");
    }
    disarmSensor();

  } else if (strcmp(start, "STOP") == 0 || strcmp(start, "OFF") == 0) {
    cancelPendingAuth("manual STOP");
    abortSequence();
    disarmSensor();
    report("All relays OFF");

  } else if (strcmp(start, "START") == 0 || strcmp(start, "ON") == 0) {
    report("No ignition relay on this car; START ignored");

  } else if (strcmp(start, "STATUS") == 0) {
    reportStatus();

  } else if (!fromPi && strcmp(start, "DISARM") == 0) {
    startSequence(SEQ_DISARM);

  } else if (!fromPi && strcmp(start, "ARM") == 0) {
    if (ALLOW_LOCK) {
      startSequence(SEQ_ARM);
    } else {
      report("Locking disabled; ARM ignored");
    }

  } else {
    char* separator = strchr(start, ' ');
    if (separator != nullptr) {
      *separator = '\0';
    }
    if (!isAuthReplyCommand(start)) {
      Serial.println("Unknown command. Use: LOCK, UNLOCK, STOP, STATUS");
    } else if (!fromPi) {
      Serial.println("Ignored authentication reply: only the Pi link may send it");
    } else if (
      separator == nullptr
      || strchr(separator + 1, ' ') != nullptr
      || !isRequestId(separator + 1)
    ) {
      Serial.println("Ignored authentication reply: expected command plus request ID");
    } else {
      handleAuthReply(start, separator + 1);
    }
  }
}

// Collects one newline-terminated line without blocking; overlong lines are dropped
void readLines(Stream& port, char* buffer, size_t& length, bool& overflow, bool fromPi) {
  while (port.available()) {
    char value = port.read();
    if (value == '\r') {
      continue;
    }
    if (value == '\n') {
      buffer[length] = '\0';
      if (!overflow) {
        handleLine(buffer, fromPi);
      }
      length = 0;
      overflow = false;
    } else if (length < MAX_LINE_LENGTH) {
      buffer[length++] = value;
    } else {
      overflow = true;
    }
  }
}

void loop() {
  runSequence();
  readLines(piLink, piLine, piLineLength, piLineOverflow, true);
  if (ALLOW_USB_COMMANDS) {
    readLines(Serial, usbLine, usbLineLength, usbLineOverflow, false);
  }
  pollSensor();
  checkAuthTimeout();
  updateLcd();
}
