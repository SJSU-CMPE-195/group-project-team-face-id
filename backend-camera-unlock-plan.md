# Backend-camera face unlock

## Problem and intended behavior

The user requires face recognition for every unlock. The camera belongs to the
selected backend: a PC uses its webcam and a Raspberry Pi uses its Pi camera.
Every UI is a remote control for that same backend. The phone camera remains
available for enrollment, but it cannot grant unlock or ignition.

Android retains its existing PIN gates. A correct unlock PIN starts a backend
face scan; it does not unlock the device. A failed scan or unavailable camera
leaves the lock closed. Same-person ignition verification remains required.
The paired backend identity determines the camera, not physical proximity.

## Ownership and steps

1. `car_face_auth/src/pc_runtime.py`, `car_face_auth/src/pi_runtime.py`,
   `pi_device_api.py`, `wireless/api.py` (backend executor): reuse the existing
   scan/session/recognition pipeline with PC webcam capture. Make `/api/unlock`
   start the canonical host-camera scan and prevent direct manual or uploaded
   verification-frame unlocks. Report backend-camera capability and source.
2. `android-app/` (Android executor): replace manual unlock with PIN followed
   by the backend-camera scan. Use backend camera capability and neutral labels,
   preserve enrollment and cancellation, and build an in-place APK update.
3. `src/` (web executor): use one backend-camera verification path, remove
   browser-camera/manual unlock choices, and default local development to the
   existing authenticated local host connection. Preserve saved simulation
   data, but remove simulation mode from normal controls.
4. `README.md`, `docs/android-wireless.md`, `docs/android-delivery-status.md`
   (root): document the single host-camera rule, current API behavior, launch
   and installation steps, and the boundary between PC and physical Pi output.
5. Runtime and APK delivery (root): review integrated changes, preserve the
   existing identity/database, restart the identified local host with new code,
   verify readiness, and install the APK over the USB-connected phone without
   clearing its data. The user checks the visible camera and unlock flow.

## Contracts and compatibility

- `/api/scan/start` is the canonical asynchronous face-verification operation.
  `/api/unlock` starts that same flow and returns a scan session.
- The service accepts host-camera source names, including the legacy
  `pi_camera` wire value, but never a phone/client source for verification.
- `/api/scan/sample` cannot grant verification from uploaded frames.
- Device capabilities include `device_camera` and `camera_source`
  (`pc_webcam` or `pi_camera`); legacy `pi_camera` remains Pi-specific.
- PC lock/ignition output remains simulated. Pi uses its physical output.
- No database schema migration, dependency installation, commit, or deployment
  to a Pi is included. Pairing identity, PIN, and enrolled templates persist.

## Verification

Before editing, each executor records the applicable compiler baseline:
project-local Vite build, Gradle APK assembly, or Python byte compilation.
After editing, compare final compiler output and run applicable JS/Android lint.
Compiler logs and build artifacts used for comparison stay in task scratch.

Root reviews all real UNLOCK grant paths and the combined diff, then checks
service readiness/capabilities and package installation. No automated tests or
browser/phone UI automation are authorized. Camera recognition through the
user's UI, Pi camera/ESP32 actuation, and physical lock/ignition acceptance are
reported separately from compilation, API readiness, and APK installation.
