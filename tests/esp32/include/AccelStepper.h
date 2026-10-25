#pragma once

class AccelStepper {
 public:
  static constexpr int HALF4WIRE = 8;

  AccelStepper(int, int, int, int, int) {}

  void setMaxSpeed(float) {}
  void setAcceleration(float) {}
  void setCurrentPosition(long position) {
    currentPosition_ = position;
    targetPosition_ = position;
  }
  void moveTo(long position) { targetPosition_ = position; }
  void run() { currentPosition_ = targetPosition_; }
  long distanceToGo() const { return targetPosition_ - currentPosition_; }

 private:
  long currentPosition_ = 0;
  long targetPosition_ = 0;
};
