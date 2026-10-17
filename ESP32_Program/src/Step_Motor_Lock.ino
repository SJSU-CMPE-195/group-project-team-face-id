#include <AccelStepper.h>
#include <LiquidCrystal.h>
#include <Preferences.h>

// =============================================
// ESP32 Motor Control — Car Security System
// DC Motor A (OUT1/OUT2): Engine ignition
// Stepper Motor (ULN2003):  Lock/Unlock
// HC-SR04 sensor:           Face-auth trigger (person walks up)
// 1602A LCD:                Status display
// =============================================

// DC Motor pins (ignition)
const int ENA = 14;
const int IN1 = 26;
const int IN2 = 27;

// Stepper Motor pins (lock) via ULN2003
const int STEP_IN1 = 18;
const int STEP_IN2 = 16;
const int STEP_IN3 = 21;
const int STEP_IN4 = 22;

// 1602 LCD in 4-bit parallel mode (RW tied to GND)
const int LCD_RS = 15;  // moved from 19 (that line was shorted to GND)
const int LCD_E  = 23;
const int LCD_D4 = 13;
const int LCD_D5 = 25;
const int LCD_D6 = 17;
const int LCD_D7 = 4;

// HC-SR04 ultrasonic sensor (ECHO is 5 V: wire it through a 1k/2k divider)
const int TRIG_PIN = 32;
const int ECHO_PIN = 33;

// 28BYJ-48 step count for lock/unlock position
const int LOCK_STEPS = 2048;

// Absolute stepper positions, so repeated LOCK/UNLOCK never over-rotate
const long UNLOCKED_POSITION = 0;
const long LOCKED_POSITION = LOCK_STEPS;

// Pi scans time out after 20 s; give it margin before giving up
const unsigned long AUTH_TIMEOUT_MS = 30000;

// Presence detection: 20-30 cm is a dead band so it doesn't flicker
const float PRESENT_CM = 20.0;
const float GONE_CM = 30.0;
const int PRESENCE_READINGS = 3;
const unsigned long SENSOR_INTERVAL_MS = 100;
const unsigned long ECHO_TIMEOUT_US = 25000;

// After a result, wait for the person to leave (or this long) before scanning again
const unsigned long REARM_MS = 10000;
// After an authorized unlock, start the ignition scan if the person is still there
const unsigned long AUTO_IGNITION_MS = 5000;
// How long a result stays on the LCD before going back to "Ready"
const unsigned long RESULT_SHOW_MS = 3000;

AccelStepper lockMotor(AccelStepper::HALF4WIRE, STEP_IN1, STEP_IN3, STEP_IN2, STEP_IN4);
LiquidCrystal lcd(LCD_RS, LCD_E, LCD_D4, LCD_D5, LCD_D6, LCD_D7);

// Lock state saved in flash so a reset or power loss doesn't forget where the lock is
Preferences lockStore;

int currentSpeed = 200;
bool motorRunning = false;
bool locked = false;
bool stepperMoving = false;

// Authentication state machine
enum AuthState { IDLE, WAITING_AUTH };
enum AuthPurpose { PURPOSE_UNLOCK, PURPOSE_IGNITION };

AuthState authState = IDLE;
AuthPurpose pendingPurpose = PURPOSE_UNLOCK;
unsigned long authStartedAt = 0;

// Presence sensor
bool personPresent = false;
int nearCount = 0;
int farCount = 0;
unsigned long lastSensorRead = 0;
bool sensorArmed = true;
unsigned long disarmedAt = 0;
bool autoIgnitionPending = false;
unsigned long autoIgnitionFrom = 0;

// LCD
bool lcdReady = false;
char lcdLines[2][17] = {"", ""};
const char* resultText = nullptr;
unsigned long resultShownAt = 0;

