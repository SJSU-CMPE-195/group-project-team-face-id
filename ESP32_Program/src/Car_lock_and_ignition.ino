// DC Motor A pins (ignition)
const int ENA = 14;
const int IN1 = 26;
const int IN2 = 27;

// DC Motor B pins (lock)
const int ENB = 15;
const int IN3 = 18;
const int IN4 = 19;

const int LOCK_SPEED = 150;    // speed of lock motor 0-255
const int LOCK_TIME  = 400;    // ms to run lock motor

int currentSpeed = 200;
bool motorRunning = false;
bool forward = true;
bool locked = false;

void setup() {
  Serial.begin(115200);

  // Motor A
  pinMode(IN1, OUTPUT);
  pinMode(IN2, OUTPUT);
  ledcAttach(ENA, 1000, 8);

  // Motor B
  pinMode(IN3, OUTPUT);
  pinMode(IN4, OUTPUT);
  ledcAttach(ENB, 1000, 8);

  // Make sure lock motor is stopped
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  ledcWrite(ENB, 0);

  Serial.println("Commands:");
  Serial.println("  on       - turn motor on");
  Serial.println("  off      - turn motor off");
  Serial.println("  flip     - reverse direction");
  Serial.println("  speed X  - set speed 0-255");
  Serial.println("  lock     - lock");
  Serial.println("  unlock   - unlock");
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
  // Stop after set time
  digitalWrite(IN3, LOW);
  digitalWrite(IN4, LOW);
  ledcWrite(ENB, 0);
}

void loop() {
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();

    if (cmd == "on") {
      motorOn();
      Serial.println(forward ? "Motor ON — forward" : "Motor ON — reverse");

    } else if (cmd == "off") {
      motorOff();
      Serial.println("Motor OFF");

    } else if (cmd == "flip") {
      forward = !forward;
      Serial.println(forward ? "Direction: forward" : "Direction: reverse");
      if (motorRunning) motorOn();

    } else if (cmd.startsWith("speed ")) {
      int val = cmd.substring(6).toInt();
      if (val >= 0 && val <= 255) {
        currentSpeed = val;
        Serial.print("Speed set to: ");
        Serial.println(currentSpeed);
        if (motorRunning) ledcWrite(ENA, currentSpeed);
      } else {
        Serial.println("Speed must be 0-255");
      }

    } else if (cmd == "lock") {
      lockMotor(true);
      locked = true;
      Serial.println("Locked");

    } else if (cmd == "unlock") {
      lockMotor(false);
      locked = false;
      Serial.println("Unlocked");

    } else {
      Serial.println("Unknown command. Use: on, off, flip, speed X, lock, unlock");
    }
  }
}