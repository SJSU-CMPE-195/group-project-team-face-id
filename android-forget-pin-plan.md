# Forget device with optional PIN reset

## Outcome

Both Android Forget device buttons open the same confirmation dialog. An
unchecked-by-default option also resets this installation's BASS PIN. Cancel
or dismissal changes nothing. Forgetting alone keeps the PIN and its failure
and cooldown state. Resetting requires forgetting the stored pairing first;
the next pairing creates and confirms a new PIN and scans the device QR.

The user explicitly requested this reset option. It replaces the older rule
that Forget device always retains the PIN. The option does not require the old
PIN, and cannot keep the paired credential while resetting the PIN. Backend
users, face templates, the fixed QR, and other clients are unaffected.

## Steps and ownership

1. `android-app/app/src/main/java/com/bass/app/PinStore.kt`,
   `PinProtection.kt`, `AppViewModel.kt` (Android executor): add a focused PIN
   clear operation, cancel pending PIN/session/connection work, and clear
   pairing before the optional PIN reset. Handle partial storage failure
   without resuming old actions or reporting a successful reset incorrectly.
2. `android-app/app/src/main/java/com/bass/app/ui/`, `AppModels.kt`,
   `android-app/app/src/main/res/values/strings.xml`,
   `android-app/app/src/main/res/values-zh-rTW/strings.xml` (Android executor):
   route both buttons through one confirmation flow, with a labelled accessible
   checkbox and fresh default selection each time.
3. `android-app/app/build.gradle.kts` (Android executor): build version 0.3.1,
   code 4. Run `assembleDebug` before editing and `assembleDebug lintDebug`
   afterwards; save and compare compiler output under
   `%TEMP%/face-ui-forget-pin/`. Keep the existing JDK, SDK, and dependencies.
4. `docs/android-wireless.md`, `docs/android-delivery-status.md`,
   `android-pin-protection-plan.md` (root): update current operation and point
   historical reset guidance to this change. Review cancellation, storage
   ordering, and all Forget callers. Install with `adb install -r` on the
   connected Pixel 7 under the existing USB-update authorization, and verify
   the installed package version.

## Acceptance

Compiler and lint results establish buildability; installation establishes
delivery only. The user checks Cancel, forgetting while retaining the PIN, and
forgetting with PIN reset followed by PIN creation and QR pairing. No automated
tests, UI automation, actual forget/reset action, dependency change, database
migration, or backend restart is part of agent verification.

## Completion

Implemented and installed on the connected Pixel 7 on 2026-09-14. The final
static review and compiler/lint checks passed. Installation preserved the
encrypted pairing and PIN preference files. Package identity and verification
receipts are recorded in [Android delivery status](docs/android-delivery-status.md#android-delivery).
The user performs the actual dialog and reset acceptance steps.
