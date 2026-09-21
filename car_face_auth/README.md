# Car face auth (Python PoC)

Facial-recognition runtime and historical development utilities live in this
folder. The deployed entrypoint is the root `bass_wireless.py`; it constructs
the injected Device API and owns camera recognition. The real Pi runtime blocks
all ESP32 lock and ignition output until command acknowledgement and physical
position feedback are implemented. PC actuator behavior remains simulated.

**Full documentation** (repo overview, frontend UI, installation, and running the PoC) is in the [root README](../README.md).

## Proof of concept scope

The proof of concept demonstrates:

- Real-time face detection  
- Face embedding generation  
- Identity verification using a local database  
- Confidence-based access decisions with a rolling window  

**Still prototype-only:**

- Multi-user robustness testing  
- Anti-spoofing (photo/video protection)  
- Physical Android/Pi TLS verification and full vehicle acceptance testing

No connected Pi/ESP32/lock was exercised for this batch. The recognition flow
does not implement presentation-attack detection, and this document does not
claim production or vehicle safety.

The canonical host now verifies per-user six-digit PINs, enforces administrator
roles, issues revocable phone credentials, and requires short-lived grants for
sensitive operations. PINs are never stored by the dashboard or Android app.
The host stores only salted verifiers derived with a device-specific
`device.auth.key`; that key, the database, `device.json`, and `device.tls.pem`
must be backed up and restored as one set. See
[Android wireless operation](../docs/android-wireless.md#host-authorization-and-recovery)
for offline migration and administrator recovery commands.

The runtime reports presentation-attack detection as unavailable. Because the
persisted liveness policy defaults to enabled, scans fail closed before camera
or actuator work. An administrator can explicitly disable the policy for
prototype testing only; doing so does not add presentation-attack protection or
support a production-safety claim. Pi startup, shutdown, scans, manual controls,
and timers cannot send motor or ignition commands. The runtime reports actuator
control unavailable and physical state unconfirmed; there is no environment
variable bypass. Enrollment and administrator data tasks may still operate.

## Prerequisites

- Python 3.9+ (3.11 recommended)
- Raspberry Pi (or PC for development)  
- Camera (Pi Camera Module or USB webcam)  
- Virtual environment (recommended)  

### Required libraries

- opencv-python  
- numpy  
- insightface  
- onnxruntime (or onnxruntime-gpu)  

For wireless HTTPS deployment, use the root host and Pi instructions. The
simulator below is only for isolated development.

## Hardware-free Device API simulator

From the repository root, run `npm run mock:pi`, then use an HTTP client or
development harness against `http://localhost:5055`. The simulator reuses the
deployed `PiRuntime` scan, enrollment, authorization, and actuator state machine
while replacing the Pi camera, InsightFace model, and ESP32 serial connection
with deterministic seams. The normal dashboard and Android app do not route to
this fixture.
See the root README's **Developer-only hardware simulator** section for
scenario and failure-injection examples. Simulator results are development evidence,
not physical hardware acceptance. Its unauthenticated HTTP port 5055 is a
separate developer fixture and is not protected by the wireless host's HTTPS
transport. It binds only to loopback and rejects remote peers.

For real PC webcam recognition, run `scripts/start-wireless.cmd` from the root
and follow [Android wireless operation](../docs/android-wireless.md). Android
uses the certificate-pinned HTTPS API on port 5056; the browser dashboard stays
on loopback HTTP port 5057. Unlock always uses the active backend's camera; a
client image or PIN cannot grant unlock by itself.

The selected Android release candidate is BASS 0.4.0 (versionCode 5), currently
an unsigned APK with SHA-256
`A40452059BECE9F7EFB4B9C4D2491CA96A87D2602624965B44B304AD53F883B9`.
It requires release signing before installation or distribution; see the linked
wireless guide for the signing inputs and artifact path.

## Retired standalone entrypoints

`src/api_server.py` fails closed when imported; the old FastAPI/Uvicorn listener
cannot be started. `src/enroll.py` and `src/verify_live.py` are nonzero-exit
retirement stubs. They do not open the camera or serial port and must not be
used for enrollment, verification, actuation, or face-data migration.

Use the authenticated host UI for enrollment and verification. The retained
`src/test_insightface.py` script is a historical experiment only; it is not a
deployment or migration tool.

The active runtime accepts only the strict `BASSF001` numeric template format
and does not deserialize legacy pickle templates. Follow the root
[offline face-template migration](../docs/android-wireless.md#face-template-migration)
for a verified legacy database; the migration creates a separate validated copy
and has not been run on a real Pi database in this worktree.
