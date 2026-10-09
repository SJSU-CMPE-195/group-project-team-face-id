# BASS BLE v1 implementation contract

Baseline: `main` at `00063eccbdb89dc447bc781063cf92bad57d830f`.
Work branch: `bluetooth-transport`. Preserve existing untracked plans and attachments.

## Scope and execution order

Android HTTPS -> app loopback byte bridge -> BLE GATT -> Pi byte bridge ->
127.0.0.1:5056 HTTPS -> existing authorization, API, SQLite and host camera.
Support four independent clients. Windows is a diagnostic central, not a BLE
host. Keep the PC loopback dashboard. Phone vehicle controls remain forbidden;
enrollment still requires administrator permission and a fresh one-use PIN grant.
No iOS, video over BLE, background Bluetooth service, new cryptographic protocol,
LAN fallback, production deployment, commits or PR are included.

1. Affected files: this document, existing compiler/build entry points. Record
   baseline output in `%TEMP%/bass-ble-v1-20261009`, freeze contracts below and
   assign independent writers before implementing the prototype.
2. Affected files: security schema/store/authorization, Device API, runtime face
   enrollment. Implement durable receipt transactions and API v4 end to end.
3. Affected files: Pi gateway/server/startup, Android transport/discovery/API.
   Implement opaque TLS bridging, identity bootstrap and orderly channel reuse.
4. Affected files: Android application workflows/persistence and React enrollment
   callers. Integrate request journals, serialized scheduling and recovery.
5. Affected files: installation/diagnostic scripts and operating documentation.
   Compare final compilers/build/lint against baseline; review combined changes.

No tests or browser/device automation are added or run without a further user
request. Acceptance scenarios below are specifications, not observed results.
Python compileall, Android compileDebugKotlin/assembleDebug/lintDebug and Web
Vite build/ESLint are the approved static/build checks. Use existing local tools;
do not install production dependencies as part of this local implementation.

## Listener and TLS identity boundary

5056 binds only 127.0.0.1 and always applies remote API authorization. Its
loopback peer is never proof of local privilege. Public commissioning routes
retain their existing proof requirements; protected calls require bearer,
current role and applicable operation grant. 5057 alone serves the local
dashboard with listener-derived port/scheme, loopback/origin checks, local
session and CSRF. Client Host/Forwarded/local-marker-like headers cannot set
`bass.local_dashboard`; tunneled `local_controls` is always false. Reject local
routes and host-only operations through 5056.

The Pi gateway connects only to TLS 127.0.0.1:5056. It never parses HTTP,
terminates TLS, forwards to 5057, injects headers, sets WSGI state or calls Flask
in process. Validate the persistent device/TLS/database identity before runtime
listeners and advertise BLE only after the TLS listener is ready. Fail closed
without plaintext, LAN/mDNS or replacement-identity fallback.

Android's temporary 127.0.0.1 port is a transport address, never host identity or
persisted pairing address. The expected identity is the saved/QR host UUID and
full leaf DER certificate SHA-256. Keep validity, self-signature, non-CA and
constant-time pin checks; TLS >=1.2, no 0-RTT, no trust-all. Connect directly
with Proxy.NO_PROXY, disable redirects and never weaken hostname verification
for localhost.

Every new logical BLE connection bootstraps pinned TLS -> unauthenticated
/health host UUID/API version validation -> bearer or commissioning secret ->
/api/me paired-device/user validation -> READY. Each HTTP exchange has its own
pinned TLS connection. BLE names, addresses, bonds and BlueZ paths are routing
hints only. Identity errors stop work and retain pairing; 401 stops protected
work, 403 is policy denial, 429 honors Retry-After. Reconnect uses fresh state.

## Enrollment API v4 and durable deduplication

POST /api/enroll/start requires exactly request_id (canonical UUID v4), user_id
(immutable existing user ID), and source='device_camera'. No name alias or
unknown fields. Successful durable acceptance returns immutable HTTP 202:
`{request_id, session_id, status_url:'/api/enroll/requests/<request_id>'}`.
202 is acceptance, not a promise that capture started or completed.