void setup() {
  Serial.begin(115200);

  // DC Motor
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  ledcAttach(ENA, 1000, 8);

  // Stepper
  lockMotor.setMaxSpeed(800);
  lockMotor.setAcceleration(400);

  // Restore the last completed lock position without moving (first boot: unlocked)
  lockStore.begin("carlock", false);
  locked = lockStore.getBool("locked", false);
  lockMotor.setCurrentPosition(locked ? LOCKED_POSITION : UNLOCKED_POSITION);

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

  Serial.println("=== Car Security System ===");
  Serial.println("Commands:");
  Serial.println("  on         - start engine");
  Serial.println("  off        - stop engine");
  Serial.println("  speed X    - set engine speed 0-255");
  Serial.println("  lock       - lock car");
  Serial.println("  unlock     - unlock car");
  Serial.println("Walking up to the sensor sends START_AUTH UNLOCK or START_AUTH IGNITION");
  Serial.println(locked ? "Restored state: Locked" : "Restored state: Unlocked");
  updateLcd();
}

void engineOn() {
  digitalWrite(IN1, HIGH);
  digitalWrite(IN2, LOW);
  ledcWrite(ENA, currentSpeed);
  motorRunning = true;
}

void engineOff() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  ledcWrite(ENA, 0);
  motorRunning = false;
}

void releaseStepper() {
  digitalWrite(STEP_IN1, LOW);
  digitalWrite(STEP_IN2, LOW);
  digitalWrite(STEP_IN3, LOW);
  digitalWrite(STEP_IN4, LOW);
}

// Returns true if the lock starts moving
bool lockCar() {
  if (locked) {
    Serial.println("Already locked");
    return false;
  }
  Serial.println("Locking...");
  lockMotor.moveTo(LOCKED_POSITION);
  locked = true;
  stepperMoving = true;
  return true;
}

// Returns true if the lock starts moving
bool unlockCar() {
  if (!locked) {
    Serial.println("Already unlocked");
    return false;
  }
  Serial.println("Unlocking...");
  lockMotor.moveTo(UNLOCKED_POSITION);
  locked = false;
  stepperMoving = true;
  return true;
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
  if (authState == WAITING_AUTH) {
    status = "Scanning...";
  } else if (resultText != nullptr && millis() - resultShownAt < RESULT_SHOW_MS) {
    status = resultText;
  }
  lcdWrite(0, status);

  if (locked) {
    lcdWrite(1, motorRunning ? "LOCKED   IGN:ON" : "LOCKED   IGN:OFF");
  } else {
    lcdWrite(1, motorRunning ? "UNLOCKED IGN:ON" : "UNLOCKED IGN:OFF");
  }
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
  disarmedAt = millis();
}

void rearmSensor() {
  if (!sensorArmed) {
    sensorArmed = true;
    Serial.println("Re-armed");
  }
}

void pollSensor() {
  // pulseIn blocks up to 25 ms, so never read while the stepper moves or a scan runs
  if (authState != IDLE || stepperMoving || motorRunning) {
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
    autoIgnitionPending = false;
    Serial.println("Person gone");
    rearmSensor();
  }

  if (autoIgnitionPending) {
    if (millis() - autoIgnitionFrom < AUTO_IGNITION_MS) {
      return;
    }
    autoIgnitionPending = false;
    if (personPresent && !locked) {
      Serial.println("Still present; starting ignition scan");
      requestAuth();
    }
    return;
  }

  if (!sensorArmed && millis() - disarmedAt >= REARM_MS) {
    rearmSensor();
  }
  if (personPresent && sensorArmed) {
    requestAuth();
  }
}

// ── Authentication flow ──────────────────────────────────────────────────

void requestAuth() {
  if (authState != IDLE) {
    return;
  }
  if (motorRunning) {
    Serial.println("Engine already running; trigger ignored");
    return;
  }
  pendingPurpose = locked ? PURPOSE_UNLOCK : PURPOSE_IGNITION;
  authState = WAITING_AUTH;
  authStartedAt = millis();
  resultText = nullptr;
  Serial.println(pendingPurpose == PURPOSE_UNLOCK ? "START_AUTH UNLOCK" : "START_AUTH IGNITION");
}

