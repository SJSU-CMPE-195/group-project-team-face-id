# Android wireless operation

BASS Android connects to a PC or Raspberry Pi on the same local network. Install
the APK once, open the app, and scan the fixed device QR. The app remembers the
pairing and discovers the current address on later launches. Normal operation
requires neither USB nor Internet access.

The host must stay powered on. Both devices must use a network that permits
peer traffic and multicast discovery. Guest Wi-Fi, client isolation, and some
VPNs can block discovery. The app does not install itself or configure Wi-Fi.

## Windows quick start

1. Connect the PC and phone to the same trusted Wi-Fi.
2. Build the dashboard once with `./node_modules/.bin/vite.cmd build` from the
   repository root after installing the existing frontend dependencies. Rebuild
   after frontend changes. Run `scripts/start-wireless.cmd`. The launcher prepares its Python environment
   when needed, checks dependencies, and starts the host. Initial dependency and
   model downloads need Internet; subsequent launches reuse the downloaded files.
3. Follow the launcher instructions for Private-network firewall rules if needed.
4. Keep the host running and open `http://localhost:5056` on the PC. In
   **Settings → Device pairing**, select **Show QR**.
5. Install the APK, open BASS, grant camera access, and set a six-digit BASS PIN
   before scanning the QR. Enter it twice to confirm. No IP or port entry is
   required.

On Windows, the default identity is `%USERPROFILE%/.bass/device.json`; QR
exports are `bass-pairing.png` and `bass-pairing.html` in that directory.
`BASS_DEVICE_CONFIG` overrides the identity path. Keep this file outside Git.
To install the firewall rules manually, run
`./scripts/setup-wireless-firewall.ps1 -Port 5056` in an administrator
PowerShell. Rules allow only Private profiles and local-subnet peers.

Every unlock uses the camera attached to the connected backend. On Windows,
the backend captures its webcam and runs CPU InsightFace recognition; on Pi,
it captures the Pi camera. The UI device does not select or upload unlock
images. Phone images remain supported for enrollment.

PC lock and ignition outputs are simulated and identified as such. A real face
match is still required before the simulated lock opens. PC success does not
establish that a physical lock or engine responded. Android uses the single
wireless API (port 5056 by default); Vite and the standalone Face API on port
8765 are not required.

## One dashboard and phone connection

Keep the wireless host running and open `http://localhost:5056` on that PC or Pi.
The host serves the built dashboard and connects it to its own backend
automatically. A custom host port also works: use the dashboard URL printed at
startup. There is no separate Connect button or backend address to configure.
Previously saved browser backend addresses no longer override this connection.
Saved simulation data remains preserved and is not used for normal controls.

Scan **Settings → Device pairing → Show QR** with the phone. This dashboard and
the paired phone now share the host's users, settings, camera, and controls.
Open **Users**, then select **Refresh** to load a new phone enrollment.
Switching to a different Pi requires scanning that Pi's QR; proximity does not
change the pairing. The fixed QR already carries the configuration Android
needs: device identity and pairing key. Discovery supplies the current address
and port, so a Wi-Fi address change does not require printing another QR.

The `/local/wireless/api/*` bridge accepts only trusted requests from a browser
on the host. It supplies the existing credential inside the server. Functional
API calls do not place the key in browser storage. The dashboard and its QR are
unavailable through the host's LAN address; Android uses the authenticated
wireless API directly. The standalone Face API is not needed.

For frontend development, run `npm run dev` and open `http://localhost:5173` on
the same computer. Vite forwards both dashboard calls and QR requests to the
local wireless backend on port 5056. It uses the identity already loaded by the
backend, including Pi service configuration overrides. Production operation
uses the Python service and built `dist` files; it does not need Vite.

## Watch a phone-triggered scan on the host

Open the dashboard on the backend computer at `http://localhost:5056` (or
`http://localhost:5173` during development). Leave its **Control** tab open.
Starting face unlock or ignition verification on the phone displays the host's
camera image in the original camera viewport and progress in the existing scan
status area. Backend-camera enrollment uses the same viewport while Control is
open.

