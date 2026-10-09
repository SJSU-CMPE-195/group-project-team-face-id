# PC Bluetooth client

## Problem and scope

The Windows dashboard currently operates its own local host. Team members
without Android phones need to activate or pair with a Raspberry Pi and use
the existing user, enrollment, and audit screens over Bluetooth.

Keep the existing PC host on port 5057. Add a separate Windows client on
`http://127.0.0.1:5058/pc-client`. The Pi remains the authority for users,
roles, PINs, operation grants, enrollment receipts, and device revocation.
Remote clients retain the existing restriction on vehicle controls. Do not
start another face-recognition host or copy the Pi database to Windows.

## Design evidence

- [Bleak client API](https://bleak.readthedocs.io/en/latest/api/client.html):
  retain discovered device objects and use acknowledged characteristic writes.
- [Bleak Windows backend](https://bleak.readthedocs.io/en/latest/backends/windows.html):
  keep all WinRT BLE work on one persistent asyncio thread.
- [Python SSL Memory BIO](https://docs.python.org/3/library/ssl.html#memory-bio-support):
  reuse the existing opaque BLE tunnel and manually pinned TLS implementation.
- [Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/SecCrypto/example-c-program-using-cryptprotectdata):
  protect saved credentials for the current Windows user and machine.
- [Web Bluetooth](https://developer.mozilla.org/en-US/docs/Web/API/Web_Bluetooth_API):
  browser availability is limited; the local Python bridge keeps the existing
  browser UI usable without a new browser TLS implementation.

## Implementation steps

1. `wireless/ble_client.py`, `wireless/ble_http.py`,
   `scripts/ble-diagnostic.py`: extract the existing diagnostic into a shared
   client. One request at a time, fresh pinned TLS per request, exact JSON
   framing, and drained close. A complete response survives teardown failure;
   uncertain mutations never retry automatically. Verify `/health` UUID and
   API v4 before sending the saved bearer, then verify `/api/me`.
2. `wireless/pc_client_store.py`, `wireless/pc_client_cards.py`: validate QR v3
   identity and purpose, atomically persist one selected pairing and pending
   commissioning request using DPAPI. Keep PINs and grants out of persistence.
3. `wireless/pc_client.py`, `wireless/pc_client_app.py`: expose a loopback-only,
   same-origin, CSRF-protected browser service. Keep credentials server-side.
   Preserve the existing `/local/security` and `/local/wireless/api` UI
   contracts, but send only ordinary remote API requests to the Pi. A RAM-only
   approval ticket defers the real PIN grant until it can be acquired and used
   with the mutation under one transport lock. Bind sessions and pending
   results to their host, client, and user.
4. `src/pc-client/*`, `src/main.jsx`, relevant security and invitation
   components: add scan, card import, activation, invitation pairing, recovery,
   saved reconnection, and clear connection/error states. Reuse existing
   dashboard layout and enrollment receipt recovery. Provide JSON invitation
   and recovery downloads so a phone is unnecessary.
5. `bass_pc_client.py`, `scripts/start-pc-client.ps1`,
   `scripts/start-pc-client.cmd`, `scripts/factory-owner-qr.py`, `README.md`,
   `docs/pc-bluetooth.md`: provide the launcher, export activation JSON alongside
   the existing QR artifacts, and document setup and verification boundaries.

## Constraints and verification

The branch is `bluetooth-transport`; retain all earlier work and unrelated
untracked plans. The user authorized committing and pushing the BLE host and
PC client for teammates to install. Pi deployment, database migration, service
restart, test suites, and browser automation remain outside this delivery.

The installed Windows environment has Flask, Cheroot, and cryptography.
Bleak is not installed locally. The Windows client and diagnostic share the
declared Bleak 3.0.2 dependency. The team installation guide includes its
official link, prerequisites, branch checkout, and build/start commands.

Before editing: Python compileall, project-local Vite build, and ESLint all
passed. Logs are in `%TEMP%/bass-pc-ble-20261009`. Repeat these checks and parse
the PowerShell launcher after implementation. Real Windows-to-Pi Bluetooth,
four-client controller capacity, PIN and camera flows require device/UI
acceptance; compilation does not establish those outcomes.

## Implementation snapshot — 2026-10-09

The PC entry point, pinned BLE client, DPAPI state store, browser security
boundary, commissioning/recovery forms, invitation JSON download, and existing
dashboard integration are implemented on `bluetooth-transport`.

Review fixes bind browser requests and deferred PIN approvals to both the
saved credential generation and the current BLE connection. The approval
deadline is checked after queueing, immediately before Pi authorization.
Commissioning retry preserves the original generated credentials without
saving the PIN. Recovery export requires fresh Pi PIN verification. Explicit
forgetting cannot discard an uncertain commissioning request or an unexported
recovery card. PC enrollment metadata survives browser restart.

| Check | Result |
| --- | --- |
| Python compileall, before and after | PASS |
| Project-local Vite build, before and after | PASS |
| Project-local ESLint, before and after | PASS |
| PowerShell launcher parser | PASS |
| Tracked diff and new-file whitespace | PASS |
| Windows Bluetooth device inventory | Adapter and LE enumerator report OK |
| Local Bleak installation / PC runtime launch | NOT RUN; delivery is for teammate installation |
| Real Windows-to-Pi BLE, camera, and multi-client acceptance | NOT RUN |
| Test suite / browser automation | NOT RUN |

Final compiler/build logs are in `%TEMP%/bass-pc-ble-20261009`. Vite retains
the pre-existing stale Browserslist-data warning. No Pi deployment, schema
migration, or existing-host restart was performed. The PC addition has no new
Pi schema change; it requires the API v4 host migration documented in the
Bluetooth transport plan. A source-install guide is provided; this delivery
does not include a packaged Windows executable.
