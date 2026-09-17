# Device pairing QR in Settings Plan

The same-origin dashboard and QR routing supersede this plan's fixed-port
connection assumptions; see [the unified connection plan](unified-host-connection-plan.md).

Add the current PC or Pi host's fixed pairing QR to its existing React Settings
page. The Android app is unchanged. Reuse the persisted wireless identity and
Python QR generator; do not generate a second identity or add dependencies.

## Step 1: Expose the QR to the local device screen

Files:

- `wireless/qr_export.py`
- `wireless/api.py`

Change:

Extract the existing PNG rendering for reuse by file export and a local-only
read endpoint. Return the device name, ID, and QR image. Require a loopback
connection, a loopback Host, and an allowed local web Origin. Reject remote
origins and LAN clients; do not trust forwarded headers. Disable caching and
ensure the existing wildcard CORS handler cannot expose the response to other
origins. Existing authenticated functional APIs retain their behavior.

## Step 2: Add the Settings card

Files:

- `src/components/DevicePairingCard.jsx`
- `src/components/SettingsTab.jsx`

Change:

Replace the obsolete Notes card with Device pairing. Include Show QR / Hide QR,
the host's name and ID, a large scannable image, loading/retry states, and a
short explanation that scanning grants device control. Follow the existing
web UI styling and language conventions. Load on demand from the wireless
service on this computer, default port 5056, and clear pending requests on
unmount. When opened through a LAN address, explain that the QR must be viewed
in a browser on the host through localhost. Do not use simulated device data.

## Step 3: Verify and document

Files:

- Changed source files above
- `docs/android-wireless.md`

Change:

Compare Vite compilation and ESLint against the saved passing baseline
(`.cache/android-delivery/settings-qr-{build,lint}-baseline.log`). Compile
changed Python modules and review the diff. Document local Settings access and
the requirement to run the wireless host. No database migration is required.
Do not run automated tests or browser/phone UI automation. The user verifies
the actual display and phone scan. Restart only the owned wireless host to
load the endpoint after implementation; preserve identity and face data.

### Expected result

On PC or Pi, open the local web app, select Settings, and press Show QR. The
displayed QR belongs to that host and matches its exported fixed label. A phone
can scan it to pair. No new compiler or lint errors.

## Completion snapshot — 2026-09-11

Implemented in the local working tree. Final Vite build, ESLint, Python
compilation, and diff whitespace checks passed; build/lint logs use the same
baseline paths with `final` in place of `baseline`. No new errors were found.
The existing Browserslist data-age advisory remains non-blocking.

The owned PC wireless process was restarted. A local read of the QR endpoint
returned HTTP 200, the existing device identity, a PNG data URL, `no-store`, and
the exact allowed local Origin. The health endpoint also succeeded. The web
preview on `http://127.0.0.1:4173` serves the rebuilt UI. No automated tests or
browser/phone UI automation were run. Visual display and phone scanning remain
for user acceptance; Pi deployment was not performed. No migration or new
dependency was needed, and no commit was created.
