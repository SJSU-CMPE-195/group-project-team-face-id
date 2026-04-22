// ==============================
// Face ID Car Demo - ESP32 Motor Controller
// Python <-> ESP32 serial protocol
// ==============================

// DC Motor A pins (ignition)
const int ENA = 14;
const int IN1 = 26;
const int IN2 = 27;

// DC Motor B pins (lock)
const int ENB = 15;
const int IN3 = 18;
const int IN4 = 19;

const int LOCK_SPEED = 255;   // 0-255
const int LOCK_TIME  = 1200;  // ms to run lock motor

int currentSpeed = 200;
bool motorRunning = false;
bool forward = true;
bool locked = false;

// ------------------------------
// Function declarations
// ------------------------------
void motorOn();
void motorOff();
void lockMotor(bool lockDirection);
String getStatus();
void processCommand(String cmd);

// ------------------------------
// Setup
// ------------------------------
void setup() {
  Serial.begin(115200);

  // Motor A (ignition)
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  ledcAttach(ENA, 1000, 8);

  // Motor B (lock)
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  ledcAttach(ENB, 1000, 8);

  // Make sure both motors are stopped at boot
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  ledcWrite(ENA, 0);

  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  ledcWrite(ENB, 0);

  Serial.println("READY");
}

void motorOn() {
  if (forward) {
    digitalWrite(IN1, HIGH);
    digitalWrite(IN2, LOW);
  } else {
    digitalWrite(IN1, LOW);
    digitalWrite(IN2, HIGH);
  }

  ledcWrite(ENA, currentSpeed);
  motorRunning = true;
}

void motorOff() {
  digitalWrite(IN1, LOW);
  digitalWrite(IN2, LOW);
  ledcWrite(ENA, 0);
  motorRunning = false;
}

void lockMotor(bool lockDirection) {
  if (lockDirection) {
    digitalWrite(IN3, HIGH);
    digitalWrite(IN4, LOW);
  } else {
    digitalWrite(IN3, LOW);
    digitalWrite(IN4, HIGH);
  }

  ledcWrite(ENB, LOCK_SPEED);
  delay(LOCK_TIME);

  // Stop lock motor
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  ledcWrite(ENB, 0);
}

// ------------------------------
// Status string for Python
// ------------------------------
String getStatus() {
  String status = "STATUS:";
  status += "MOTOR=";
  status += (motorRunning ? "ON" : "OFF");
  status += ",DIR=";
  status += (forward ? "FORWARD" : "REVERSE");
  status += ",SPEED=";
  status += String(currentSpeed);
  status += ",LOCK=";
  status += (locked ? "LOCKED" : "UNLOCKED");
  return status;
}

// ------------------------------
// Command processor
// ------------------------------
void processCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "STATUS") {
    Serial.println(getStatus());
  }

  else if (cmd == "START" || cmd == "ON") {
    motorOn();
    Serial.println("OK:START");
  }

  else if (cmd == "STOP" || cmd == "OFF") {
    motorOff();
    Serial.println("OK:STOP");
  }

  else if (cmd == "FLIP") {
    forward = !forward;

    // If motor is already running, re-apply direction immediately
    if (motorRunning) {
      motorOn();
    }

    Serial.print("OK:DIR=");
    Serial.println(forward ? "FORWARD" : "REVERSE");
  }

  else if (cmd.startsWith("SPEED ")) {
    String valuePart = cmd.substring(6);
    int val = valuePart.toInt();

    // toInt() returns 0 for invalid text too, so handle carefully
    if ((valuePart == "0") || (val > 0 && val <= 255)) {
      currentSpeed = val;

      if (motorRunning) {
        ledcWrite(ENA, currentSpeed);
      }

      Serial.print("OK:SPEED=");
      Serial.println(currentSpeed);
    } else {
      Serial.println("ERROR:BAD_SPEED");
    }
  }

  else if (cmd == "LOCK") {
    Serial.println("OK:LOCK");
    lockMotor(true);
    locked = true;
  }

  else if (cmd == "UNLOCK") {
    Serial.println("OK:UNLOCK");
    lockMotor(false);
    locked = false;
  }

  else {
    Serial.println("ERROR:UNKNOWN_COMMAND");
  }
}

// Main loop
void loop() {
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    processCommand(cmd);
  }
}
