# One host connection for phone and dashboard

## Problem and result

The dashboard's manual backend selection can point at a different service from
its fixed-port pairing QR. Pi installation currently starts the wireless API
without serving the dashboard. A phone and the host's own browser should use
one backend, identity, database, and camera without another Connect action.

The existing fixed QR already provides device identity and its pairing key.
Android discovers that identity's current network address and port. Preserve
that format and all existing labels; do not embed a changing Wi-Fi address.

## Scope and decisions

- Serve the built dashboard at `http://localhost:<wireless-port>` on the PC/Pi.
- The host dashboard automatically uses its same-origin local bridge. Remove
  the normal backend URL selector and ignore previously saved manual URLs.
- The local bridge requires a real loopback connection, a valid local Host,
  and trusted request origin. It supplies the existing credential inside the
  server. Direct wireless API requests still require Bearer authentication.
- Load the on-demand QR from that same backend. Preserve QR hiding, abort,
  no-store, and rejection of access through a LAN address.
- Build `dist` on a development machine and deliver it with the Pi checkout.
  Pi installation checks this before making changes. Python serves the files;
  no Node runtime, dependency, or service is added to the Pi.
- Existing PIN gates, backend-camera verification, MJPEG ownership, and face
  data remain authoritative. No Android update or database migration is needed.

## Steps and file ownership

1. `wireless/api.py`, `wireless/local_dashboard.py`, `bass_wireless.py`,
   `scripts/install-wireless-pi.sh`: backend executor adds the guarded local
   bridge and built dashboard, binds QR to the loaded identity, and checks Pi
   build artifacts before installation.
2. `src/App.jsx`, `src/hooks/useAppState.js`,
   `src/components/ConnectionPanel.jsx`, `src/components/DevicePairingCard.jsx`,
   `src/components/SettingsTab.jsx`: web executor removes manual connection
   setup and uses the same origin for controls and pairing.
3. `scripts/local-wireless-proxy.js`, `public/sw.js`: root aligns the development
   proxy with the production local bridge and excludes all local API/QR
   requests from the service-worker cache.
   `src/hooks/useAppActions.js`, `src/components/UsersTab.jsx`: root confines
   legacy Face API deletion to simulation; device deletion uses the host's
   existing users endpoint and requests Refresh if its directory is stale.
4. `README.md`, `docs/android-wireless.md`, `docs/android-delivery-status.md`,
   previous connection/QR plans: root updates the supported launch, deployment,
   and verification instructions.

## Verification and activation

Save pre-change and final Python compilation, direct Vite build, and JavaScript
syntax output under `%TEMP%/face-ui-unified-host/`. Compare these outputs and
run targeted ESLint and diff whitespace checks. Do not run aggregate build
scripts that include tests, automated tests, or browser/phone automation.
An independent static review checks origin/authentication boundaries, streaming,
same-host QR routing, and deployment completeness.

After the code and build are ready, activate only this project's backend with
the existing database and device identity. The previous process termination was
rejected by automatic approval review; if that restriction persists, provide a
guarded manual restart helper. Record read-only HTTP observations separately
from the user's visual/phone acceptance. No Pi hardware is connected, so Pi
installation and physical camera/actuator acceptance remain unperformed.

## Local delivery

Implemented and activated on the PC by the user on 2026-09-15. The backend
serves the final dashboard at `http://localhost:5056`; the existing development
page on 5173 reaches the same backend and QR. Read-only HTTP checks confirm
served build bytes, shared identity, expected local access/authentication
responses, and unchanged pairing configuration/users-table digests across
restart. Compiler, syntax, targeted lint, and independent static review passed.
See [delivery evidence and remaining manual acceptance](docs/android-delivery-status.md).