The backend opens its camera once and captures continuously. Recognition reads
new frames from that capture while the dashboard displays a continuous MJPEG
stream. Display updates do not wait for recognition to finish. The browser does
not open the webcam separately. Actual smoothness depends on the camera, host
load, and connection; the view is a positioning aid and is not recorded.
The image clears when capture ends or the connection fails. The latest session
result remains visible. Viewing or leaving
the preview does not start or cancel the phone's scan. Phone-camera enrollment
images are not included in this host-camera preview.

## Display the QR in web Settings

On the PC or Pi itself, keep the wireless host running and open the existing
BASS web UI through `localhost` or `127.0.0.1`. In **Settings → Device pairing**,
select **Show QR**. The card displays that host's name and fixed
QR. Scan it with the Android app. Select **Hide QR** when finished; leaving
Settings also clears the displayed QR.

The card loads `/local/pairing-qr` from the same origin as its controls. The
built dashboard uses the serving backend's actual port; the development server
forwards to its local backend. Both use that backend's loaded identity. A
standalone `vite preview` only serves files and is not the operational dashboard.

This QR is available only to a browser on the host. Opening the web UI through
a LAN address does not grant access. The read endpoint
`GET /local/pairing-qr` requires a loopback peer, loopback Host, and trusted local
browser request, and returns non-cacheable metadata and the PNG as a data URL.
It is separate from the authenticated functional `/api/*` routes. No new
pairing identity or database schema is created by displaying the QR.

## Build the APK

Install JDK 17, Android SDK Platform 36, and Build Tools 35.0.0. Gradle Wrapper,
Android Gradle Plugin, Kotlin, and library versions are pinned in `android-app/`.
There is no need to change the system Java default.

From the repository root in PowerShell:

```powershell
./scripts/build-android.ps1 -JavaHome 'C:/path/to/jdk-17' -AndroidSdk "$env:LOCALAPPDATA/Android/Sdk"
```

The script runs APK assembly and lint, not automated tests. Output:
`android-app/app/build/outputs/apk/debug/app-debug.apk`.

This development APK uses the local Android debug signing key. Keep that key
for in-place updates. Google Play publication and production signing-key
management are outside this delivery. In Android Studio, open `android-app/`,
select JDK 17 for Gradle, and set the SDK path locally. Do not commit signing
keys, `local.properties`, or build outputs.

## BASS PIN and camera controls

The Android app uses a separate six-digit BASS PIN, entered on its own numeric
keypad. It is not the phone's screen-lock PIN. First pairing sets and confirms
the PIN; later pairing, every phone-camera enrollment, and starting an unlock
scan require a fresh verification. A correct unlock PIN only starts the scan;
the backend must recognize an allowed enrolled face before unlocking.
Cancelling or leaving the app discards the pending action. Re-enrollment
requires verification too, before any user or
enrollment request is sent.

After upgrading an already paired installation without a PIN, set the PIN
before using the app's controls. Existing pairing and face data are preserved.
**Forget device** opens a confirmation dialog with **Also reset BASS PIN**
unchecked. Leave it unchecked to keep the current PIN, failed-attempt count,
and cooldown. Select it to remove the local PIN along with the saved pairing.
This option does not require the old PIN. The app forgets the pairing before
resetting the PIN; it cannot keep that connection while resetting its PIN.
The next pairing requires creating and confirming a new six-digit PIN and
scanning the device QR again. Cancel dismisses the dialog without making changes.
Backend users, face templates, and the fixed pairing QR remain unchanged.

PIN verification data is encrypted with Android Keystore and excluded from
backups. Five incorrect attempts impose a persisted 60-second cooldown. This
protects the supported app's local actions; the host still authorizes clients
using the QR pairing key. It is not a device-wide administrator password.

Phone camera is available for enrollment only. Face unlock and same-person
ignition verification always use the connected backend's camera: PC webcam or
Pi camera. Every unlock button starts this flow. An unavailable camera, failed
match, or cancelled scan cannot fall back to a PIN-only/manual unlock. Lock,
stop, and reset retain their existing behavior. Android must not show successful
physical actuation in PC mode.

The paired host determines the camera. To use a Pi instead of the PC, connect
to that Pi's identity. Being near another host does not silently switch the
camera or database. All clients connected to the same backend operate its
camera and read its state.

