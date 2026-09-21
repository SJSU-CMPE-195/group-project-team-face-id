# Android wireless operation

BASS Android connects to a PC or Raspberry Pi on the same local network. Install
the APK once, open the app, and scan an activation or phone invitation QR. The app remembers the
pairing and discovers the current address on later launches, then asks only for
the paired account's PIN. Normal operation
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
4. Keep the host running and open `http://localhost:5057` on the PC. Existing
   accounts can sign in. New ownership is established from Android using an
   activation card and a device pairing window; local administrator bootstrap
   is retired. For the current hardware-free PC, use the
   [Hardware Simulator tab](#hardware-simulator-and-developer-reset).
5. In **Users**, create the intended user with a six-digit PIN, then select
   **Pair phone** to show that user's five-minute invitation QR.
6. Install the APK, open BASS, grant camera access, and scan the invitation QR.
   Enter that user's host PIN. No token, IP or port entry is required.

On Windows, the default identity files are `%USERPROFILE%/.bass/device.json`,
`device.tls.pem`, and `device.auth.key`; QR exports are `bass-pairing.png` and
`bass-pairing.html` in that directory. `BASS_DEVICE_CONFIG` overrides the JSON
path, and the two companion files stay beside it. Keep them outside Git. Back
up these three files and the SQLite database as one coherent set because the
database's PIN verifiers depend on `device.auth.key`.

The launcher uses an existing OpenSSL executable to provision the certificate.
It detects the copy bundled with Git for Windows when `openssl` is not on
`PATH`; set `BASS_OPENSSL` only when a different existing executable is needed.
No additional package is required for TLS.
To install the firewall rules manually, run
`./scripts/setup-wireless-firewall.ps1 -Port 5056` in an administrator
PowerShell. Rules allow only Private profiles and local-subnet peers.

Every unlock uses the camera attached to the connected backend. On Windows,
the backend captures its webcam and runs CPU InsightFace recognition; on Pi,
it captures the Pi camera. The UI device does not select or upload unlock
images. Phone images remain supported for enrollment.

PC lock and ignition outputs are simulated and identified as such. A real face
match is still required before the simulated lock opens. PC success does not
establish that a physical lock or engine responded. The real Pi blocks every
motor and ignition output path until the protocol provides command
acknowledgement and physical-position feedback. Android uses the single wireless
HTTPS API (port 5056 by default); Vite and the standalone Face API on port 8765
are not required. The old standalone Face API is retired and cannot be started.

## One dashboard and phone connection

Keep the wireless host running and open `http://localhost:5057` on that PC or Pi.
This loopback-only listener serves the built dashboard and connects it to its
own backend automatically. Android uses the separate LAN HTTPS listener on port
5056. There is no separate Connect button or backend address to configure.
Previously saved browser backend addresses no longer override this connection.
Saved simulation data remains preserved and is not used for normal controls.

In **Users**, a signed-in administrator selects **Pair phone** for the intended
account and confirms their administrator PIN. Scan the displayed invitation QR
in BASS Android and enter the invited account's PIN. This single scan supplies
the device identity, TLS pin and invitation; the PIN stays separate. The host
returns a distinct revocable credential for that phone. The dashboard and phone
then share the host's users, settings, camera, and controls within that user's
permissions. Open **Users**, then select **Refresh** to load a new phone
enrollment.
Switching to a different Pi requires scanning that Pi's QR; proximity does not
change the pairing. The version 3 public QR carries the device identity and
TLS certificate fingerprint, with no authorization secret. Discovery supplies the current address and
port, so a Wi-Fi address change does not require printing another QR.

The `/local/wireless/api/*` bridge accepts only trusted requests from a browser
on the host. The dashboard uses a 15-minute HTTP-only, SameSite=Strict session
cookie plus a CSRF token held in memory. After the first successful name-and-PIN
sign-in, it remembers the account ID and display name, scoped to the host UUID
and ownership generation. Returning after session expiry requires only that
account's PIN. Explicit **Sign out** or **Use another account** clears the saved
selection; a reset or host change invalidates it. An administrator creating
another user does not switch the administrator's remembered account. Browser
storage contains no credential or PIN, and the saved ID grants no access.
Unsafe operations require a fresh PIN-authorized grant bound to
the signed-in user, action, and target. The dashboard and its QR are unavailable
through the host's LAN address; Android uses its own authenticated HTTPS
credential. The standalone Face API is not needed.

For frontend development, run `npm run dev` and open `http://localhost:5173` on
the same computer. Vite forwards both dashboard calls and QR requests to the
loopback dashboard on port 5057. It uses the identity already loaded by the
backend, including Pi service configuration overrides. Production operation
uses the Python service and built `dist` files; it does not need Vite.

For a custom loopback port, set the same `BASS_DASHBOARD_PORT` in the host and
Vite shells. On Windows, `scripts/start-wireless.ps1 -DashboardPort <port>` sets
the host port; the parameter also defaults from `BASS_DASHBOARD_PORT`.

## Hardware simulator and developer reset

The PC developer panel uses the existing host database, identity and LAN
connection. Start `scripts/start-wireless.cmd -HardwareSimulator`, open the normal
dashboard at `http://localhost:5057`, and select **Hardware** in its sidebar.
The controls stay in that dashboard before product login and after product
reset. Other product tabs still require sign-in.

This explicit PC development mode trusts direct localhost clients. The backend
silently establishes and renews a separate HttpOnly developer cookie and CSRF
token when the UI reads hardware status. There is no separate developer login
or special browser entry. Writes still require the cookie, exact Origin and
CSRF token. The LAN listener, cross-origin requests, ordinary PC startup and Pi
mode cannot use these controls. Enable this mode only on a trusted development
PC.
The built dashboard at `http://localhost:5057` is the control surface; Vite does
not proxy these privileged developer routes.

Press and hold the multifunction button, then release. Three seconds opens a
two-minute pairing window; ten seconds opens a two-minute recovery window.
Only one action fires on release. The backend times the press and expires it
when keepalives stop. Losing focus cancels the incomplete press. Power off
cancels work and windows while retaining committed ownership.

Fresh devices require the activation card, an open pairing window, and a name
and six-digit PIN chosen in Android. The app saves the pending request and
credentials before submitting, so an interrupted reply can resume the same
attempt. Export recovery material outside the phone. Recovery requires that
separate material and the recovery window, rotates recovery credentials, and
revokes previous device sessions. Ownership transfer requires the current owner,
a fresh PIN, and pairing window; the successor must accept its short-lived
transfer credential and create their own PIN. Invited administrators cannot
alter the owner's PIN, face, account access or devices.

Upgrades classify existing populated databases as `legacy`; they do not select
an owner automatically or delete records. To test first use, explicitly choose
**Developer reset**, read the listed scope, and enter `RESET`. It stops product
operations, drains writers, and backs up SQLite plus `device.json`,
`device.tls.pem`, and `device.auth.key` before clearing product accounts, faces,
PINs, logs, pairings, invitations, grants, ownership and recovery state. Safe
settings are restored; device UUID and TLS identity are preserved. A new
activation credential replaces the old one.

Backups and manifests are private under the identity directory's
`backups/developer-reset/`. The database reset receipt and the wipe commit
together. Retrying the same request returns its existing receipt, even after
another owner has claimed the device. A backup or reload failure keeps the
product in maintenance. Retry the same pending reset through the panel; ordinary
startup refuses an incomplete reset. Never delete the reset journal to bypass
maintenance. Restoring a backup is an offline operation with the host stopped
and the complete matching database/identity/key set restored together.

Developer reset is not a product recovery button. Ordinary PC startup and Pi
mode have no developer routes. Even in developer mode, LAN requests cannot use
them. No real hotspot, automatic Wi-Fi provisioning, GPIO, trusted boot, rollback
protection, PAD or physical actuator acceptance is supplied by this panel.

## Watch a phone-triggered scan on the host

Open the dashboard on the backend computer at `http://localhost:5057` (or
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
BASS web UI through `localhost` or `127.0.0.1` and sign in as an administrator.
In **Settings → Device pairing**, select **Show QR**. The card displays that
host's name and fixed public QR. For normal phone pairing, use **Users → Pair
phone** and scan its invitation QR instead. If the phone already scanned the
fixed public QR, choose **Scan invitation QR** on the next screen, then enter
the user's PIN. Select **Hide QR** when finished; leaving Settings also clears
the displayed QR.

The Users invitation contains the same fixed identity plus a one-use invitation
token. The dashboard removes the image at expiry and shows a countdown. Only the
host can accept or reject the invitation; the visual countdown grants no access.
Choose **Pair phone** again if it expires. Creating a replacement invalidates the
earlier invitation for that user. Closing the image hides it without revoking it.

An administrator using Android can save a self-contained invitation file. The
recipient opens it from the initial pairing screen's saved-file action. Older
token-only invitation files need to be reissued. A rejected PIN can be corrected
without rescanning a still-valid invitation; an expired invitation needs a new QR.

The card loads `/local/pairing-qr` from the same origin as its controls. The
built dashboard uses the serving backend's actual port; the development server
forwards to its local backend. Both use that backend's loaded identity. A
standalone `vite preview` only serves files and is not the operational dashboard.

This QR is available only to a browser on the host. Opening the web UI through
a LAN address does not grant access. The read endpoint
`GET /local/pairing-qr` requires a loopback peer, loopback Host, and trusted local
browser request, an authenticated local administrator session, and returns
non-cacheable metadata plus the PNG as a data URL. It is separate from the
authenticated functional `/api/*` routes. Displaying the QR does not create a
phone credential; only successful invite-and-PIN onboarding does that.

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

The selected release candidate is BASS 0.4.0 (`versionCode` 5). With every
release-signing variable unset, this command produces the unsigned candidate:

```powershell
cd android-app
./gradlew.bat assembleRelease
```

Artifact:
`app/build/outputs/apk/release/app-release-unsigned.apk`

The current SHA-256 and dated build results are in the
[delivery snapshot](android-delivery-status.md).

Set all four variables to build a signed release: `BASS_ANDROID_KEYSTORE`,
`BASS_ANDROID_STORE_PASSWORD`, `BASS_ANDROID_KEY_ALIAS`, and
`BASS_ANDROID_KEY_PASSWORD`. A partial signing configuration is rejected. The
unsigned candidate must be signed before installation or distribution. Local
builds use the installed JBR 21; CI is configured for JDK 17. A successful build
does not establish Android-to-host or physical-hardware acceptance.

## BASS PIN and camera controls

The six-digit BASS PIN is the user's host account PIN, not the phone's screen
lock PIN. Operation-grant PIN input is cleared after submission. An unfinished
pairing request temporarily retains its PIN in encrypted storage so an
interrupted exchange can retry the exact request; completion removes that
pending request. Android Keystore encrypts the saved per-phone credential and
account metadata; completed pairing records contain no PIN. The host
stores a salted verifier derived with its private `device.auth.key`.

Pairing requires the intended host's TLS-pinned identity, a five-minute
administrator-issued invitation and the target user's PIN. The invitation QR
carries the identity and invitation together; only the PIN is typed. A successful exchange returns a unique phone token
that the administrator can revoke without replacing the QR or affecting other
phones. A QR or an earlier shared pairing key alone cannot call ordinary API
operations.

Every sensitive action asks for the current user's PIN and obtains a single-use,
30-second grant bound to the action and target. A correct unlock PIN only starts
the signed-in user's face scan; the backend must still recognize that enrolled,
allowed user. Cancelling, changing tabs, backgrounding, signing out, or changing
identity discards the pending action. Five incorrect attempts impose a
server-side 60-second cooldown. Administrator actions are hidden from ordinary
users and remain enforced by the host if a client calls the API directly.

Returning to the paired Android app asks for that account's PIN before loading
the dashboard. No username is needed. The host checks the PIN against the
account bound to that phone's credential at `POST /api/session-login`; it does
not issue an operation grant. Incorrect PINs share the existing account cooldown.
Temporary connection failures and cancelled PIN entry preserve the pairing for
retry. A successful first pairing already verifies PIN and enters directly.
Completed login does not retain the PIN or bypass sensitive-action verification.

Installations saved under the earlier shared-key format must install the new APK,
scan a new invitation QR, and complete PIN verification. **Sign out** clears that
phone's saved pairing and account selection; using it again requires pairing.
It does not revoke the host's credential record or remove backend users, face
templates, PIN verifiers, other phones, or the fixed QR. An administrator can
separately revoke a phone from the device list.

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

Public QR protocol 3 contains JSON fields `version`, `purpose: "device"`,
`device_id`, and `tls_certificate_sha256`; it contains no address, port, or
authorization secret. Invitation QR uses the same version, device ID and TLS pin,
with `purpose: "pairing_invite"` and `invite_token` (64 lowercase hex digits).
It contains no PIN, activation, recovery or transfer credential. Android checks
both the device ID and certificate pin when an invitation is scanned after a
public device QR. Activation, recovery and transfer cards each have a
distinct purpose and only their corresponding secret. The TLS value is the lowercase hexadecimal SHA-256
fingerprint of the leaf certificate's DER bytes. Android verifies this pin
during the TLS handshake before sending an activation/recovery proof, invite, PIN, or
application data.

The on-disk `device.json` storage format remains version 1. It preserves the
existing device ID and legacy key field, while the host emits QR protocol 3 and
API protocol 3. The companion `device.tls.pem` stores the private key and
certificate. Restarting or updating the host does not regenerate either file.

DNS-SD service `_bass._tcp` advertises the device ID and API protocol version 3,
never the onboarding key or phone token. It also advertises `transport=https`.
Android uses discovery only to find an address; it does not trust discovery as
proof of host identity. An IP change does not require a new label.

Possession of the QR does not grant control. Successful onboarding also needs an
unexpired user-specific invite and that user's PIN. Keep activation, recovery
and transfer exports private. The fixed public QR display is local-only.
Invitation images are returned only to authenticated administrators after the
existing PIN-authorized operation grant, with `Cache-Control: no-store`.
Android encrypts its distinct phone token with Android
Keystore and excludes it from backups.

The host creates a persistent self-signed RSA-3072 certificate valid for 3650
days. A corrupt or expired TLS identity stops startup instead of silently
replacing the certificate. The database is bound to the configured device ID
and TLS certificate fingerprint; a mismatch stops startup before hardware or a
listener opens. Restore the database and all three identity files from one
coherent backup, or follow the explicit offline rotation procedure under
[Host authorization and recovery](#host-authorization-and-recovery). There is
no HTTP downgrade or trust-all fallback.

Phones paired with the earlier protocol 1 or shared-key format must install the
new APK, scan a protocol 3 invitation QR, and enter their account PIN.
Old shared keys are rejected for ordinary API calls. The new QR remains fixed
while `device.json` and `device.tls.pem` are retained. Copying only
`device.json` to another host creates a different certificate. A database bound
to the original set then fails closed; restore the matching set or perform the
explicit authorization rotation. Replacing a certificate alone is not a
completed re-pairing procedure.

TLS protects onboarding, PIN exchange, phone credentials, operation grants, and
application data in transit. Authorization remains authoritative at the host;
client metadata does not assign identity, administrator status, or permission.

## API and recovery

LAN wireless `/api/*` requests use HTTPS. Ordinary authenticated operations
require the phone's revocable bearer token. `POST /api/pairings` exchanges a
valid invite and user PIN for that credential; a shared fixed-QR key grants no
authorization. Sensitive API
writes also require `X-BASS-Operation-Grant`; the grant is single-use, expires
after 30 seconds, and is bound to the authenticated user, action, and target.
The host requires TLS 1.2 or newer; `/health` is minimal public liveness
information. The local dashboard uses HTTP only on `127.0.0.1:5057`, with an
HTTP-only 15-minute session and CSRF protection.

The old standalone Pi and Face API listeners are retired. `pi_device_api.py`
only supplies an injected Flask route factory to `bass_wireless.py`; importing
`car_face_auth.src.api_server` fails closed. The developer mock remains a
separate unauthenticated HTTP fixture on port 5055, but binds only to loopback
and rejects remote peers.

The canonical host is served by bounded Cheroot 11.1.2 workers with pyOpenSSL
26.4.0. Its shared HTTP dependencies also pin Flask 3.1.3, Werkzeug 3.1.8, and
Pillow 12.3.0. The host rejects requests larger than 9 MiB and JSON bodies
larger than 64 KiB. Client enrollment accepts JPEG only, up to 8 MiB, 4096
pixels on either axis, and 4,194,304 total pixels; oversized input returns 413
before enrollment or a data-changing runtime operation starts.

Authenticated `GET /api/device-info` returns `device_id`, API
`protocol_version` 3,
`transport` (`https`), `name`, and `capabilities` (`client_camera`, `device_camera`, `camera_source`,
`pi_camera`, `simulated_actuators`, `actuator_control_available`,
`physical_state_confirmed`, `actuator_feedback`, and `liveness_available`).
`camera_source` is `pc_webcam` or `pi_camera`;
the legacy `pi_camera` flag remains true only on Pi. `device_camera` describes
the supported capture path, not a guarantee that the camera is plugged in or
free. `GET /api/status` reports the same actuator and liveness fields under
`runtime`. The Pi reports control unavailable, physical state unconfirmed, and
feedback unavailable. PC reports available simulated controls and simulated
feedback. `liveness_available` is currently false. The app checks identity and
API protocol before reading state.

Existing Device API routes remain authoritative for users, permissions,
templates, settings, logs, sessions, and actuation. Phone enrollment frames are
JPEG multipart uploads to enrollment sample endpoints. Android never grants
face authorization itself.

`POST /api/verify-log` is retired and returns 410 without writing an event.
Clients cannot submit an authoritative verification result.

`POST /api/scan/start` starts backend-camera unlock or same-person ignition
verification after a matching operation grant. `POST /api/unlock` is retired
and returns 410. Poll `/api/scan/status` for the result. Client/phone
verification sources and `/api/scan/sample` uploads are rejected. Legacy
`pi_camera` request values remain compatible with the backend camera route.
Only the backend's successful face-match flow can grant unlock.

Presentation-attack detection is not implemented. The persisted liveness policy
defaults to enabled, so the runtime fails closed before opening the camera or
attempting actuation while `liveness_available` is false. An administrator can
explicitly disable the policy for prototype testing. That choice reduces
security and must not be presented as liveness, production, physical-lock, or
vehicle-safety acceptance.

The Pi does not open the serial actuator path or send commands from startup,
shutdown, manual controls, scans, or timers. The dashboard and Android disable
those controls, label physical state as unconfirmed, and do not turn stored
database state into a physical success claim. There is no environment-variable
bypass. Enrollment and administrator data tasks remain available, subject to
the liveness policy. PC controls remain available and are labelled simulated.

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
startup do not establish phone acceptance. No connected Pi/ESP32/lock was
tested for this batch, and presentation-attack detection is not implemented;
these steps do not establish production or vehicle safety.

- Scan once, reconnect after app/host restart, and reconnect after the host IP
  changes. With multiple hosts, connect only to the scanned identity.
- Pair with an administrator-issued invite and the intended user's host PIN.
  Cancel PIN entry, enter incorrect PINs, and restart the app during the
  server-side cooldown. Verify that pairing and sensitive actions cannot proceed
  without a valid host grant, including after an update.
- From both the connected and offline screens, open Sign out and cancel;
  the saved phone credential must remain. Confirm sign-out and verify that
  reconnecting requires a new invitation QR and the user's PIN. Backend users,
  PIN verifiers, other phones, and face templates must remain.
- Return to Android after backgrounding and verify that only the paired
  account's PIN is requested before content loads. Cancel/back must remain
  locked, with Retry and Sign out available. Wrong PIN and temporary disconnect
  must preserve the pairing. Returning from a file picker must preserve pending
  exports while still requiring PIN before dashboard access.
- On PC, sign in once with name and PIN, then let the session expire or restart
  the host. The same browser should request only PIN. Explicit sign-out must
  restore account selection. Changing accounts in another tab must clear any
  unsubmitted PIN draft.
- Enroll a real face, verify the saved template, cancel/retry enrollment, and
  try frames containing no face or multiple faces.
- Make the host camera unavailable, then start paired-device-camera enrollment.
  Confirm an error dialog remains visible after capture ends and explains how
  to retry. A new person's card must show **No face template**. Release the
  camera and retry from that card; dismissing the error must not start a session.
- On PC, start Unlock from the phone and verify that the PC webcam captures the
  face and that every lock or ignition result is labelled simulated. On Pi,
  verify that lock, unlock, ignition, reset, timer, startup, and shutdown paths
  cannot send serial output and that both UIs report physical state unconfirmed.
  Pi camera enrollment and administrator data operations may still be checked.
  Keep the host dashboard open and confirm it automatically shows the actual
  continuous PC camera view and progress in Control's existing camera area,
  even while recognition is processing a frame. Change dashboard
  tabs during the phone scan and confirm the scan continues; return to Control
  to view it again. The image must clear when the scan ends.
  Correct PIN alone must not change simulated state; unknown faces and camera
  failures must deny the operation. On PC, complete same-person simulated
  ignition, then verify simulated stop, lock, and reset. Inspect status and logs
  after each operation. Phone-camera unlock must not be offered.
- As an administrator, add/remove users, reset a user PIN, change access, issue
  an invite, and revoke one phone without affecting another. As an ordinary
  user, verify logs, full user lists, settings writes, and administrator actions
  remain unavailable. A directory entry without a template must not count as an
  enrolled face.
- Save and refresh all settings. Liveness/failure-lockout retain existing
  backend limits; a setting switch does not add an enforcement algorithm.
- Refuse permissions, scan an invalid QR, use a wrong key, present a certificate
  that does not match the QR pin, and try an HTTP-only host. Credentials must
  not be sent after TLS verification fails, and there must be no HTTP fallback.
  Interrupt Wi-Fi mid-session and reconnect; commands must not repeat.
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
account, run `bash scripts/install-wireless-pi.sh`. It uses sudo where needed
and requires an existing OpenSSL executable; set `BASS_OPENSSL` if `openssl` is
not on `PATH`. The installer does not add an OpenSSL package. The defaults are
`~/faceid/device.json`, `~/faceid/device.tls.pem`, `~/faceid/faceid.db`, and
`~/faceid/pairing/` for the exported label. The installed systemd service keeps
the resolved OpenSSL path so later starts use the same executable.

To preserve the fixed QR when moving the host, copy `device.json` and its
companion `device.tls.pem` securely to the transfer directory. If moving the
existing authorization database, also copy `device.auth.key` and the SQLite
database from the same stopped-service backup. Restrict all sensitive files
with `chmod 600`, and run:

```bash
BASS_DEVICE_CONFIG_SOURCE=/path/to/transferred/device.json \
  bash scripts/install-wireless-pi.sh
```

The installer finds the companion TLS and PIN-secret files from the JSON
filename. For a new empty state, a missing TLS file is provisioned before the
first identity binding. For an existing database, a new or mismatched
certificate fails the offline binding check; restore the matching coherent set
or use the explicit stopped-host rotation below. If PIN credentials exist but
the matching `device.auth.key` is missing, startup fails closed. A different
existing destination identity is not overwritten. The installer transfers the
auth-key sidecar when present and completes schema migration plus database
identity binding before it starts the service.

Set `FACEID_DB_PATH` to the existing database before installation when its
location differs. The installer refuses to overwrite a different identity or
start alongside enabled/active legacy camera services. Review its message
before explicitly disabling those services. Inspect the installed service with
`systemctl status faceid-wireless.service` and
`journalctl -u faceid-wireless.service -n 50 --no-pager`.

On the Pi itself, open `http://localhost:5057` using its browser (substitute
`BASS_DASHBOARD_PORT` if changed). Controls and the Settings QR use that running
service automatically. Android connects separately to the Pi's HTTPS port 5056
(`BASS_PORT`). Scan the Pi's QR on the same network, then either UI can operate
the Pi camera. Phone enrollment appears in the Pi dashboard after Refresh.
There is no second frontend service or Connect action.

Back up `device.json`, `device.tls.pem`, `device.auth.key`, and SQLite together
as one coherent stopped-service snapshot. The first two preserve the fixed QR
and certificate pin; the auth key is required to verify the PIN records in that
database. Code deployment does not automatically transfer configuration,
credentials, schema, or data.

## Host authorization and recovery

Authorization adds tables to the existing SQLite database; it does not replace
face templates or create a second database. The Pi installer runs the additive
migration before starting the service.

### Windows staged migration

Keep the host and every other SQLite writer stopped from the backup through the
final switch. On this workstation, the active database is
`C:\Users\Vampy\face-ui\.cache\mock_faceid.db`, the persistent identity is
`C:\Users\Vampy\.bass\device.json`, and the private migration directory is
`C:\Users\Vampy\.bass\backups\20260919-084531-security-migration`. Before
conversion, confirm that `before\source.db` is a coherent SQLite snapshot and
that `before\source-exact.db`, `before\device.json`, and the original QR are
retained. The initial snapshot has no TLS or auth-key companion.

The staging commands below document this completed migration. Their output
paths now exist; do not rerun conversion into them. A later migration needs its
own verified backup and new private output directory.

From the repository root, first inspect the stopped snapshot without loading
legacy pickle. Only use `--trusted-legacy` after separately verifying the exact
legacy stream and its provenance:

```powershell
$repo = 'C:\Users\Vampy\face-ui'
$run = 'C:\Users\Vampy\.bass\backups\20260919-084531-security-migration'
$python = Join-Path $repo '.venv-wireless\Scripts\python.exe'
Set-Location $repo

& $python scripts\migrate-face-templates.py --dry-run `
  "$run\before\source.db" "$run\candidate\templates.db"
& $python scripts\migrate-face-templates.py --trusted-legacy `
  "$run\before\source.db" "$run\candidate\templates.db"
```

Preserve `candidate\templates.db` and its manifest unchanged as the converter
result. Create `candidate\faceid.db` as a separate copy for authorization
migration, and stage `candidate\device.json` with exactly the same bytes as the
backed-up identity. Then prepare the additive schema and bind that candidate to
its newly created TLS and auth-key companions without starting hardware or a
listener:

```powershell
$env:FACEID_DB_PATH = "$run\candidate\faceid.db"
& $python bass_wireless.py --mode pc `
  --config "$run\candidate\device.json" --migrate-security-only
& $python bass_wireless.py --validate-config "$run\candidate\device.json"
```

Confirm the candidate database, `device.json`, `device.tls.pem`, and
`device.auth.key` form one matching set. Keep the host stopped while replacing
the active database and publishing those staged sidecars together. Do not
modify the converter result, manifest, or stopped-source backups. See the dated
[delivery record](android-delivery-status.md#current-pc-runtime-and-data) for
the retained backup locations and subsequent data-preservation checks.

Start the switched host from the repository root with both active paths
explicit in the same PowerShell session:

```powershell
$env:FACEID_DB_PATH = 'C:\Users\Vampy\face-ui\.cache\mock_faceid.db'
$env:BASS_DEVICE_CONFIG = 'C:\Users\Vampy\.bass\device.json'
& .\scripts\start-wireless.ps1 -Mode pc
```

Existing administrators can sign in at `http://localhost:5057`. Legacy installs
without a configured administrator require the documented offline account
recovery procedure; upgrading does not reopen first-owner claim. On the PC,
an explicitly confirmed developer reset can instead prepare a fresh phone-first
claim. Set each user's host PIN, show their invitation QR and pair each phone
by scanning it and entering that PIN. The retained device UUID and
legacy key do not make an older unpinned QR valid because the first TLS identity
adds a certificate pin. Liveness remains enabled by default while PAD is
unavailable, so face scans stay blocked. An administrator may explicitly
disable liveness only for reduced-security prototype testing.

If validation or startup fails, stop the host and keep the failed hardened set
for diagnosis. Rollback stays offline: preserve the newly created TLS/auth
artifacts and the immutable pre-migration snapshots, then repair the staged
migration or re-enroll the affected face before another reviewed switch. Never
restart the old pickle/shared-key software or expose it to the LAN.

### Manual Pi authorization migration

For a manual Pi migration, stop every database writer and set
`FACEID_DB_PATH` explicitly:

```bash
sudo systemctl stop faceid-wireless.service
FACEID_DB_PATH=/home/pi/faceid/faceid.db \
BASS_DEVICE_CONFIG=/home/pi/faceid/device.json \
  .venv-wireless/bin/python bass_wireless.py --mode pi --migrate-security-only
```

The Pi migration path has only been exercised with isolated temporary
databases. It has not migrated, replaced, or restarted a real Pi database, and
no Pi deployment has been performed.

Each migrated database records the configured `device_id` and the SHA-256
fingerprint of its TLS certificate. Normal startup checks both before opening
hardware, HTTPS 5056, or dashboard HTTP 5057. Restore the matching four-file
backup when they differ.

For an intentional certificate replacement, stop the host and take a coherent
backup first. Replace `device.tls.pem` through an operator-controlled process,
then bind it and revoke the old authorization state with an explicit database
path:

```bash
FACEID_DB_PATH=/home/pi/faceid/faceid.db \
  .venv-wireless/bin/python bass_wireless.py --mode pi \
  --config /home/pi/faceid/device.json \
  --rotate-tls-authorization-only
```

The command does not create or replace an existing valid TLS file. It
atomically binds the certificate already on disk and revokes every phone and
local credential, operation grant, and pairing invite. After success, start the
host, sign in locally with an administrator PIN, display the replacement QR,
and issue new user invites. Without this explicit rotation, a replacement TLS
file and bound database are rejected; printing a QR alone does not re-pair or
revoke anything.

If an existing administrator loses the PIN, keep the service stopped and run
the local interactive recovery script on the host. It accepts the new PIN only
through the terminal prompt and revokes that user's existing phone credentials
and operation grants:

```bash
.venv-wireless/bin/python scripts/recover-host-admin.py \
  --db /home/pi/faceid/faceid.db \
  --config /home/pi/faceid/device.json \
  --user 'Admin Name'
```

Restart only after reviewing the command result and confirming the database,
`device.json`, `device.tls.pem`, and `device.auth.key` belong to the intended
host. This recovery path does not claim a successful real-Pi migration.

## Face-template migration

The runtime stores templates in the strict `BASSF001` numeric format: at most
10 finite 512-value little-endian float32 embeddings with an exact validated
length. Runtime code does not import or call pickle. A legacy template is
rejected with a migration-required diagnostic instead of being deserialized.

This conversion is an offline operation because legacy conversion itself uses
pickle. Run it only on a verified, trusted existing database while the wireless
service and every other database writer are stopped. Never convert an unknown
or suspected-tampered database; re-enroll those users through the authenticated
host instead.

From the repository root, inspect the source without deserializing legacy data:

```bash
python3 scripts/migrate-face-templates.py --dry-run \
  /home/pi/faceid/faceid.db \
  /home/pi/face-template-migration/faceid.db
```

After verifying the source and approving legacy deserialization, create a
separate migrated copy:

```bash
python3 scripts/migrate-face-templates.py --trusted-legacy \
  /home/pi/faceid/faceid.db \
  /home/pi/face-template-migration/faceid.db
```

The source is opened read-only. The output directory may be one new private
directory or an existing owner-only directory; the output database and its
`.manifest.json` must not already exist. The tool refuses overwrite, uses the
SQLite backup API, verifies the source did not change, checks integrity, foreign
keys, row counts, and the final strict template format, then writes hashes and
counts without names or templates to the manifest.

These commands create and validate a separate copy. They do not replace the
active database or restart the service. The dated delivery record distinguishes
the workstation execution from the remaining work; a real Pi migration,
reviewed replacement, restart, and rollback check remain unperformed.

The face-template conversion is a content migration. Authorization, ownership,
and request receipts also require the additive schema preparation described
above. Startup applies that schema to `FACEID_DB_PATH`; configure the actual
target database before starting a host. See the dated delivery record for the
completed PC migration and the still-unperformed Pi migration.

Pi Camera still requires separate hardware acceptance. Real ESP32 lock and
ignition output remains deliberately blocked until command acknowledgement and
position feedback are implemented and accepted. PC simulated controls cannot
satisfy physical-hardware acceptance.
