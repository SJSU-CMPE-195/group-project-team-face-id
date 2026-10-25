#include "Arduino.h"

#include <cstring>
#include <functional>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

unsigned long mockMillis = 0;
FakeSerial Serial;

void requestAuth();
void updateLcd();

#include "../../ESP32_Program/src/Step_Motor_Lock.ino"

namespace {

void expect(bool condition, const std::string& message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void expectOutput(const std::string& text) {
  expect(
      Serial.output.find(text) != std::string::npos,
      "missing serial output: " + text);
}

std::size_t outputCount(const std::string& text) {
  std::size_t count = 0;
  std::size_t position = 0;
  while ((position = Serial.output.find(text, position)) != std::string::npos) {
    count++;
    position += text.length();
  }
  return count;
}

void resetFirmware(bool startsLocked = false) {
  mockMillis = 1000;
  Serial.clear();
  currentSpeed = 200;
  motorRunning = false;
  locked = startsLocked;
  stepperMoving = false;
  lockMotor.setCurrentPosition(
      startsLocked ? LOCKED_POSITION : UNLOCKED_POSITION);
  authState = IDLE;
  pendingPurpose = PURPOSE_UNLOCK;
  authStartedAt = 0;
  authBootNonce = 0xA1B2C3D4;
  authRequestCounter = 0;
  pendingRequestId[0] = '\0';
  personPresent = false;
  nearCount = 0;
  farCount = 0;
  lastSensorRead = 0;
  sensorArmed = true;
  disarmedAt = 0;
  autoIgnitionPending = false;
  autoIgnitionFrom = 0;
  lcdReady = false;
  lcdLines[0][0] = '\0';
  lcdLines[1][0] = '\0';
  resultText = nullptr;
  resultShownAt = 0;
}

std::string startAuth() {
  requestAuth();
  expect(authState == WAITING_AUTH, "requestAuth did not enter WAITING_AUTH");
  expect(std::strlen(pendingRequestId) == 16, "request ID is not 16 chars");
  const std::string requestId = pendingRequestId;
  const std::string purpose = locked ? "UNLOCK " : "IGNITION ";
  expectOutput("START_AUTH " + purpose + requestId + "\n");
  return requestId;
}

void sendReply(const std::string& command, const std::string& requestId) {
  handleLine(String(command + " " + requestId));
}

void testMatchingAuthorizedReplyIsAcknowledged() {
  resetFirmware(true);
  const std::string requestId = startAuth();

  sendReply("AUTHORIZED", requestId);

  expect(!locked, "matching authorization did not unlock");
  expect(stepperMoving, "unlock did not start the stepper");
  expect(authState == IDLE, "accepted request remained active");
  expect(pendingRequestId[0] == '\0', "accepted request ID was retained");
  expect(autoIgnitionPending, "unlock did not schedule ignition scan");
  expectOutput("AUTH_ACK " + requestId + "\n");
}

void testExpiredReplyIsRejectedBeforeDispatch() {
  resetFirmware(true);
  const std::string requestId = startAuth();
  mockMillis += 31000;

  sendReply("AUTHORIZED", requestId);

  expect(locked, "expired authorization changed the lock state");
  expect(!stepperMoving, "expired authorization moved the stepper");
  expect(authState == IDLE, "expired request remained active");
  expect(!autoIgnitionPending, "expired request scheduled ignition");
  expectOutput("AUTH_CANCEL " + requestId + "\n");
  expectOutput("Ignored expired reply: AUTHORIZED\n");
  expect(
      outputCount("AUTH_ACK " + requestId + "\n") == 0,
      "expired authorization was acknowledged");
}

void testTimeoutCheckCancelsAtDeadline() {
  resetFirmware();
  const std::string requestId = startAuth();
  mockMillis += AUTH_TIMEOUT_MS;

  checkAuthTimeout();

  expect(authState == IDLE, "timed-out request remained active");
  expect(!sensorArmed, "timeout did not disarm the presence sensor");
  expectOutput("AUTH_CANCEL " + requestId + "\n");
  expectOutput("authentication timed out");
}

void testWrongRequestIdCannotAuthorize() {
  resetFirmware(true);
  const std::string requestId = startAuth();
  const std::string wrongRequestId = "A1B2C3D400000099";

  sendReply("AUTHORIZED", wrongRequestId);

  expect(locked, "wrong request ID changed the lock state");
  expect(!stepperMoving, "wrong request ID moved the stepper");
  expect(authState == WAITING_AUTH, "wrong request ID consumed the request");
  expect(std::string(pendingRequestId) == requestId, "request ID changed");
  expectOutput("request ID does not match");
}

void testOnlyMatchingPendingReplyExtendsDeadline() {
  resetFirmware();
  const std::string requestId = startAuth();
  const unsigned long originalStart = authStartedAt;
  mockMillis += 1000;

  sendReply("AUTH_PENDING", "A1B2C3D400000099");
  expect(authStartedAt == originalStart, "wrong pending reply reset timeout");

  sendReply("AUTH_PENDING", requestId);
  expect(authStartedAt == mockMillis, "matching pending reply did not reset timeout");

  mockMillis += AUTH_TIMEOUT_MS - 1;
  checkAuthTimeout();
  expect(authState == WAITING_AUTH, "request expired before refreshed deadline");
  mockMillis++;
  checkAuthTimeout();
  expect(authState == IDLE, "request survived refreshed deadline");
}

void expectManualCancel(
    const char* command,
    bool startsLocked,
    bool expectedLocked) {
  resetFirmware(startsLocked);
  const std::string requestId = startAuth();
  autoIgnitionPending = true;

  handleLine(String(command));

  expect(authState == IDLE, std::string(command) + " left auth pending");
  expect(
      pendingRequestId[0] == '\0',
      std::string(command) + " retained request ID");
  expect(
      !autoIgnitionPending,
      std::string(command) + " retained auto ignition");
  expect(locked == expectedLocked, std::string(command) + " lock result is wrong");
  expectOutput("AUTH_CANCEL " + requestId + "\n");
}

void testManualCommandsCancelPendingWork() {
  expectManualCancel("STOP", false, false);
  expect(!motorRunning, "STOP left the engine running");
  expectManualCancel("LOCK", false, true);
  expectManualCancel("UNLOCK", true, false);
}

void testAcceptedGrantCannotBeAppliedTwice() {
  resetFirmware(true);
  const std::string requestId = startAuth();
  sendReply("AUTHORIZED", requestId);
  const std::size_t ackCount = outputCount("AUTH_ACK " + requestId + "\n");

  sendReply("AUTHORIZED", requestId);

  expect(!locked, "duplicate grant relocked the car");
  expect(
      outputCount("AUTH_ACK " + requestId + "\n") == ackCount,
      "duplicate grant was acknowledged twice");
  expectOutput("Ignored AUTHORIZED: no authentication pending\n");
}

}  // namespace

int main() {
  const std::vector<std::pair<std::string, std::function<void()>>> tests = {
      {"matching authorized reply", testMatchingAuthorizedReplyIsAcknowledged},
      {"expired reply", testExpiredReplyIsRejectedBeforeDispatch},
      {"timeout check", testTimeoutCheckCancelsAtDeadline},
      {"wrong request ID", testWrongRequestIdCannotAuthorize},
      {"matching AUTH_PENDING", testOnlyMatchingPendingReplyExtendsDeadline},
      {"manual cancellation", testManualCommandsCancelPendingWork},
      {"duplicate grant", testAcceptedGrantCannotBeAppliedTwice},
  };

  for (const auto& test : tests) {
    try {
      test.second();
      std::cout << "PASS: " << test.first << '\n';
    } catch (const std::exception& error) {
      std::cerr << "FAIL: " << test.first << ": " << error.what() << '\n';
      return 1;
    }
  }

  std::cout << "All " << tests.size()
            << " firmware state-machine tests passed\n";
  return 0;
}
