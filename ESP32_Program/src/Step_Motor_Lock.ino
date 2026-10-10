#include <AccelStepper.h>

// =============================================
// ESP32 Motor Control — Car Security System
// DC Motor A (OUT1/OUT2): Engine ignition
// Stepper Motor (ULN2003):  Lock/Unlock
// Pushbutton:               Face-auth trigger
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

// Trigger button (to GND, internal pull-up) and external status LED (220 ohm to GND)
const int BUTTON_PIN = 32;
const int STATUS_LED = 33;

// 28BYJ-48 step count for lock/unlock position
const int LOCK_STEPS = 2048;

// Absolute stepper positions, so repeated LOCK/UNLOCK never over-rotate
const long UNLOCKED_POSITION = 0;
const long LOCKED_POSITION = LOCK_STEPS;

const unsigned long DEBOUNCE_MS = 50;
// Pi scans time out after 20 s; give it margin before giving up
const unsigned long AUTH_TIMEOUT_MS = 30000;
const unsigned long FAIL_BLINK_MS = 1500;

AccelStepper lockMotor(AccelStepper::HALF4WIRE, STEP_IN1, STEP_IN3, STEP_IN2, STEP_IN4);

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
unsigned long failBlinkUntil = 0;

// Button debounce
int lastButtonReading = HIGH;
int buttonState = HIGH;
unsigned long lastButtonChange = 0;

void setup() {
  Serial.begin(115200);

  // DC Motor
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  ledcAttach(ENA, 1000, 8);

  // Stepper
  lockMotor.setMaxSpeed(800);
  lockMotor.setAcceleration(400);

  // Trigger button and status LED
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(STATUS_LED, OUTPUT);
  digitalWrite(STATUS_LED, LOW);

  Serial.println("=== Car Security System ===");
  Serial.println("Commands:");
  Serial.println("  on         - start engine");
  Serial.println("  off        - stop engine");
  Serial.println("  speed X    - set engine speed 0-255");
  Serial.println("  lock       - lock car");
  Serial.println("  unlock     - unlock car");
  Serial.println("Button press sends START_AUTH UNLOCK or START_AUTH IGNITION");
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

void lockCar() {
  if (locked) {
    Serial.println("Already locked");
    return;
  }
  Serial.println("Locking...");
  lockMotor.moveTo(LOCKED_POSITION);
  locked = true;
  stepperMoving = true;
}

void unlockCar() {
  if (!locked) {
    Serial.println("Already unlocked");
    return;
  }
  Serial.println("Unlocking...");
  lockMotor.moveTo(UNLOCKED_POSITION);
  locked = false;
  stepperMoving = true;
}

// ── Authentication flow ──────────────────────────────────────────────────

void requestAuth() {
  if (authState != IDLE) {
    return;
  }
  if (motorRunning) {
    Serial.println("Engine already running; button ignored");
    return;
  }
  pendingPurpose = locked ? PURPOSE_UNLOCK : PURPOSE_IGNITION;
  authState = WAITING_AUTH;
  authStartedAt = millis();
  failBlinkUntil = 0;
  Serial.println(pendingPurpose == PURPOSE_UNLOCK ? "START_AUTH UNLOCK" : "START_AUTH IGNITION");
}

void finishAuth(bool authorized) {
  authState = IDLE;
  if (!authorized) {
    failBlinkUntil = millis() + FAIL_BLINK_MS;
    return;
  }
  if (pendingPurpose == PURPOSE_UNLOCK) {
    unlockCar();
  } else if (locked) {
    // Car was relocked while the ignition scan ran
    Serial.println("Car is locked; ignition not started");
  } else {
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
    finishAuth(true);
  } else {
    Serial.print("Access not granted: ");
    Serial.println(cmd);
    finishAuth(false);
  }
}

void pollButton() {
  int reading = digitalRead(BUTTON_PIN);
  if (reading != lastButtonReading) {
    lastButtonChange = millis();
    lastButtonReading = reading;
  }
  if (millis() - lastButtonChange > DEBOUNCE_MS && reading != buttonState) {
    buttonState = reading;
    if (buttonState == LOW) {
      requestAuth();
    }
  }
}

void checkAuthTimeout() {
  if (authState == WAITING_AUTH && millis() - authStartedAt > AUTH_TIMEOUT_MS) {
    Serial.println("No reply from Pi; authentication timed out");
    finishAuth(false);
  }
}

void updateStatusLed() {
  if (authState == WAITING_AUTH) {
    digitalWrite(STATUS_LED, HIGH);
  } else if (millis() < failBlinkUntil) {
    digitalWrite(STATUS_LED, (millis() / 100) % 2 ? HIGH : LOW);
  } else {
    digitalWrite(STATUS_LED, LOW);
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
    lockCar();

  } else if (cmd == "UNLOCK") {
    unlockCar();

  } else if (cmd == "AUTH_PENDING" || cmd == "AUTHORIZED" || cmd == "DENIED"
             || cmd == "BUSY" || cmd == "ERROR") {
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
      Serial.println(locked ? "Locked" : "Unlocked");
    }
  }

  pollButton();
  checkAuthTimeout();
  updateStatusLed();

  if (Serial.available()) {
    handleLine(Serial.readStringUntil('\n'));
  }
}
