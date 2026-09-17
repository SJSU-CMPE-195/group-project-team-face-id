# BASS Native Android and Fixed QR Pairing Plan

Historical implementation plan. For the current host-camera unlock rule, see
[Backend-camera face unlock](backend-camera-unlock-plan.md) and
[Android wireless operation](docs/android-wireless.md).

Approved implementation scope: native Kotlin/Compose Android app, local network
discovery, fixed device QR pairing, and all existing BASS operations. The PC
delivery performs real face recognition with simulated actuators; Pi/ESP32
acceptance is a separate hardware step. Daily operation needs neither USB nor
Internet. Phone and host must share a LAN that permits peer discovery.

## Step 1: Record the baseline and establish the Android application

Files: `android-app/`, existing web build/lint entry points.

Preserve the React application. Record web compiler/lint outputs before edits.
Create a pinned Gradle/Kotlin/Compose project, camera/QR support, encrypted
pairing storage, and native Console, Users, Logs, and Settings screens with
English and Traditional Chinese resources. Record the first Android compilation
and compare subsequent compile/lint results.

## Step 2: Add portable wireless provisioning

Files: `bass_wireless.py`, `wireless/`, `requirements-wireless.txt`.

Persist one device UUID and random pairing key outside Git. Export a fixed QR
containing protocol version, device ID, and pairing key, with no IP or port.
Publish `_bass._tcp` over DNS-SD with public identity metadata only. Require the
pairing key for all functional wireless APIs and verify the device ID before
using a discovered endpoint. Keep QR viewing local to the host.

## Step 3: Reuse the authoritative API and face workflow

Files: `car_face_auth/src/pc_runtime.py`, narrowly affected
`car_face_auth/src/pi_runtime.py`, Android networking/session modules.

Run CPU InsightFace against actual phone JPEGs with canonical SQLite persistence.
Simulate only lock/ignition transport on PC; Pi keeps the production hardware
runtime. Support enrollment, face unlocking and same-driver ignition, manual
controls, users/access, settings, logs, cancellation, and status refresh through
one API. Do not introduce a second face database or database schema migration.

Guard late responses by device/session generation. Stop camera capture when
backgrounded or disconnected, best-effort cancel remote sessions, and never
automatically retry actuator commands. Rediscover an existing pairing after host
address changes without asking the user to enter an address.

## Step 4: Package host startup and delivery

Files: Windows launch/firewall scripts, Pi wireless systemd installer,
`docs/android-wireless.md`.

Provide one-click Windows startup with dependency, model, database, port, and
discovery checks, plus a fixed printable QR. Restrict firewall rules to Private
networks. Provide Pi deployment instructions preserving device identity and the
existing database; prevent competing processes from owning the camera/ESP32.

## Verification and acceptance

Run web build/lint, Android compilation/lint, and changed Python compilation.
Do not add or run automated tests or browser/phone UI automation. Deliver the APK,
QR, startup commands, and a manual Pixel 7 checklist covering all functionality,
USB unplugged, restarts/address changes, wrong QR/key, permissions, disconnects,
and multiple devices. Distinguish compile success, observed service health, real
phone acceptance, and Pi/ESP32 hardware acceptance.

## Agreed boundaries

- Fixed QR possession grants access; no user accounts or host approval step.
- Trusted-LAN HTTP is the agreed v1 transport; no public tunnel or TLS claim.
- No auto APK installation, Wi-Fi provisioning, Bluetooth, or cross-LAN control.
- Newly required Android camera/UI/QR and Python mDNS/QR dependencies are within
  the approved plan. Existing web dependencies and toolchain are preserved.
- No commits, PRs, or branch changes are authorized by this implementation task.
