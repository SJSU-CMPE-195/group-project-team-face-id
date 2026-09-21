# BASS delivery status

Publication preparation: 2026-09-21 (America/Los_Angeles), branch
`wireless-https`, based on `7fcaeec5444e7b9ca5055b154c56203fd5319247`.
Fetching origin confirmed that main still matches this base. Implementation and
runtime evidence below was recorded on September 19; the focused TLS suite was
rerun on September 20. Both host listeners were still running during publication
preparation. Sources are Git, compiler/build logs, isolated security regressions,
live listener checks and USB package-manager evidence. Plans remain untracked,
including `pin-return-plan.md` and `security-publication-plan.md`.

The Gradle wrapper's executable bit is included so the new Ubuntu Android CI job
can run `./gradlew`. The first run of [PR #7](https://github.com/SJSU-CMPE-195/group-project-team-face-id/pull/7)
passed Web lint/build. Android SDK setup requested the removed `tools` package;
the workflow now explicitly requests `platform-tools`. Python passed 223 tests
and failed one connection-limit test on a Linux socket timeout. That test now
fills workers and the accepted queue in order, verifies overflow closure and
closes sockets on every exit; the 11-test module passes locally. The corrections
require fresh CI; inspect the PR for its subsequent checks and merge result.

## Returning account login

The PC browser remembers its last successfully authenticated account. First
sign-in requires name and PIN; returning after session expiry requires only that
account's PIN. The stored hint contains host UUID, ownership generation, user ID
and display name. It contains no PIN, credential, or authoritative permissions.
The host selects that exact active account and checks its PIN. It never searches
accounts by PIN. Creating another account as an administrator does not change
which account this browser remembers.

Explicit Sign out or Use another account clears the selection after confirmed
logout or an expired-session response. Wrong PIN and temporary connection errors
preserve it. Host replacement or ownership-generation changes discard stale
hints. Reset clears the hint. Cross-tab identity/session changes clear PIN drafts;
superseded authentication results cannot restore the old account or error.
Browser storage restrictions show a warning and limit remembering to the current
tab. An existing browser without a saved hint must sign in with its name once.
An existing valid 15-minute session remains usable until expiry or logout.

Android retains its encrypted pairing and account identity. A foreground return
validates pinned TLS, the host, and the saved bearer identity, then requests only
that account's PIN before loading dashboard content. Authenticated
`POST /api/session-login` accepts only `{pin}`, checks the current credential's
user with the existing verifier/cooldown, and returns the current identity.
It issues no operation grant and performs no actuator operation. A successful
first pairing or claim submitted in that foreground already checked PIN and
enters directly. Restored interrupted issuance requires entry PIN.

Wrong PIN or cancellation does not erase pairing. Cancel/back leaves a locked
screen with Retry and Sign out. Backgrounding clears the in-memory entry result
and pending operation authorization, while preserving durable pending claims,
transfers, and recovery/export material. Returning from a document picker also
requires PIN before dashboard access. Sign out clears this phone's saved pairing
and account selection; signing in again needs a new invitation QR and account
PIN. This local action does not revoke the server's device record.

The separate sensitive-operation PIN grants and host face-verification gate
remain required. PIN login does not unlock a device. No schema migration or
production dependency was needed for returning login.

## Other retained behavior

- App-owned Web/Android interface text remains English. User-entered names and
  stored product data are preserved.
- Users > Pair phone shows a five-minute invitation QR containing host UUID,
  TLS pin and one-use invitation. Android scans it directly and asks for the
  invited user's PIN; no long token, IP, or port is typed. Public device,
  activation, recovery and transfer materials remain separate.
- The Hardware tab remains in the original sidebar before and after product
  login. Explicit PC simulator mode establishes an independent localhost
  developer cookie automatically. There is no separate developer sign-in,
  one-time launch link, or extra browser interface.
- Pairing/recovery windows use backend-timed 3/10-second button holds. Power
  cycling preserves ownership. Developer reset requires RESET, drains product
  work, backs up DB/identity/keys and atomically clears product data. Durable
  receipts prevent replay from resetting twice; incomplete reset stays in
  maintenance. No real-data reset was performed during this update.
- First ownership requires an activation credential and pairing window. There
  is only one owner; invited administrators cannot alter owner credentials or
  devices. Interrupted issuance retains its exact encrypted request and result.
- Developer controls are absent from ordinary PC/Pi startup and inaccessible
  from the LAN listener. Enabled simulation trusts direct localhost clients;
  its cookie/CSRF checks do not isolate the product from other local programs.
- TLS pinning, per-user PIN cooldowns, revocable device credentials, operation
  grants, atomic audit writes and bounded HTTP serving remain active. PAD is
  unavailable, and enabled liveness policy blocks face operations. Real Pi
  motor/ignition outputs remain blocked pending feedback-protocol acceptance.

## Current PC runtime and data

Runtime observed at 20:12 after the PIN-return update:

- Dashboard: `http://localhost:5057/`, bound to `127.0.0.1:5057`.
- Phone API: `https://192.168.86.20:5056`, bound to `0.0.0.0:5056`.
- Server PID: `26552`; launcher PID: `42028`. These are snapshot values.
- Database: `C:\Users\Vampy\face-ui\.cache\mock_faceid.db`.
  This is the existing PC product database, not an isolated test DB.
- Identity/key files: `C:\Users\Vampy\.bass\device.json`, `device.tls.pem`,
  and `device.auth.key`.
- Logs: `.cache/pin-return.stdout.log` and `.cache/pin-return.stderr.log`.
- Hardware status: powered, outside maintenance, existing legacy ownership.

The equivalent command, using the existing database, is:

```powershell
$env:FACEID_DB_PATH = 'C:\Users\Vampy\face-ui\.cache\mock_faceid.db'
.\.venv-wireless\Scripts\python.exe bass_wireless.py --mode pc --config 'C:\Users\Vampy\.bass\device.json' --host 0.0.0.0 --port 5056 --dashboard-port 5057 --hardware-simulator
```

Do not start a second host while these listeners are running. For a stopped
host, the normal launcher is `scripts/start-wireless.cmd -HardwareSimulator`.

A read-only snapshot with the old host stopped was compared after startup.
All 17 user rows including face data, the PIN/security row, two credential rows,
six settings, ownership and other product tables match exactly. All 216 prior
audit rows remain; startup appended three. UUID/TLS/key file hashes match.
SQLite integrity and foreign-key checks pass. Transient runtime state is
excluded from exact row comparison. No existing account, PIN, pairing, or
invitation was changed by verification.

The earlier ownership schema migration was applied on this PC by normal startup.
Existing populated data remains `legacy`; no owner was silently appointed.
For offline additive preparation, stop the host, retain the explicit DB target
above, and run:

```powershell
.\.venv-wireless\Scripts\python.exe bass_wireless.py --mode pc --config 'C:\Users\Vampy\.bass\device.json' --migrate-security-only
```

The PIN-return change needs no additional migration. No Pi migration/deployment
was performed. Prior private recovery backups remain under
`C:\Users\Vampy\.bass\backups\20260919-185725-hardware-simulator-upgrade`
and `20260919-084531-security-migration`. They are historical snapshots; do not
restore them over later user changes without a deliberate recovery decision.

## Verification

Baseline/final evidence: `%TEMP%/face-ui-pin-return/`.

| Check | Baseline | Final |
| --- | --- | --- |
| Python source compilation | PASS, 74 files | PASS, 74 files |
| Web npm run lint | PASS | PASS |
| Web npm run build, including PWA | PASS | PASS |
| Android Kotlin/debug and release assembly/lint | PASS | PASS |

The initial Android baseline attempt had no SDK environment and failed before
compilation. Using the already-installed SDK and JBR 21 resolved it without a
source/configuration/dependency change. The final Gradle run completed 96 tasks
in 40 seconds. Final lint reports 0 errors and 17 warnings: seven dependency
notices, nine UseKtx suggestions, and one certificate TrustManager notice. The
baseline XML was overwritten by Gradle, so no exact warning-count delta is
claimed. The Web build retains its existing stale Browserslist-data warning.

The full isolated command
`.venv-wireless/Scripts/python.exe -B -m unittest discover -s tests -v`
passes **224/224 tests in 44.006 seconds**. New cases cover stable account IDs,
shared PINs without cross-account selection, inactive/missing users, shared
cooldowns, revoked/version-stale credentials, authenticated phone entry, no
operation grant from login, cookie/CSRF/logout, and credential request limits.
Existing TLS, ownership, recovery, reset and hardware-denial regressions pass.
Fixtures use temporary databases and simulated hardware; some reduce KDF work
for speed. These timings do not measure production PIN throughput.

The live English-only dashboard matches `dist/`, serving
`index-BriuYDNP.js`, SHA-256
`D804BE07D0862BF9A2B63F9A9A35BD6BAB26B40F4CFB3FA99C5369D95DF1DE19`.
Trusted status supplies the retained UUID and ownership generation with no-store
headers. Automatic developer sessions work but cannot authorize product session
or user APIs (401). Removed/cross-origin/foreign-Host developer entry checks pass.
Pinned LAN HTTPS rejects developer access even with a spoofed localhost Host.
The new phone entry route rejects a missing device credential before parsing.
These checks sent no real PIN and performed no reset, power/button event,
invitation issuance or account mutation.

Independent backend and Android lifecycle reviews found no blocking issue.
The Web review identified a cross-tab PIN-draft issue; clearing drafts on
identity/session changes and guarding late failures resolved it. Final source
review and Git whitespace checks pass. No browser automation was run.

## Android artifacts and installation

Package remains `com.bass.app`, version 0.4.0 / code 5, minimum SDK 26 and target
SDK 36. Artifacts:

- Debug: `android-app/app/build/outputs/apk/debug/app-debug.apk`.
  SHA-256: `AEA8F33CE491E6CA9A53291661FD8E8925D699A1C9352A0C1ECF53C9519C279E`.
- Unsigned release: `android-app/app/build/outputs/apk/release/app-release-unsigned.apk`.
  SHA-256: `364B4194EE5FF770449C167602D940C779CE9FBF80D868C35F84A09B48586686`.

Debug signature verification passes. Release has no debuggable flag and remains
unsigned as requested; apksigner reports missing signature metadata. It cannot
be installed or distributed as a production-signed build.

At 20:15:08, `adb install -r` updated the connected Pixel 7
(`34251FDH20091R`) successfully. Its installed base.apk SHA-256 matches the debug
artifact. Original installation time and both app-data directory identities are
retained. Starting MainActivity returned Status: ok for the running app. No PIN
was entered and no product action was performed on the phone. This establishes
package installation and launch, not successful end-to-end login.

## Next use and remaining acceptance

Refresh the existing dashboard. This browser may need name and PIN once to
create its new remembered-account hint; later expired sessions need only PIN.
Open the updated paired Android app and enter the saved account's PIN. Explicit
Sign out clears the remembered selection; Android then requires a new invitation
QR. Use the manual checks in [Android wireless operation](android-wireless.md).

First-owner testing still requires an operator-approved developer reset of the
existing legacy data, followed by a 3-second pairing hold and phone activation.
No automatic reset was performed. The Hardware tab remains available before
login and after reset.

Not run: browser visual acceptance, physical phone QR scanning and PIN-entry
interaction, real camera/SAF and mDNS/TLS commissioning/recovery/transfer flows,
Pi boot, GPIO, real Wi-Fi/hotspot/no-router use, trusted boot, rollback protection,
PAD, MCU completion feedback, or physical actuator safety. No vehicle or
production hardware acceptance is claimed.

Production signing and firmware flashing remain unperformed. Local validation
and focused reviews are not exhaustive security certification. The publication
snapshot above distinguishes these local results from subsequent GitHub CI and
merge evidence.
