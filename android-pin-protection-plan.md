# Android PIN protection Plan

Historical plan, completed on 2026-09-11. Its PIN-only manual unlock and Pi-only
camera assumptions are superseded by
[Backend-camera face unlock](backend-camera-unlock-plan.md). Current operation
is documented in [Android wireless operation](docs/android-wireless.md).
The original rule below that Forget device always keeps the PIN is superseded
by [Forget device with optional PIN reset](android-forget-pin-plan.md).

Remove phone-camera face verification from Android Console. Keep phone-camera
enrollment, protected by a separate BASS six-digit PIN with a phone-style
numeric keypad and masked digits. The PIN is local to this Android installation
and is different from both the phone screen-lock credential and the device QR
pairing key. It protects actions in this app; it does not replace host-side
pairing-key authorization or protect other clients holding the device QR.

## Step 1: Store the local BASS PIN

Files:

- `android-app/app/src/main/java/com/bass/app/PinStore.kt` (new)
- `android-app/app/src/main/res/xml/data_extraction_rules.xml`

Change:

Store a randomly salted PIN verifier protected with Android Keystore encryption
in private app storage. Use platform PBKDF2-HMAC-SHA256 on a background thread,
constant-time comparison, and a persisted 60-second cooldown after five failed
attempts. Do not store the plaintext PIN or include it in logs, saved UI state,
or backups. Keep the PIN when forgetting a paired device; do not provide an
unauthenticated PIN reset. No additional dependency or business database
migration is needed.

Use 600,000 PBKDF2 iterations, following the existing-platform option described
in the [OWASP password storage guidance](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html).

## Step 2: Gate pairing and phone enrollment

Files:

- `android-app/app/src/main/java/com/bass/app/AppModels.kt`
- `android-app/app/src/main/java/com/bass/app/AppViewModel.kt`
- `android-app/app/src/main/java/com/bass/app/PinProtection.kt` (new)
- `android-app/app/src/main/java/com/bass/app/ui/BassApp.kt`
- `android-app/app/src/main/java/com/bass/app/ui/screens/PinScreen.kt` (new)
- `android-app/app/src/main/java/com/bass/app/ui/screens/PairingScreen.kt`
- `android-app/app/src/main/java/com/bass/app/ui/screens/UsersScreen.kt`
- `android-app/app/src/main/res/values/strings.xml`
- `android-app/app/src/main/res/values-zh-rTW/strings.xml`

Change:

First pairing requires entering the new PIN twice before accepting/saving a
pairing. Later pairing uses the existing PIN. Each phone enrollment, including
re-enrollment from an existing user row, requires a fresh PIN verification
before creating a directory entry or starting the enrollment session.

Successful verification authorizes only the pending action and its original
device/name/source snapshot. Cancel, background, device change, or stale
completion must not start an operation. Never cache a session-wide unlocked
flag. Mask digits, provide clear/confirm/cancel actions, and clear input after
use. Translate all new Android strings into English and Traditional Chinese.

For an existing installation with pairing but no PIN, require initial PIN setup
before showing operational screens; preserve the pairing and face data. A
normal APK update must not silently bypass the new protection.

## Step 3: Remove phone-camera verification

Files:

- `android-app/app/src/main/java/com/bass/app/ui/screens/ConsoleScreen.kt`
- `android-app/app/src/main/java/com/bass/app/AppViewModel.kt`
- Associated Android string resources above

Change:

Remove the phone camera source selector, preview, and permission flow from
Console face verification. Guard the ViewModel entry point too. Face unlock
and same-person ignition verification use only the device/Pi camera. On the PC
host, explain that its device camera is unavailable and disable those actions;
phone enrollment remains available after PIN verification.

Manual unlock also requires fresh PIN verification, as explicitly selected by
the user. Gate it at the ViewModel command entry point before sending the
unlock request; cancellation must send no command. Other control behavior
remains unchanged.

## Step 4: Compile, deliver, and install

Files:

- `android-app/app/build.gradle.kts`
- `docs/android-wireless.md`
- Changed Android source files above

Change:

Bump the development app version, build the APK, and run Android lint. Compare
against the passing pre-change baseline in
`.cache/android-delivery/android-pin-baseline.log`. Review all sensitive action
callers and the combined diff. No automated tests or phone/browser UI automation
will run. Install the update over the existing app on the connected Pixel 7,
preserving app data, and verify the package version.

### Expected result

Pairing, manual unlock, and each phone-camera enrollment require the BASS PIN.
Phone-camera unlock is unavailable. PIN cancellation, incorrect input, and stale responses
cannot start protected work. APK assembly and lint introduce no new errors.
The user accepts the PIN setup, failure/cancel, enrollment, and camera-source
behavior on the phone; Pi hardware acceptance remains separate.

## Completion snapshot — 2026-09-11

Implemented and installed as development version `0.2.0` (version code `2`) on
the connected Pixel 7 using `adb install -r`. Installation returned Success;
`dumpsys package com.bass.app` confirmed the version. No app data was cleared.

Android assembly and lint passed against the saved baseline. Final evidence:
`.cache/android-delivery/android-pin-final.log`; zero lint errors and the same
ten dependency/KTX warnings as the baseline. Combined static review checked
the PIN gates, cancellation, and original-action context; no blocking grant
bypass was found. No automated tests or UI automation ran.

PIN storage and verification are isolated in `PinStore.kt`; `PinProtection.kt`
owns one challenge at a time. Existing backup rules already exclude all shared
preferences, so they did not need changes. Existing UI callbacks continue to
use the now-protected ViewModel entry points. No dependency or business schema
migration was added. Phone PIN setup and workflow acceptance remain with the
user, and Pi deployment/hardware verification remain pending.
