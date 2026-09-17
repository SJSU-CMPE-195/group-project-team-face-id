# Desktop wireless connection

This records the original development-only connection. The automatic,
production host dashboard and shared QR connection supersede its manual
selection flow; see [the unified connection plan](unified-host-connection-plan.md).

## Problem and scope

During investigation on 2026-09-14, the Android host returned the enrolled user
`GG`, while desktop requests to port 5056 received HTTP 401. The web client did
not send the required authentication. Its simulator has separate browser data.
Both clients must read the existing wireless host.

The desktop development server runs on the same trusted computer as the host.
Keep credentials on that computer and preserve the host's authentication,
database, active phone sessions, and existing browser simulation data.

## Steps

1. `scripts/local-wireless-proxy.js`, `vite.config.js`: add a development-only
   proxy to the fixed loopback wireless host. Require a loopback peer and Host,
   reject cross-origin requests before reading credentials, and attach the
   existing pairing credential on the server. Reject LAN callers.
2. `src/components/ConnectionPanel.jsx`: offer an explicit local connection
   button in the localhost development UI. Select Device API and the proxy,
   preserving other saved settings.
3. `docs/android-wireless.md`: document the desktop connection and existing
   Refresh button for changes made from the phone.

## Verification

Before changes, the project-local Vite build succeeded, with output saved under
`%TEMP%/face-ui-desktop-wireless-sync/baseline-build.log`. Compare the final
Vite build with that baseline and run targeted ESLint on the changed modules.
Use read-only HTTP checks for the shared user list and existing enrollment.
Do not run tests or browser automation. The user verifies the visible UI.

No dependency, schema migration, commit, or phone-backend restart is required.
