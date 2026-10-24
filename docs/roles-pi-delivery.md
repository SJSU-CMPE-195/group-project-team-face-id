# Roles and host-camera delivery

Snapshot: October 8, 2026 (America/Los_Angeles), prepared for Git publication.
Source: the integrated local checkout, saved compiler/build/lint output and
previous local migration/installation records. The publication base is
`origin/main` at `7a7ba4d2858fcca612ca3e4587408bde6c68a502`.

The two upstream commits are retained, including wiring/CAN documentation,
serial motor control and the face-engine threshold change. CAN remains a design
proposal. This delivery does not deploy to Pi or confirm physical motor movement.

## Current source amendment: October 22, 2026

The current checkout supersedes the October 8 directory and enrollment policy
described in the historical snapshot below:

- Every authenticated account sees the full active account directory, including
  name, Owner/Administrator/User role, face-enrollment state and face-access
  state. Read access does not grant write access; existing role and Owner target
  protections still govern account management.
- An ordinary user can start or replace host-camera enrollment only for their
  own account after a fresh PIN check. Administrators retain their permitted
  target scope. Phone/browser image enrollment remains retired with HTTP 410.
- A blocked account may replace its template without restoring access and still
  cannot start a scan. During another authorized, actor-bound scan, the host can
  identify a blocked enrolled face by name and deny access before actuation.
- Log responses keep `id`, `ts`, `type`, `ok` and `detail` and add readable
  `title`, `summary` and `outcome` (`success`, `denied`, `cancelled`, `error` or
  `info`). Stored diagnostic detail and the existing log visibility scopes are
  unchanged.

These changes add no dependency or database migration. Browser automation,
Pi deployment, manual authenticated phone/Pi flows and hardware acceptance
are **NOT RUN** for this amendment. The October 8 compiler, APK and runtime evidence
applies only to the earlier delivery and must not be used as acceptance of the
October 22 changes. Current local automated results are recorded below.

October 22 local checks, compared with the pre-change checkout:

| Check | Before | After |
| --- | --- | --- |
| Project-local Vite build | PASS | PASS |
| Project-local ESLint | PASS | PASS |
| Wireless-environment Python compileall | PASS | PASS |
| Android compileDebugKotlin / assembleDebug | PASS | PASS |
| Android lintDebug | 0 errors, 19 warnings | Same 19 warnings |

Evidence is saved locally under `.cache/team-feedback/`. Vite retains the same
outdated Browserslist-data notice. The final log-title copy adjustment was
rebuilt and checked with targeted ESLint after the full lint pass. Existing
test assertions/fixtures were updated for the new policies and receipt binding.

### October 22 automated test follow-up

Source: the local uncommitted checkout based on `main` at `ac38bc0`, after the
user requested the reported scenarios be tested. Tests used temporary SQLite
databases, fake camera/actuator interfaces and local test servers. No production
code fix was needed by this run.

| Check | Command | Result |
| --- | --- | --- |
| Full Python suite | `.venv-wireless/Scripts/python.exe -B -m unittest discover -s tests -v` | PASS: 249 tests, 48.614 s, no failures or skips |
| Web enrollment/scan state helpers | `node --test .cache/team-feedback/scan-state.test.mjs` | PASS: 4 tests |
| PWA shell and API caching boundary | `node scripts/check-pwa.mjs` | PASS |
| Python compiler comparison | `compileall` over product Python modules and `tests` | PASS before/after, no compiler output |
| Diff whitespace | `git diff --check` | PASS |

The Python total includes 17 new regression cases; targeted runs are subsets
and must not be added to that total. The existing directory test now checks
Owner, Administrator and User viewers against the same three-role directory.

| Reported behavior | Observed automated coverage |
| --- | --- |
| Enrollment appears as scanning | Web helpers preserve enrollment kind/sample progress and use enrollment labels; runtime rejects scan/enrollment conflicts in both directions. Rendered UI is not tested. |
| Blocked faces should be named and denied | Blocked templates remain available for identification; confirmed blocked matches return the name and denial, retain the recognized audit subject and send no unlock command. Blocked actors cannot initiate scans. |
| Phone can start own enrollment | A paired ordinary bearer obtains a fresh PIN grant, starts host enrollment and queries its receipt through the real Flask routes. The physical phone and BLE transport are not exercised. |
| Complete user directory and roles | All three roles receive the full active directory and correct Owner/admin flags, enrollment and access fields. |
| Readable logs and safe self-enrollment | Event projection, legacy fields, malformed detail, raw-detail suppression and role visibility pass. Self replacement, blocked access preservation, target rejection, cancellation, revocation and receipt replay pass. |

Evidence is in `.cache/team-feedback/full-python-tests.txt`,
`scan-state-tests.txt`, `tests-pwa.txt` and the corresponding targeted test logs.
The full suite emitted third-party Cheroot/PyOpenSSL deprecation warnings;
these did not fail the run. New test fixture mistakes were corrected before
the full run; no production behavior was weakened to satisfy assertions.

This is local software evidence. Live camera recognition, Android rendering,
BLE reconnection, physical lock/ignition response and user acceptance remain
unverified. The previous compiler/build/lint results remain applicable because
the test follow-up changed only tests and documentation.

### October 23 publication validation snapshot

Source: the isolated publication candidate based on `main` at `ac38bc0`, with
only the team-feedback repair applied. USB and emulator work are excluded. Local
evidence is under `.cache/publish-team-feedback-20261023/`; it is private
validation evidence rather than a repository artifact.

