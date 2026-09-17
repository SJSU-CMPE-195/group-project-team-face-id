# Android and host dashboard delivery status

Snapshot: 2026-09-15, local `main` at
`1813e4a816390a8f35faa0b424691fb92a6e8ddf`, with uncommitted changes. No commit,
PR, merge, remote CI check, or Pi deployment was performed. See
[operation and deployment](android-wireless.md),
[unified connection](../unified-host-connection-plan.md),
[camera preview](../backend-camera-preview-plan.md), and
[optional PIN reset](../android-forget-pin-plan.md).

## Camera failure prompt

Android retains terminal enrollment/verification failures in its existing error
dialog after capture ends. Successful and cancelled sessions keep their existing
behavior. Camera open/read/stall diagnostics preserve the underlying error and
suggest closing other camera apps such as Zoom, checking the camera connection
and permissions, and retrying. Another app is a possible cause; the host does
not identify which app owns the camera. See the
[implementation plan](../camera-failure-prompt-plan.md) and
[manual acceptance](android-wireless.md#manual-pixel-7-acceptance).

Python compilation and Android `:app:compileDebugKotlin` passed before and
after the change. Final `:app:assembleDebug :app:lintDebug` passed with zero
lint errors and ten warnings. English/Traditional Chinese resource keys match.
Evidence is under `.cache/camera-enrollment-failure/`: `python-baseline.log`,
`python-pi-baseline.log`, `python-final.log`, `android-baseline.log`,
`android-final.log`, pre-edit source copies, and `runtime.json`. No automated
tests or browser/phone automation were run. No dependency or schema changes
are required.

Built APK: `android-app/app/build/outputs/apk/debug/app-debug.apk`, version
`0.3.1`, code `4`. SHA-256:
`C5790D17819F48CF1B43E575641E206E1E7FEDE0019CE3DE938E848E5D66D326`.

At 14:21 PDT, a read-only authenticated status request confirmed PC mode on
port 5056, PID 51580, with no active camera session. This process was not
restarted and still needs restarting to load the new camera diagnostics.
`adb devices -l` returned no connected devices. The new APK must be installed
before the phone can display terminal failure dialogs. Camera-busy behavior and
retry on the phone remain unverified.

## Web copy cleanup (2026-09-15 13:12 PDT)

The web dashboard now keeps operation labels, state, scan details, errors, and
necessary input guidance. The footer, version/decorative badges, repeated
instructions, technical database descriptions, duplicate enrollment names,
user-added timestamps, and displayed pairing UUID were removed. Pairing retains
the host name, QR, access reminder, and Show/Hide/Retry controls. Android, API,
verification, timers, cancellation, and data behavior are unchanged.

Direct Vite baseline/final builds and targeted ESLint passed. Independent
static review compared the eight changed components with pre-edit snapshots
and found only copy/display changes and cleanup of unused display bindings.
Evidence is in `%TEMP%/face-ui-copy-cleanup/`: `baseline-build.log`,
`final-build.log`, `baseline-eslint.log`, `final-eslint.log`, `source-before/`,
and `served-build.json`.

The current served assets are `index-gUF0BSf5.js` and `index-BoyqNqg7.css`.
Read-only requests to port 5056 returned HTTP 200 for HTML, JavaScript, and CSS,
with all bytes matching the final local build. Backend PID 51580 remains running;
no backend restart or APK installation was needed. Visual acceptance remains
manual. No automated tests or browser operations were performed.

## Implemented behavior

The PC/Pi wireless service serves the built dashboard at its own local URL,
`http://localhost:5056` by default. Controls use `/local/wireless/api/*`, and
Settings loads `/local/pairing-qr` from that same origin. The manual Connect
button and backend address selector are removed; saved manual addresses remain
stored but no longer determine the connection. Device user deletion uses the
host's authoritative users endpoint and does not contact the separate Face API.

The local bridge verifies the actual loopback peer, local Host and serving port,
exact Origin/Referer when supplied, and browser request metadata. It injects the
running host's credential internally, strips CORS, and uses `no-store`.
Direct `/api/*` and `/ready` still require Bearer authentication. Local QR access
uses the same origin boundary with no CORS. Static files are confined to `dist`.
Vite forwards the same paths after its own local guard, without independently
reading a potentially different device configuration. All `/local/*` requests
bypass the service-worker cache. No new dependency or database migration is added.

The fixed QR still contains only version, device ID, and pairing key. Android
uses discovery for the current LAN address/port. Existing labels and phone
pairings remain valid. Switching to another host requires that host's pairing;
proximity does not change the database or camera.

Every unlock requires recognition by the connected backend camera. Android's
six-digit PIN only starts the face scan. Same-person ignition requires another
scan. Phone frames remain an enrollment option. PC mode uses its webcam and
simulated actuators; Pi mode uses Picamera2 and ESP32. One capture producer feeds
recognition and continuous MJPEG preview in Control's original camera area.
Preview requests cannot start or cancel scans. Only the latest frame is held
in memory; a stopped/stale session stops exposing its image.

Android Forget device opens one confirmation dialog from connected/offline
screens. The optional PIN reset defaults off. Forgetting alone preserves PIN,
failed attempts, and cooldown; selecting reset clears PIN after deleting local
pairing. Cancel writes nothing. The next pairing after reset creates a new PIN.
Backend templates and the fixed device QR are not changed by Forget device.

## Earlier unified-host activation (2026-09-15 00:01 PDT)

The user ran `.cache/restart-backend-unified.ps1` and confirmed completion.
At 00:01 PDT, the new backend listener is PID 51580 on port 5056; Vite remains
PID 60104 on 5173. Startup logs identify PC mode, the existing database, and the
new `http://localhost:5056/` dashboard. The helper verified the old project
processes and backed up SQLite before restart. Earlier automatic approval review
rejected agent process termination, so the user performed the restart.

Read-only HTTP observations in `activation-http.json` confirm the dashboard,
JavaScript, and CSS return HTTP 200 with bytes matching the final `dist` build.
Both 5056 and the 5173 development proxy return device metadata and pairing QR
with the configured device ID and identical QR image hashes. Their local API/QR
responses use `no-store` and no CORS headers. Cross-origin QR/bridge requests
and LAN access to the dashboard/QR return 403.

The configured identity is `C:/Users/Vampy/.bass/device.json`, with SHA-256
`548B0DD74A1B5427987844F044E686CF4212A863BB116118A015ADE7EF1F2E8B` at the snapshot.
The project database is `C:/Users/Vampy/face-ui/.cache/mock_faceid.db`.
Restart evidence is under `%TEMP%/face-ui-unified-host/`:
`before-restart.json`, `after-restart.json`, `faceid-before-unified.db`, and
`wireless.stdout.log`. The identity file hash and complete raw users-table
digest match before/after restart (11 stored rows). The enrolled-face API count
is zero in both snapshots. This update did not remove stored users or templates.

Authenticated readiness and camera metadata return 200, with no active camera
session. Unauthenticated stream access returns 401; an inactive stream returns
204 directly and through Vite, and a missing session ID returns 400. These
read-only observations did not open the camera or establish browser rendering,
an active video stream, or phone acceptance.

## Earlier unified-host verification

Evidence for that activation is in `%TEMP%/face-ui-unified-host/`:

- `backend-baseline.log`, `backend-final.log`: Python compilation passes before
  and after; final `py_compile` passes. Git Bash `bash -n` passes. The initial
  Windows `bash` resolution failed; the installed Git Bash completed the check.
- `web-baseline.log`, `web-final.log`, `integrated-build.log`: direct Vite builds
  passed for the original unified-host activation. Its files were
  `index-Cyf_6GAF.js` and `index-D9UHm7d_.css`, including the user-deletion fix;
  the current web assets are recorded in Web copy cleanup above.
- `proxy-{baseline,final}.log`, `sw-{baseline,final}.log`: JavaScript syntax
  checks pass. `root-eslint.log` and user-actions ESLint baseline/final pass;
  the web executor's targeted ESLint also passes.
- `startup-{baseline,final}.log`, `restart-helper-parse.log`: PowerShell parsing
  passes. Git whitespace checks pass; existing newline conversion warnings remain.
- Independent static review passes for authentication/origin/path boundaries,
  same-host QR routing, no credential in build artifacts, MJPEG iterable and
  disconnect preservation, cache bypass, and installer ordering. Review findings
  for cross-origin QR and legacy Face API deletion were corrected before completion.

No automated tests, browser/phone automation, or agent-triggered camera scans
were run. Aggregate `npm run build` was avoided because it runs PWA tests.
The read-only activation observations above establish endpoint availability and
data preservation; they do not establish visual or physical acceptance.

## Previously installed Android package

The earlier `app-debug.apk` was version `0.3.1`, code `4`.
SHA-256: `C6DFD6A8699239D1CD4B0463C7DE19AE58F92B896ED2E90507D2219F748399CF`.
At 22:39 PDT on 2026-09-14, `adb install -r` installed this package on the USB-connected Pixel 7
(serial `34251FDH20091R`). Package inspection confirmed its version. Encrypted
pairing/PIN preference hashes matched before and after installation.

Its baseline assembly and final `assembleDebug lintDebug` passed, with zero lint
errors and ten existing warnings. English/Traditional Chinese resources match
at 129 strings and two plurals. Evidence is in `%TEMP%/face-ui-forget-pin/`.
Actual Forget/PIN reset was not executed by the agent. That connection
unification update required no APK replacement; the camera failure prompt does.

## Pi delivery and remaining acceptance

Pi installation now requires the complete built `dist/` directory beside
`bass_wireless.py`, and checks its index before modifying packages, services, or
data. `dist` is Git-ignored; copy the build from the same source revision when
deploying. Python serves it, so no Node runtime or second frontend service is
needed on Pi. Missing build files leave an actionable local 503 while direct
backend startup remains available.

Manually open the active host dashboard, show its QR, and confirm
phone/dashboard users and controls refer to the same host. Verify a phone scan
appears in Control's camera area and ends cleanly. Confirm PIN alone does not
unlock, rejection/cancellation do not grant access, and ignition verifies the
same driver. Image smoothness and timing require visual acceptance.

No Pi hardware is connected. Pi installer/systemd execution, camera, ESP32,
physical lock, and ignition remain unverified. A stalled native camera driver
can retain capture ownership until it returns or the host restarts. Startup
uses existing `db.init_db()`; no schema migration is required. Identity and
SQLite transfer to Pi remain separate, unperformed deployment steps.
