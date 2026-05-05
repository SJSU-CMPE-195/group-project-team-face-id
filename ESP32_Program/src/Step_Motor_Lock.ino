#include <AccelStepper.h>

// =============================================
// ESP32 Motor Control — Car Security System
// DC Motor A (OUT1/OUT2): Engine ignition
// Stepper Motor (ULN2003):  Lock/Unlock
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

// 28BYJ-48 step count for lock/unlock position
const int LOCK_STEPS = 2048;

AccelStepper lockMotor(AccelStepper::HALF4WIRE, STEP_IN1, STEP_IN3, STEP_IN2, STEP_IN4);

int currentSpeed = 200;
bool motorRunning = false;
bool locked = false;
bool stepperMoving = false;

void setup() {
  Serial.begin(115200);

  // DC Motor
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  ledcAttach(ENA, 1000, 8);

  // Stepper
  lockMotor.setMaxSpeed(800);
  lockMotor.setAcceleration(400);

  Serial.println("=== Car Security System ===");
  Serial.println("Commands:");
  Serial.println("  on         - start engine");
  Serial.println("  off        - stop engine");
  Serial.println("  speed X    - set engine speed 0-255");
  Serial.println("  lock       - lock car");
  Serial.println("  unlock     - unlock car");
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

  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd == "on") {
      engineOn();
      Serial.println("Engine ON");

    } else if (cmd == "off") {
      engineOff();
      Serial.println("Engine OFF");

    } else if (cmd.startsWith("speed ")) {
      int val = cmd.substring(6).toInt();
      if (val >= 0 && val <= 255) {
        currentSpeed = val;
        Serial.print("Engine speed set to: ");
        Serial.println(currentSpeed);
        if (motorRunning) ledcWrite(ENA, currentSpeed);
      } else {
        Serial.println("Speed must be 0-255");
      }

    } else if (cmd == "lock") {
      Serial.println("Locking...");
      lockMotor.move(LOCK_STEPS);
      locked = true;
      stepperMoving = true;

    } else if (cmd == "unlock") {
      Serial.println("Unlocking...");
      lockMotor.move(-LOCK_STEPS);
      locked = false;
      stepperMoving = true;

    } else {
      Serial.println("Unknown command. Use: on, off, speed X, lock, unlock");
    }
  }
}