# Roles and host-camera delivery

Snapshot: October 8, 2026 (America/Los_Angeles), prepared for Git publication.
Source: the integrated local checkout, saved compiler/build/lint output and
previous local migration/installation records. The publication base is
`origin/main` at `7a7ba4d2858fcca612ca3e4587408bde6c68a502`.

The two upstream commits are retained, including wiring/CAN documentation,
serial motor control and the face-engine threshold change. CAN remains a design
proposal. This delivery does not deploy to Pi or confirm physical motor movement.

## Implemented behavior

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

## Verification

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
