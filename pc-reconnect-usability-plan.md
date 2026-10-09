# PC reconnect and remote-operation clarity

## Problem and scope

The Windows client can lose its BLE connection and fail to reconnect until the
user forgets a valid pairing. Its status page also does not explain which Pi
operations the signed-in account can perform. The user confirmed that Users and
Logs navigation exists and requested the same remote functions as the Android
app.

Keep the Android role model: members can view their account and logs;
administrators and the owner can manage users and enroll faces with the Pi
camera. Vehicle controls remain host-only. Do not change credentials, roles,
database schema, BLE framing, or replay uncertain mutations.

## Evidence and approach

- Bleak requires an explicit disconnect before discarding a client, including
  cleanup after a connection loss. The current helper skips that call when
  `is_connected` is false. See [official Bleak usage](https://bleak.readthedocs.io/en/latest/usage.html).
- Saved pairing fields and server token storage match. A pairing invitation's
  five-minute expiry does not expire an established paired device.
- Reconnect currently hides the failing stage. Successful commissioning also
  hides a subsequent authentication failure; retain that committed pairing and
  report the separate connection problem.
- The deployed hardware failure has not been captured. Source defects can be
  corrected now; the reported five-minute disconnect still needs a device retry
  and a precise error if it continues.

## Steps and ownership

1. `wireless/ble_client.py` — BLE protocol executor: always run bounded Bleak
   disconnect cleanup, including after an unsolicited disconnect. Preserve
   existing deadlines, certificate checks, and poison-until-disconnect behavior.
2. `wireless/pc_client.py` — security executor: clean an unusable client before
   scanning, report reconnect stages, preserve completed future errors, and keep
   a safe connection error in memory for the local UI. A completed pairing stays
   saved even if its next authentication fails.
3. `src/App.jsx`, `src/components/RemotePiPanel.jsx`,
   `src/pc-client/PcClientGate.jsx`, `src/pc-client/PcClientProvider.jsx` — web
   executor: expose existing Users/enrollment and Logs entry points on the remote
   status page, show the account's role, and retain useful connection errors.
   Transient failure guidance must recommend reconnecting with the saved pairing.
4. `docs/pc-bluetooth.md`, this plan — root: document the operation/role boundary,
   reconnect behavior, and verified results; review the combined changes and
   publish the correction on the existing delivery branch.

## Verification

Baseline captured on 2026-10-09 from `21e793d`: Python `compileall`, project-local
Vite build, and project-local ESLint all passed. Outputs are in the task scratch
directory. Compare the same final checks and review `git diff --check`.

No test suite or browser automation is authorized. Real Windows/Pi reconnect,
extended connection lifetime, and role-specific UI acceptance remain manual.
No database migration is required for this correction.

Implementation checks on 2026-10-09:

| Check | Baseline | Final |
| --- | --- | --- |
| Python `compileall` for wireless and PC entry points | PASS | PASS; no diagnostics |
| Project-local Vite production build | PASS | PASS |
| Project-local ESLint | PASS | PASS; no diagnostics |
| Tracked diff whitespace check | Clean | PASS |
| Windows/Pi reconnect and five-minute disconnect reproduction | NOT RUN | NOT RUN |

No Pi files, database state, stored pairing credentials, Android files, or runtime
dependencies were changed. The background-launch and automatic-reconnect ideas
remain outside this correction.
