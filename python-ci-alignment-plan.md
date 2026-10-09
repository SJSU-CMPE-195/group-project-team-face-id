# Restore Python CI after the BLE merge

## Problem and scope

The first `main` CI run at `52c58a7` failed in `python-unit`: 231 tests ran,
with 29 failures and 36 errors. Web lint/build and Android build/lint passed.
The failing log includes obsolete API v3 expectations, missing fixture methods,
retired owner-transfer/client-camera calls, and requests that now correctly
require host-local authority or an authenticated enrollment reservation.
The preceding `main` run at `00063ec` also failed, so this repair includes stale
fixtures inherited from that version as well as the BLE/API v4 transition.

Use the existing unittest suite to distinguish stale tests from production
regressions. Preserve the approved BLE/API v4 contracts, host-only vehicle
controls, PIN/role checks, recovery receipts, and hardware safety boundaries.
Do not disable CI, skip failing tests, restore retired features, or weaken
authorization merely to make the suite pass. Any production defect found must
be reviewed separately before changing its owning module.

## Approach and ownership

The existing unittest fixtures already use temporary SQLite databases and
hardware-free runtimes. Keep that structure and update fixtures to implement
the actual interface, consistent with the
[standard-library unittest guidance](https://docs.python.org/3.11/library/unittest.html#organizing-test-code).

1. `tests/test_commissioning.py`, `tests/test_host_commissioning.py`,
   `tests/test_host_lifecycle_failures.py`, `tests/test_owner_protection.py`,
   `tests/test_developer_reset.py` — security executor: align activation,
   recovery, reset, and owner authorization cases. Reuse applicable failure and
   exact-retry cases for the supported recovery flow; explicitly check retired
   transfer endpoints remain unavailable.
2. `tests/test_pi_device_api.py`, `tests/test_runtime_authorization.py`,
   `tests/test_runtime_maintenance.py`, `tests/test_runtime_safety.py` — Pi
   executor: repair shared fake database/runtime signatures and exercise
   enrollment through accepted host-camera requests. Preserve cancellation,
   identity/version, actuation, and no-physical-confirmation assertions.
3. `tests/security_fixtures.py`, `tests/test_wireless_authorization.py`,
   `tests/test_wireless_request_limits.py`, `tests/test_wireless_tls.py`,
   `tests/test_pi_device_api_limits.py`, `tests/test_simulated_pi_api.py` — API
   executor: distinguish local dashboard requests from mobile requests; update
   API v4 enrollment, user creation, limits, and transport expectations. Keep
   invalid input, replay, role, and remote-control rejection coverage.
4. This plan and any confirmed production fix — root: review combined coverage
   and shared fixture compatibility, compare compiler output, run the existing
   Python CI command, and publish the correction to the already merged `main`.
   Observe all jobs at the corrected commit before reporting CI success.

Each test file has one writer. No dependency, schema, service, deployment,
Android, browser automation, or hardware changes are planned.

## Verification

Baseline on 2026-10-09:

- GitHub run `38001224259`, `python-unit`: FAIL (29 failures, 36 errors).
- Existing project Python `compileall`, including tests: PASS, no diagnostics.
- Local equivalent `python -B -m unittest discover -s tests -v`: FAIL, the same
  231 tests, 29 failures, and 36 errors in 80.872 seconds. Output is retained in
  the task scratch directory.

Run each changed area's existing tests, then the full existing Python suite.
Compare final compiler output with its baseline and inspect `git diff --check`.
Keep logs outside product source. GitHub CI remains the Linux runner check;
neither local nor CI test success proves Pi deployment, BLE operation, database
migration, or physical camera/actuator acceptance.

## Local result (2026-10-09)

- Only the 15 test/fixture files listed above changed; production code and CI
  configuration are unchanged.
- Targeted existing tests: PASS, 31 security/recovery + 69 API/runtime + 50
  wireless/limits/simulation tests.
- Full CI unittest command after the Linux fixture correction: PASS, 232 tests
  in 72.521 seconds, no skips.
- Final `compileall`: PASS. Baseline and final logs are both empty and identical.
- Combined diff review and `git diff --check`: PASS. Independent reviews found
  no blockers in local-session authority, member log scope, remote control
  rejection, or durable enrollment authorization coverage.
- GitHub run `38002679632` at `dd37d4a` reduced the Python failure to one Linux
  environment-isolation error. The host commissioning test patched the SQLite
  module path but left the entrypoint's `FACEID_DB_PATH` unset, so the process
  lock tried to create the production `/home/pi/faceid` directory. Its fixture
  now sets both paths to the same temporary database and restores them afterward.
  The next corrective commit still requires an exact-commit CI check.
- Pi/BLE, physical camera/actuator, browser, and deployment acceptance: NOT RUN.