## Fixed pairing and discovery

The QR contains JSON with `version`, `device_id`, and `pairing_key`; it contains
no address or port. Identity is persisted separately from the face database.
Restarting/updating the host does not regenerate it.

DNS-SD service `_bass._tcp` advertises the device ID and protocol version, never
the pairing key. Android resolves the current address and verifies authenticated
device information before loading the functional API. An IP change does not
require a new label.

Possession of the QR grants device access, including control operations. Keep
labels and exports with the intended users. The host never serves the QR/key
through its LAN API. Android encrypts the saved pairing with Android Keystore
and excludes it from backups.

V1 uses HTTP on a trusted LAN. Pairing authenticates requests but does not
encrypt traffic. Do not expose it through a public tunnel or router forwarding.

## API and recovery

All wireless `/api/*` requests require `Authorization: Bearer <pairing_key>`.
`/health` is minimal public liveness information. Existing standalone web
development entry points retain their own behavior.

Authenticated `GET /api/device-info` returns `device_id`, `protocol_version`,
`name`, and `capabilities` (`client_camera`, `device_camera`, `camera_source`,
`pi_camera`, `simulated_actuators`). `camera_source` is `pc_webcam` or `pi_camera`;
the legacy `pi_camera` flag remains true only on Pi. `device_camera` describes
the supported capture path, not a guarantee that the camera is plugged in or
free. The app checks identity/protocol before reading state.

Existing Device API routes remain authoritative for users, permissions,
templates, settings, logs, sessions, and actuation. Phone enrollment frames are
JPEG multipart uploads to enrollment sample endpoints. Android never grants
face authorization itself.

`POST /api/scan/start` starts backend-camera unlock or same-person ignition
verification. `POST /api/unlock` starts the same unlock scan and returns a scan
session; it no longer grants access directly. Poll `/api/scan/status` for the
result. Client/phone verification sources and `/api/scan/sample` uploads are
rejected. Legacy `pi_camera` request values remain compatible with the backend
camera route. Only the backend's successful face-match flow can grant unlock.

`GET /api/camera/status` returns the camera source, latest host-camera session
public status (including its `kind`), `frame_id`, and `frame_available`.
`GET /api/camera/stream?session_id=...` returns a continuous
`multipart/x-mixed-replace; boundary=frame` JPEG stream for the active session.
An inactive or mismatched session returns 204; a missing identifier returns 400.
The stream ends with the session or when capture fails or becomes stale.
Disconnecting a viewer does not stop capture or cancel the session.
`GET /api/camera/frame?session_id=...` returns the matching active session's
cached JPEG (200), no available frame (204), or a missing-session-id error
(400). These read-only routes inherit wireless authentication and use
`Cache-Control: no-store`; they never open the camera. Frames exist only in
memory and are cleared when their session ends. A stale frame is unavailable.
The dashboard polls status metadata and keeps one stream connection per viewed
session. Its service worker excludes these API requests from offline caching.

Capture stops on leaving a workflow or backgrounding; the app attempts to
cancel that exact session. It does not replay interrupted control commands.
Reconnection refreshes state before enabling controls. Abandoned client
enrollment sessions also expire on the server.

Failed enrollment or verification keeps the backend's error message in the
phone's error dialog after capture ends. Camera open/read/stall errors include
recovery guidance: another app such as Zoom may be using the camera; close apps
using it, check its connection and permissions, then retry. The host cannot
identify the owning app. A failed enrollment leaves the person available for
retry; without a saved face template, their card shows **No face template**.

## Manual Pixel 7 acceptance

The user performs these checks with USB unplugged. Compilation and service
startup do not establish phone acceptance.

- Scan once, reconnect after app/host restart, and reconnect after the host IP
  changes. With multiple hosts, connect only to the scanned identity.
- Set/confirm the BASS PIN, cancel verification, enter incorrect PINs, and
  restart the app during cooldown. Verify that pairing, phone enrollment, and
  unlock scans cannot start without the PIN, including after an update.
