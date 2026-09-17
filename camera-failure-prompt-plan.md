# Camera failure prompt

## Problem and result

Android ends capture when the backend reports a camera failure, but hides the
session message with the capture controls. Keep failures visible in the existing
error dialog. Camera diagnostics should explain that another app may be using
the camera and how to retry; they cannot identify which app owns the camera.

## Steps and ownership

1. `android-app/app/src/main/java/com/bass/app/AppViewModel.kt` and both
   `android-app/app/src/main/res/values*/strings.xml` files (Android executor):
   retain terminal failure messages in the existing error dialog, with a
   localized fallback. Preserve successful and cancelled session behavior.
2. `car_face_auth/src/camera_capture.py` and
   `car_face_auth/src/{pc_runtime,pi_runtime}.py` (root): add recovery guidance
   to camera open/read/stall errors while preserving the underlying diagnostic.
3. `docs/android-wireless.md` and `docs/android-delivery-status.md` (root):
   document the prompt, manual acceptance, and build/activation status.

## Verification

Save Python compilation and Android Kotlin compilation before edits under
`.cache/camera-enrollment-failure/`; compare final compilation and Android
assembly/lint results with that baseline. Review the combined task diff against
saved pre-edit copies. No automated tests, browser automation, or camera scans
are run. The user verifies a blocked camera and a successful retry after release.
The Android package needs updating and the backend needs restarting to activate
both changes. No dependencies or database migration are required.