| Check | Result |
| --- | --- |
| Python compileall comparison | PASS before/after, no compiler output |
| Full Python suite | PASS: 249 tests, 62.479 s |
| Project-local Vite build | PASS before/after |
| Project-local ESLint | PASS before/after |
| PWA shell and API caching boundary | PASS |
| Web enrollment/scan state helpers | PASS: 4 tests |
| Android `assembleDebug` / `lintDebug` | PASS: BUILD SUCCESSFUL in 1m19s, 48 tasks; lint 0 errors and the same 19 warnings as baseline |

Android evidence is in `final-android.txt`, `final-android-lint.xml` and
`android-lint-comparison.json`. The baseline/final issue ID, severity and
message comparison found no differences. No dependency or database migration
was added.
Browser automation, Pi deployment, manual authenticated phone/Pi flows and
physical hardware acceptance are **NOT RUN**.

## October 8 implemented behavior (historical)

- Successful one-use activation QR/name/PIN setup creates the sole Owner without
  a physical button. Exact request retries recover the committed response.
  Owner-transfer endpoints return HTTP 410.
- The Owner can replace or remove the sole regular administrator using a fresh
  PIN grant and `PATCH /api/users/<id>/role`. Database guards enforce the role
  limits. Role changes invalidate old operation grants while retaining pairing.
- Phones expose Status, Users and Logs. Ordinary users see their own records;
  administrators manage non-Owner accounts; the Owner manages all. Regular
  administrators cannot alter the Owner's account, PIN, face, access or phones.
- Log filtering happens before limits. Regular administrators cannot see Owner
  actors or subjects; unattributed legacy logs remain Owner-only. Non-Owner
  responses use safe summaries rather than raw internal detail.
- Account creation saves name/PIN separately from face enrollment. Administrators
  select an existing account and authorize host-camera capture. Cancellation or
  failure retains the account and any previous face template. Uploaded face
  enrollment endpoints return HTTP 410.
- Vehicle controls require the trusted local dashboard. Production capture uses
  the Pi camera; PC camera and actuators remain labeled development simulations.
  Phone cameras scan QR codes only. Liveness/PAD policy is unchanged.
- Web and Android refresh `/api/me`, clear stale data/actions when identity or
  roles change, and expose paired-phone revocation. Phone pairing is under
  Users; the duplicate web Settings pairing card is removed.
- Owner/admin badges use accessible crown/shield icons. Repeated descriptions
  are removed while actions, errors and security consequences remain visible.
- Power recovery returns to a locked state even when safety settings prevent
  face operations, retaining the readiness error so settings can be repaired.
  Face-operation checks and failed-maintenance recovery restrictions remain.
- The offline factory QR tool and host share a database process lock. Full reset
  reuses verified backups, durable reset receipts and interruption recovery,
  clearing product data while preserving device and TLS identity.

## October 8 verification (historical)

Outputs for publication are under `.cache/push-20261008/`; original implementation
and UI comparisons remain under `.cache/roles-pi-flow/`, `.cache/power-recovery/`
and `.cache/ui-copy-icons/`. These are private local evidence, not repository
artifacts.

| Check | Before upstream integration | Integrated publication tree |
| --- | --- | --- |
| Direct project-local Vite build | PASS | PASS |
| Project-local ESLint | PASS | PASS |
| Python compileall | PASS | PASS |
| Android compileDebugKotlin / assembleDebug / lintDebug | PASS | PASS |

Android lint reports 0 errors and 15 existing warnings. Integration preserved
the local application changes; the only additional tracked inputs are the
upstream face-engine change, upstream motor patch artifact and merged README.

Automated tests and browser automation were not run, per the user's instruction.
Existing GitHub workflows include tests; their outcome is not established by
these local compiler/build checks. Some tests still describe retired client
capture, administrator creation or Owner transfer and have not been updated.
No dependencies were added.

The October 1 debug APK was installed and launched on the connected Pixel 7.
Installation and launch do not establish visual or end-to-end acceptance. The
October 2 PC startup returned dashboard HTTP 200 and `ok: true` from `/health`;
this is a dated observation, not a claim that the host is currently running.

## Database migration and Pi deployment

**A database migration is required.** The local PC additive migration completed
on September 30 with the service stopped. The authorized full backup/reset and
private activation QR export also completed. Database integrity, foreign keys,
backup manifests, reset completion and preserved identity hashes were checked.
Exact local backup paths and receipts remain in the private maintenance records.
Git publication does not repeat that reset or apply a migration to another host.

Pi deployment and migration are **NOT RUN**. Back up the matching database,
`device.json`, `device.tls.pem` and `device.auth.key`, then stop every writer.
From the Pi repository, using its actual existing paths:

```bash
sudo systemctl stop faceid-wireless.service
FACEID_DB_PATH=/home/pi/faceid/faceid.db \
BASS_DEVICE_CONFIG=/home/pi/faceid/device.json \
  .venv-wireless/bin/python bass_wireless.py --mode pi --migrate-security-only
```

Inspect the resulting schema and role state before restarting. A legacy database
is not assigned an Owner automatically; generating new activation credentials
for an activated product requires a separately authorized full reset. Build or
copy the updated web `dist/` before deployment because Git does not track it.
See [the Pi handoff](android-wireless.md#pi-handoff) for the deployment workflow.

## Remaining acceptance

Manual acceptance is **NOT RUN** for first/reused activation QR, administrator
replacement, all three data-visibility scopes, current-phone revocation, delayed
enrollment, cancellation/retry, denied phone vehicle calls and power recovery.

Pi enrollment/unlock acceptance depends on the teammate's liveness work;
`PiRuntime.liveness_available` is still false. Serial commands are implemented,
but `physical_state_confirmed` remains false and physical actuator feedback is
unavailable. Camera, liveness, lock and motor acceptance require the actual Pi
and hardware. Local builds, API availability and PC simulation do not establish
those results.
