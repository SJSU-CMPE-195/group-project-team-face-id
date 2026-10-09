# Windows dashboard connected to a Pi

Use the PC Bluetooth client when an Android phone is unavailable. It serves the
existing BASS dashboard at `http://127.0.0.1:5058/pc-client` and connects to the
Pi over Bluetooth. The Pi owns the database, camera, account permissions, and
enrollment results.

The existing `scripts/start-wireless.cmd` still starts a local PC development
host on port 5057. Use `scripts/start-pc-client.cmd` to operate a separate Pi.
Only the latter needs a Windows Bluetooth adapter. Internet and a shared Wi-Fi
network are unnecessary after installation.

## Dependencies and startup

Use Windows 11 with a Bluetooth LE adapter. Install
[Python 3.12](https://www.python.org/downloads/windows/),
[Node.js 22.12 or newer](https://nodejs.org/en/download), and
[Git for Windows](https://git-scm.com/downloads/win), with their commands on
PATH. The client uses Flask, Cheroot, cryptography, and
[Bleak 3.0.2](https://pypi.org/project/bleak/3.0.2/). Bleak is also used by the
optional Windows diagnostic; it is not a Pi dependency.

In PowerShell, download this branch and prepare the environment:

```powershell
git clone --branch bluetooth-transport https://github.com/SJSU-CMPE-195/group-project-team-face-id.git
cd group-project-team-face-id
python -m venv .venv-wireless
.venv-wireless/Scripts/python.exe -m pip install -r requirements-http.txt
.venv-wireless/Scripts/python.exe -m pip install -r requirements-ble-diagnostic.txt
npm ci
npm run build
```

If you already have a checkout, save your local changes, fetch the branch, and
switch to `bluetooth-transport` before running the dependency/build commands.
The launcher does not install packages or change Windows Bluetooth settings.
Then run from the repository folder:

```powershell
scripts/start-pc-client.cmd
```

Keep that terminal running and open
[PC Bluetooth client](http://127.0.0.1:5058/pc-client). Enable Bluetooth in
Windows and keep the Pi powered on and nearby. The Pi must run the matching
BLE host with API v4 and its required enrollment-receipt migration. See
[Pi host setup](android-wireless.md#pi-handoff).

## Activate or pair without a phone

1. Import the supplied JSON card, paste its JSON text, or import a newly
   exported activation HTML file.
2. Choose **Scan for Pi** and select the intended nearby device. A Bluetooth
   name or address only helps find it; the imported card verifies its identity.
3. For an unclaimed Pi, use its private activation card and choose the owner's
   name and six-digit PIN. Confirm that PIN to download the recovery card and
   keep it outside this PC. First-owner activation does not require the pairing
   button window.
4. For an existing account, ask an administrator to create an invitation in
   **Users** and download its JSON card. Import it and enter that account's
   PIN. The invitation is single-use and expires after five minutes.
5. On later visits, reconnect the saved Pi and sign in with its paired account
   PIN. Changing accounts requires a new invitation for the intended account.

To replace a saved pairing, disconnect and choose **Forget pairing**. This
removes the credential from this PC; it does not revoke that credential on the
Pi. Export and acknowledge any pending recovery card first. A request whose
result is still uncertain must be reconciled before clearing its state.

For owner recovery, import the saved recovery card, open the Pi's recovery
window with its ten-second physical button hold, and choose a new owner PIN.
Recovery replaces the previous owner credentials and issues a new recovery
card; download that replacement before continuing.

The factory export tool now writes `bass-activation.json` alongside the PNG and
printable HTML. Export the existing unclaimed credential; a reset is not
required merely to obtain JSON. Follow the stopped-host command documented in
[factory activation export](android-wireless.md#factory-owner-qr-and-full-reset).
Older HTML files contain only an image and cannot be imported. This client does
not decode QR images; obtain a new JSON export instead.

## Dashboard behavior and recovery

Use the PC for the Android app's remote management tasks. The Status page shows
the signed-in role and links to Users and Logs. Select Users to enroll a face or
manage an account; the Pi camera captures enrollment samples.

| Signed-in role | Available operations |
| --- | --- |
| Member | View device status, the account's face-enrollment status, and permitted logs. |
| Administrator | Add users, enroll or replace faces, manage face access, issue pairing invitations, and manage permitted paired devices. Owner-protected records remain restricted. |
| Owner | Administrator operations, the owner's face enrollment and paired devices, and administrator-role assignment. |

Management actions require a fresh PIN for the signed-in account. Pairing a PC
does not promote that account to administrator. If Users has no management
buttons, check the displayed role and ask the owner to review the account's
permissions. Vehicle controls remain available only on the Pi's local dashboard.
Live camera video is not transported over BLE; enrollment progress and results
are available as JSON.

The saved pairing survives closing the client and restarting Windows. If the
connection drops, scan and reconnect to the saved Pi; a timeout does not require
another invitation or Forget pairing. Connection errors identify whether the
failure occurred while connecting Bluetooth, verifying the Pi, or authenticating
the saved pairing. A completed pairing stays saved if its next connection check
fails. A confirmed revoked credential requires a new invitation.

Only one API operation runs at a time on this PC connection. A pending PIN
approval is kept briefly in memory; the bridge requests the real Pi grant
immediately before the associated action. A wrong PIN is reported when that
action runs. Disconnecting or closing the client is not proof that an action
was cancelled.

Commissioning writes save their request ID and generated credentials before
sending. If a response is lost, use the explicit retry for that same pending
request. After restarting the bridge, re-enter the original PIN; PINs are never
saved. A different PIN cannot replace an already committed request. Enrollment
uses the host's durable request receipt and does not automatically repeat capture.
The PC browser keeps only the enrollment request metadata in local storage so
it can look up the original receipt after a browser restart. These records are
scoped to the host, paired client, and account; they contain no PIN or bearer.

The selected pairing and pending commissioning metadata are protected by
Windows DPAPI for the current user and computer in
`%LOCALAPPDATA%/BASS/pc-client-state.dpapi`. Copying that file to another computer
does not migrate the pairing. Do not delete it to clear an uncertain request.
Browser JavaScript receives neither the saved bearer nor the Pi's operation
grants. Recovery exports are deliberately readable backup credentials; keep
them private.

## Verification boundary

This implementation requires manual acceptance with a Windows BLE adapter and
the Pi. Compilation and UI build checks do not establish Bluetooth discovery,
activation, connection recovery, camera success, or concurrent-client capacity.
No test suite or browser automation is part of this delivery.