GET /api/enroll/requests/<request_id> exposes owned request state, the existing
session projection when available and the stored terminal result. Retain live
session-ID status/cancel compatibility. All clients send request IDs; do not
leave an old enrollment-start bypass. API protocol becomes 4; QR/config format
remains 3 so host UUID, certificate pin and pairing credentials survive.

The narrow enrollment_requests table has key (product generation, initiating
device ID, enrollment.start, request ID), actor/target identity and auth-version
snapshots, semantic digest, preallocated session ID, immutable start response,
state, terminal result and timestamps. It contains no PIN, grant, bearer, image
or face template and has no identity FK cascade that could erase dedup records.
Fingerprint canonical target user ID, fixed source and API version, excluding
credentials. Auth versions are guards, not a new dedup namespace.

- Authenticate and require current admin first, normalize input, then look up
  the owned receipt. Identical input returns the original 202 with no new grant
  consumption/work; changed input returns 422 request_id_reused. Unknown or
  other-device lookups return 404. Invalid IDs are 400; invalid bearer 401;
  current role denial or a new request's invalid grant 403.
- Receipt replay is exempt from the former enrollment-start burst limit. New
  work still requires a one-use grant from the rate-limited PIN authorization
  endpoint; throttling must not replace a saved acceptance with 429.
- Only NEW requests validate the live target and owner-target policy. Historical
  replay does not resolve a renamed/deleted target by name or perform new work.
- Under the existing security barrier, one BEGIN IMMEDIATE revalidates principal,
  consumes the grant, inserts accepted receipt and immutable 202. Only an
  explicit created=true result may launch. Duplicate races lose at this boundary.
- Before camera side effects, worker CASes accepted -> active. Busy/launch failure
  after acceptance produces a durable failed result; it never retries the ID.
- Under the same authorization barrier, completion revalidates generation,
  initiating device, current admin/owner policy and actor/target versions, then
  atomically writes the bound user's face_encoding, audit and completed receipt.
  Cancellation/timeout and completion compete through the same state CAS; a
  terminal state cannot be resurrected. If completion wins, cancel returns it.
- At startup before serving, accepted/active rows become interrupted without
  replaying capture. Atomic completion means interrupted did not commit that
  operation's face write. Same ID cannot restart; a new attempt needs a new ID
  and fresh grant.
- Keep compact receipts/tombstones independently of the runtime's 300-second
  session cleanup and credential revocation. Explicit product reset can erase
  them and create a new product namespace. No automatic expiry/reusability.

## One-channel scheduling and GATT contract

Each Android connection has one bounded scheduler and one active API exchange.
The scheduler accepts at most 16 active/queued jobs in total.
Cancel is first at job boundaries; other requests are FIFO. A multi-request
snapshot yields after each request. Coalesce duplicate snapshot triggers and
allow one queued poll per session; schedule polling after completion, not by
accumulating timer ticks. Drop stale queued work by connection/session generation.

Issue a PIN grant only when its protected action job dispatches. Grant plus its
dependent write cannot be interleaved with another API. Cancellation before the
mutation is sent can abandon it; after sending, resolve the result before remote
session cancellation. Never persist or transfer a grant across reconnect and
never automatically reacquire/replay. Persist the enrollment ID/input/owner
before the first mutation byte, and save complete results to that owner before
checking whether the current UI generation still matches.

GATT uses one custom service with protocol information and a read+write tunnel
characteristic. UUIDs: service `c74f82e0-a691-4f6f-a6b3-1220c3f7679a`,
protocol info `c74f82e1-a691-4f6f-a6b3-1220c3f7679a`, tunnel
`c74f82e2-a691-4f6f-a6b3-1220c3f7679a`. Protocol info is the single byte `01`.
Require BlueZ options.device on every call, key state by peer
and connection/tunnel generation, and cap independent peers at four. Missing
peer/invalid offset/oversized data/invalid state fails closed. No notifications,
write-without-response, application authorization in advertisements or shared
private characteristic value. Use one outstanding Android GATT operation.