- From both the connected and offline screens, open Forget device and cancel;
  pairing and PIN must remain unchanged. Reopen it and confirm the reset option
  starts unchecked. Forget without resetting and verify the old PIN and any
  cooldown remain. Forget with reset selected, then create and confirm a new
  PIN and scan the same device QR. Backend users and face templates must remain.
- Enroll a real face, verify the saved template, cancel/retry enrollment, and
  try frames containing no face or multiple faces.
- Make the host camera unavailable, then start paired-device-camera enrollment.
  Confirm an error dialog remains visible after capture ends and explains how
  to retry. A new person's card must show **No face template**. Release the
  camera and retry from that card; dismissing the error must not start a session.
- On PC, start Unlock from the phone and verify that the PC webcam captures the
  face. On Pi, perform the same action and verify that the Pi camera captures.
  Keep the host dashboard open and confirm it automatically shows the actual
  continuous camera view and progress in Control's existing camera area, even
  while recognition is processing a frame. Change dashboard
  tabs during the phone scan and confirm the scan continues; return to Control
  to view it again. The image must clear when the scan ends.
  Correct PIN alone must not unlock; unknown faces and camera failures must
  leave the lock closed. Complete same-person ignition, then verify stop, lock,
  and reset. Inspect status and logs after each operation. Phone-camera unlock
  must not be offered.
- Add/remove users, change access, and verify recognition enforces permissions.
  A directory entry without a template must not count as an enrolled face.
- Save and refresh all settings. Liveness/failure-lockout retain existing
  backend limits; a setting switch does not add an enforcement algorithm.
- Refuse permissions, scan an invalid QR, use a wrong key, interrupt Wi-Fi
  mid-session, and reconnect. Commands must not repeat on reconnection.
- Check discovery failure on an isolated/guest network produces an actionable
  error instead of connecting to a different device.

## Pi handoff

Use the wireless Pi installer under `scripts/` and its systemd unit. Only one
process may own the camera/ESP32. Stop the PC host before transferring identity;
do not advertise the same ID from two machines.

Build the dashboard from the same source revision on the development machine:

```powershell
./node_modules/.bin/vite.cmd build
```

On a POSIX development machine, use `./node_modules/.bin/vite build`. Copy the
complete `dist/` directory into the Pi checkout alongside `bass_wireless.py`.
`dist/` is ignored by Git, so cloning or pulling the source does not deliver the
dashboard. Rebuild and copy it again after frontend changes. No pairing key is
included in these static build files. The Pi installer checks for the dashboard
before changing packages, services, or stored data; it does not install Node.

On Raspberry Pi OS, from that checkout owned by the intended non-root service
account, run `bash scripts/install-wireless-pi.sh`. It uses sudo where needed.
The defaults are `~/faceid/device.json`, `~/faceid/faceid.db`, and
`~/faceid/pairing/` for the exported label. To reuse the PC identity, copy its
`device.json` securely to the Pi, restrict it with `chmod 600`, and run:

```bash
BASS_DEVICE_CONFIG_SOURCE=/path/to/transferred/device.json \
  bash scripts/install-wireless-pi.sh
```

Set `FACEID_DB_PATH` to the existing database before installation when its
location differs. The installer refuses to overwrite a different identity or
start alongside enabled/active legacy camera services. Review its message
before explicitly disabling those services. Inspect the installed service with
`systemctl status faceid-wireless.service` and
`journalctl -u faceid-wireless.service -n 50 --no-pager`.

On the Pi itself, open `http://localhost:5056` using its browser (substitute
`BASS_PORT` if changed). Controls and the Settings QR use that running service
automatically. Scan the Pi's QR from Android on the same network, then either
UI can operate the Pi camera. Phone enrollment appears in the Pi dashboard
after Refresh. There is no second frontend service or Connect action.

Transfer device configuration to preserve the label. Transfer SQLite separately
if users/templates must move too. Back up both before replacing destination
files. Code deployment does not automatically transfer configuration or data.

No business database schema migration is added. Startup uses existing
`db.init_db()` against `FACEID_DB_PATH`; users/templates are not reset. Configure
the Pi database path before starting its service.

Pi Camera, ESP32 communication, and physical lock/ignition behavior require
separate hardware acceptance; PC simulated controls cannot satisfy it.