void finishAuth(bool authorized, const char* lcdText) {
  authState = IDLE;
  disarmSensor();
  if (!authorized) {
    showResult(lcdText);
    return;
  }
  if (pendingPurpose == PURPOSE_UNLOCK) {
    showResult(lcdText);
    unlockCar();
    autoIgnitionPending = true;
    autoIgnitionFrom = millis();
  } else if (locked) {
    // Car was relocked while the ignition scan ran
    Serial.println("Car is locked; ignition not started");
    showResult("Car locked");
  } else {
    showResult(lcdText);
    engineOn();
    Serial.println("Engine ON");
  }
}

void handleAuthReply(const String& cmd) {
  if (authState != WAITING_AUTH) {
    Serial.print("Ignored ");
    Serial.print(cmd);
    Serial.println(": no authentication pending");
    return;
  }
  if (cmd == "AUTH_PENDING") {
    authStartedAt = millis();
    Serial.println("Pi is scanning...");
  } else if (cmd == "AUTHORIZED") {
    Serial.println("Access granted");
    finishAuth(true, "Authorized");
  } else {
    Serial.print("Access not granted: ");
    Serial.println(cmd);
    const char* text = "Error";
    if (cmd == "UNAUTHORIZED") text = "Unauthorized";
    else if (cmd == "DENIED") text = "Denied";
    else if (cmd == "BUSY") text = "Busy";
    finishAuth(false, text);
  }
}

void checkAuthTimeout() {
  if (authState == WAITING_AUTH && millis() - authStartedAt > AUTH_TIMEOUT_MS) {
    Serial.println("No reply from Pi; authentication timed out");
    finishAuth(false, "Denied");
  }
}

// ── Serial commands ──────────────────────────────────────────────────────

void handleLine(String cmd) {
  cmd.trim();
  cmd.toUpperCase();
  if (cmd.length() == 0) {
    return;
  }

  if (cmd == "START" || cmd == "ON") {
    engineOn();
    Serial.println("Engine ON");

  } else if (cmd == "STOP" || cmd == "OFF") {
    engineOff();
    // Someone still standing there shouldn't immediately get another ignition scan
    disarmSensor();
    Serial.println("Engine OFF");

  } else if (cmd.startsWith("SPEED ")) {
    int val = cmd.substring(6).toInt();
    if (val >= 0 && val <= 255) {
      currentSpeed = val;
      Serial.print("Engine speed set to: ");
      Serial.println(currentSpeed);
      if (motorRunning) ledcWrite(ENA, currentSpeed);
    } else {
      Serial.println("Speed must be 0-255");
    }

  } else if (cmd == "LOCK") {
    if (lockCar()) disarmSensor();

  } else if (cmd == "UNLOCK") {
    if (unlockCar()) disarmSensor();

  } else if (cmd == "AUTH_PENDING" || cmd == "AUTHORIZED" || cmd == "UNAUTHORIZED"
             || cmd == "DENIED" || cmd == "BUSY" || cmd == "ERROR") {
    handleAuthReply(cmd);

  } else {
    Serial.println("Unknown command. Use: START, STOP, SPEED X, LOCK, UNLOCK");
  }
}

void loop() {
  // Keep stepper moving
  if (stepperMoving) {
    lockMotor.run();
    if (lockMotor.distanceToGo() == 0) {
      stepperMoving = false;
      releaseStepper();
      lockStore.putBool("locked", locked);
      Serial.println(locked ? "Locked" : "Unlocked");
    }
  }

  pollSensor();
  checkAuthTimeout();
  updateLcd();

  if (Serial.available()) {
    handleLine(Serial.readStringUntil('\n'));
  }
}