Wire v1: C->S OPEN=[01,01], DATA=[02,<TLS bytes>], CLOSE=[03]; S->C
OPEN_OK=[81,01], DATA=[82,<TLS bytes>], WAIT=[83], CLOSED=[84],
ERROR=[ff,<code>]. Error codes cover BUSY, BAD_STATE, UNSUPPORTED_VERSION,
UPSTREAM_UNAVAILABLE and TIMEOUT. Freeze service/characteristic UUIDs in both
implementations and the diagnostic client. Payload cap=min(negotiated MTU-4,511).
Buffers are bounded at 32 KiB per direction and apply backpressure. Alternate
eligible reads/writes; WAIT delays read 100ms but never blocks writes. Callback
timeout is 10s; uncertainty poisons the link until disconnect. Reject stale
callbacks by BluetoothGatt object and generation; never replay DATA.

## Framing and normal teardown

One accepted app-local TCP socket per HTTP exchange; close its listener after
accept, use Connection: close, fixed-length request streaming and
Accept-Encoding: identity. JSON replies require valid Content-Length and exact
body byte count (including error replies); reject chunked/compressed/EOF-delimited
responses rather than interpreting EOF as success. The API exchange deadline is
60s and Pi TLS/request limits must accommodate that BLE profile.

OPEN -> OPEN_OK -> DATA -> complete framed HTTP result -> HttpsURLConnection
disconnect and opaque-byte drain -> CLOSE -> Pi drains/releases upstream ->
CLOSED -> next OPEN.

1. Keep bridging after HTTP completion/disconnect. Wait for local input EOF and
   confirmation of all preceding DATA writes before sending CLOSE.
2. Pi flushes uplink before half-closing upstream, continues to drain downlink,
   and sends all DATA before CLOSED. CLOSED requires CLOSE received, upstream
   EOF, empty queues, released socket/worker resources.
3. Android may reuse only after CLOSED callback and no outstanding GATT operation.
   Local EOF wait is 2s; CLOSE->CLOSED is 5s. Otherwise invalidate the BLE link.
4. A complete framed HTTP result remains authoritative after teardown failure;
   persist/return it and separately mark transport unusable. Incomplete responses
   are uncertain and use receipt reconciliation, never automatic mutation retry.
5. Bridges do not parse or synthesize TLS close_notify; TLS owns it. HUC does not
   expose that alert, so no success claim depends on observing it.

## Migration, rollout and acceptance

Use existing additive SecurityStore schema migration. On the Pi stop the service,
back up matching device.json/device.tls.pem/device.auth.key/SQLite together, and
run as the configured service user with actual paths:

```sh
FACEID_DB_PATH=<existing-db> BASS_DEVICE_CONFIG=<existing-config> \
  .venv-wireless/bin/python bass_wireless.py --mode pi --migrate-security-only
```

Coordinate API v4 clients and host; older Android clients intentionally reject
v4. Do not roll back to an old writer against live v4 operations/receipts. Code
deployment, schema migration and physical acceptance are separate observations.
Pi runtime dependencies are BlueZ/python3-dbus/python3-gi; Windows diagnostic
uses Bleak. Do not install absent production packages without authorization.

Acceptance matrix (NOT RUN): loopback/forged-header privilege denial; wrong pin
before HTTP and wrong UUID before secrets; same-ID replay/races/conflicting
payload/cross-device/revoked roles; grant/receipt/face commit crash windows;
cancel-versus-completion race; concurrent refresh/poll/grant/start/cancel;
truncated response/final DATA/CLOSED loss/stale callback; four clients with
MTU23/517 and Wi-Fi disabled. Measure TLS/API latency and hardware connection
capacity without claiming those are established by compilation.

