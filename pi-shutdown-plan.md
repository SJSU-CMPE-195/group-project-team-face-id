# Pi Bluetooth host shutdown

## Problem and evidence

The Pi host reportedly stays running after Ctrl+C or SIGTERM, with no new
terminal output. At baseline `a063ceb`, the Python signal handler only raises
`KeyboardInterrupt`; it never explicitly quits the GLib loop. PyGObject can
handle an exception inside a callback without exiting that loop. Its signal
wakeup support does not make a custom handler stop the loop automatically.
See the [GNOME maintainer discussion](https://bugzilla.gnome.org/show_bug.cgi?id=622084)
and [PyGObject main-loop examples](https://gitlab.gnome.org/GNOME/pygobject/-/blob/main/tests/test_mainloop.py).

The baseline stopping message appears after nested resource cleanup, so silence
alone does not establish where this particular Pi is stuck. There is no live Pi
trace or verified PyGObject version. Ctrl+Z suspends the process and therefore
retains its database lock; it is not a shutdown command.

## Approach and ownership

1. `wireless/ble_gateway.py` — Pi executor: add an idempotent shutdown request
   that queues GLib loop termination. Keep a persistent request flag so a signal
   just before `run()` cannot be lost. Finish an outstanding registration reply
   or its existing timeout before exiting; skip further registration and ready
   publication when stopping. Remove pending idle sources before the existing
   bounded unregister loop. Clean partially initialized resources when startup
   raises `KeyboardInterrupt` as well as an ordinary exception.
2. `bass_wireless.py` — root: keep Python signal-handler ownership and restoration.
   Route SIGINT and SIGTERM to the active Pi gateway through its entire context
   cleanup. Startup and PC mode retain their exception-based exit. Ignore repeat
   shutdown requests while cleanup runs. Print progress before resource teardown
   rather than only after it, so any remaining Pi stall has a visible stage.
3. `docs/android-wireless.md`, this plan — root: document Ctrl+C/SIGTERM shutdown,
   Ctrl+Z suspension, and the manual acceptance boundary; review the combined
   diff and publish the focused correction on the existing delivery branch.

Keep BLE framing, identity, credentials, database contents, and hardware control
unchanged. No dependency or database migration is needed. Serial output and
Cheroot worker waits can still delay shutdown if underlying work is stuck; that
is a separate, unconfirmed failure path and is not grounds for changing motor
control or replacing the HTTP server in this correction.

## Verification

Baseline on 2026-10-09 at `a063ceb`: project Python
`-m compileall -q bass_wireless.py wireless car_face_auth/src` passed with no
diagnostics. Save baseline and final output in the task scratch directory and
compare them; review `git diff --check` and the combined signal/cleanup paths.

Do not run tests, browser automation, or signal a live host. Pi Ctrl+C, SIGTERM,
service stop, database-lock release, and restart are manual acceptance checks.
Compilation does not establish those outcomes.

Implementation checks on 2026-10-09:

| Check | Baseline | Final |
| --- | --- | --- |
| Python `compileall` for host, wireless, and runtime modules | PASS | PASS; identical empty diagnostics |
| Diff whitespace check | Clean | PASS |
| Source review of signal, registration, and cleanup paths | Existing gap identified | Completed before publication |
| Pi Ctrl+C, SIGTERM, service stop, lock release, and restart | NOT RUN | NOT RUN |

The gateway also checks that a shutdown cannot leave its registration marked
pending when entering cleanup. No functional tests, browser automation, remote
host commands, database changes, dependency installs, or service changes ran.
