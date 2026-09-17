# Backend camera preview

## Problem and outcome

The host dashboard needs a continuous view during phone-triggered face scans.
Waiting for recognition before capturing another frame makes positioning slow.
An open dashboard connected to that same host should display the continuous
image, activity, and result in Control's original camera viewport, regardless
of which client started the operation. Reuse the existing scan status area.

## Approach and ownership

1. `car_face_auth/src/camera_capture.py`, `car_face_auth/src/pi_runtime.py`,
   `car_face_auth/src/pc_runtime.py`, `pi_device_api.py` (backend executor):
   use one session-owned camera producer to capture continuously, independently
   of recognition. Share the latest BGR frame with recognition and a reduced
   JPEG with the display stream; retain no queue or recording. Recognition
   consumes each sequence at most once. End capture with its session and keep
   ownership until a blocked camera read has actually released the device.
2. `src/hooks/useApi.js`, `src/hooks/useBackendCameraPreview.js`,
   `src/components/ControlTab.jsx` (web executor): connect a native image element
   to one continuous MJPEG response in the original Control camera viewport.
   Poll only status metadata. Use the existing status area for progress and
   completion. Detach the stream on completion/disconnection/backend changes.
   Observing another client's session does not take over or cancel it.
3. `public/sw.js`, `scripts/local-wireless-proxy.js`,
   `docs/android-wireless.md`, `docs/android-delivery-status.md` (root): exclude
   the proxied image API from offline caching, review stream proxying, and
   document endpoint semantics, activation, and manual acceptance boundaries.
4. Local backend activation (root): preserve the current database and pairing
   identity, reload the new code, and verify authenticated/proxied API access.
   The existing Android APK remains compatible.

## Contract

- `GET /api/camera/status`: camera source, latest host-camera session public
  status plus kind, frame sequence number, and frame availability.
- `GET /api/camera/frame?session_id=...`: the matching active session's cached
  JPEG (200), no current frame (204), or missing session identifier (400).
- `GET /api/camera/stream?session_id=...`: one continuous
  `multipart/x-mixed-replace; boundary=frame` response for an active session;
  missing identifier is 400 and an inactive/mismatched session is 204. The
  response ends on completion, cancellation, capture failure, or stale frames.
  Viewer disconnection does not cancel the scan or stop the camera.
- Existing wireless bearer authentication and localhost proxy protections apply.
  Responses use `Cache-Control: no-store`. No camera is opened by a preview GET.
- Only backend-camera images are published. Preview has no actuation authority
  and does not change recognition or PIN rules. No database migration, new
  dependency, recording, Android change, or Pi deployment is required.

## Verification

Record Python byte compilation and a direct Vite build as the before-change
baseline, then compare with final compiler/build output and targeted ESLint.
Review frame/session lifetime, cancellation ownership, and credential handling.
Perform read-only runtime/API checks after activation. Per the working agreement,
no automated tests or UI automation are added or run. The user verifies the
actual phone-triggered camera image and progress in the host dashboard.