References:

- [BlueZ GattCharacteristic](https://github.com/bluez/bluez/blob/master/doc/org.bluez.GattCharacteristic.rst)
- [Android BluetoothGatt](https://developer.android.com/reference/android/bluetooth/BluetoothGatt)
- [Android HttpURLConnection](https://developer.android.com/reference/java/net/HttpURLConnection)
- [TLS closure](https://www.rfc-editor.org/rfc/rfc8446.html#section-6.1)
- [HTTP framing](https://www.rfc-editor.org/rfc/rfc9112.html#section-6.3)
- [Atomic idempotency](https://aws.amazon.com/builders-library/making-retries-safe-with-idempotent-APIs/)

## Implementation snapshot — 2026-10-09 05:29 UTC

Local source: branch `bluetooth-transport`, uncommitted changes based on
`00063eccbdb89dc447bc781063cf92bad57d830f`. No commit, push, PR, deployment,
service launch, package installation or live database migration was performed.
Existing unrelated untracked plans and attachments were preserved.

Implemented the Pi opaque TLS gateway, Android BLE discovery/transport and
single-channel scheduler, API v4 durable enrollment receipts, atomic runtime
completion, Android and dashboard recovery journals, and the Windows health
diagnostic. PC operation retains its local dashboard; fresh activation now
requires the Pi BLE host. The existing simulator enrollment path uses the same
atomic completion boundary with a safe-format simulated encoding.

The combined review covered listener authorization, pinned identity bootstrap,
receipt ownership and transaction boundaries, cancellation/completion ordering,
BlueZ sender authentication, per-peer backpressure, and late client results.
Review findings were fixed before the final build. This was source review and
compiler/build verification, not an executed fault-injection test.

| Check | Before changes | Final source |
| --- | --- | --- |
| Python `compileall` over existing CI paths | PASS | PASS |
| Android `:app:compileDebugKotlin`, cached JDK 17 | PASS | PASS |
| Android `assembleDebug lintDebug`, cached JDK 17 | Not run | PASS; 0 errors, 19 lint warnings |
| Project-local Vite production build | PASS | PASS |
| Project-local ESLint | PASS | PASS |
| PowerShell launcher parser | Not run | PASS |
| Pi installer `bash -n`, CRLF normalized in memory | Not run | PASS |
| Combined tracked diff and new-file whitespace | Not applicable | PASS |

The machine-default Java 25 baseline could not run this project's Gradle
toolchain; the existing cached JDK 17 completed both baseline and final checks.
The Vite Browserslist age warning remains. No dependency upgrade was made to
silence it. Test suites, the PWA runtime self-check and browser/device automation
were not run under the requested verification boundary.

Compiler/build logs are in
`C:/Users/Vampy/AppData/Local/Temp/bass-ble-v1-20261009/`.
The final helper command was:

```powershell
./scripts/build-android.ps1 `
  -JavaHome 'C:/Users/Vampy/face-ui/.cache/android-delivery/jdk17/jdk-17.0.20.1+1' `
  -AndroidSdk 'C:/Users/Vampy/AppData/Local/Android/Sdk'
```

Generated debug APK:
`android-app/app/build/outputs/apk/debug/app-debug.apk` (34,562,820 bytes).
SHA-256: `56245fd5087db4d3fc2952574ecd4485189bdeb0a0ec3536ba555afc27460eb4`.
The dashboard was rebuilt into ignored `dist/`; Git source transfer alone does
not deliver those assets.

The additive `enrollment_requests` database migration is REQUIRED on the Pi;
use the stopped-service command above with the actual database/config paths
and service account. Its execution and verification are NOT RUN. Raspberry Pi
BlueZ/D-Bus policy, camera/actuator behavior, four-client controller capacity,
MTU23/517 throughput, TLS latency, real disconnect recovery and UI acceptance
are NOT RUN. Compilation does not establish those hardware results.
