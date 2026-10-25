# Implementation 2 verification

Snapshot: October 25, 2026 (America/Los_Angeles).
Graded ref: [impl2-v1](https://github.com/SJSU-CMPE-195/group-project-team-face-id/tree/impl2-v1).

Software checks used commit `78773cd558c8b408781f8d3834e1f45d8ea788b3`,
which contains the twelve selected assignment files on top of `66c3d30`.
The final submission adds documentation corrections and these results;
application source, dependencies and executable `impl.yaml` commands are unchanged.
Unrelated local changes are excluded.

## Delivered

- [README](../README.md): all eleven required sections, concise setup and usage,
  team attribution, implementation limits and the mock walkthrough.
- [impl.yaml](../impl.yaml): setup, build, test, run and mock commands.
- [.env.example](../.env.example) and [.gitignore](../.gitignore): supported
  configuration names and exclusions for local state and private material.
- [Firmware profile](../ESP32_Program/sketch.yaml): pinned ESP32 platform and
  libraries, with a checksum-verified Arduino CLI download.
- [Hardware reference](hardware-reference.md): detailed wiring, planned CAN work
  and troubleshooting outside the shorter README.

The standalone mock uses `.cache/simulated_faceid.db`, separate from the normal
PC host. No existing database was migrated, reset or deleted. The firmware moved
to Arduino's required primary filename without content changes: the old and new
source have the same Git blob, `dc814ab07cfaaa46697930fe9a61af12649a0e1f`.
The old path remains a compatibility include for the existing C++ harness.

## Verification results

| Check | Result | Observed scope |
|---|---|---|
| Baseline and final frontend/Python compilers | PASS | No new diagnostics; local Vite output assets unchanged |
| README, manifest, paths and ignore rules | PASS | Eleven headings; YAML fields/types; README links/anchors; intended twelve-file publication scope |
| Linux dependency setup | PASS | All six `impl.yaml` setup commands; clean dependency installation and verified CLI checksum |
| Linux frontend build and lint | PASS | `npm ci`, `npm run build` and `npm run lint`; PWA check passed |
| Linux Python compilation and existing suite | PASS | `compileall`; 270 tests, 0 failures, 0 skips |
| Native ESP32 compilation | PASS | Arduino CLI 1.5.1, ESP32 core 3.3.12, AccelStepper 1.64.0, LiquidCrystal 1.0.7; no flashing |
| Android build and lint | PASS | Isolated submission snapshot, `assembleDebug lintDebug`; no device run |
| Fresh-database mock HTTP walkthrough | PASS | Demo Driver granted; UNLOCK followed by auto-relock; unknown face timed out without another command |
| GitHub CI for the software revision | PASS | Frontend lint/build, Python unit suite and Android build/lint |
| Real Pi/phone/BLE/camera/ESP32 journey | NOT RUN | Hardware and user acceptance remain separate |
| Production deployment | NOT RUN | Repository publication only |

The Linux container used Node 22.23.1, Python 3.11.17 and g++ 12.2.0.
The corrected LF snapshot produced:

```text
python -B -m unittest discover -s tests -v
Ran 270 tests in 41.546s
OK
```

An initial Windows-exported validation archive converted shell scripts to CRLF,
causing one legacy-installer test to fail. Re-exporting the published commit with
LF line endings resolved it; the repository source needed no fix. The complete
suite above passed on that corrected snapshot.

The fresh mock walkthrough also exposed a missing documented fixture setting.
It needs both `liveness: false` and `failLockout: false`: scripted frames cannot
prove liveness, and this standalone fixture has no account authorization policy.
README now includes both. Normal host defaults and account lockout are unchanged.

The corrected walkthrough was replayed from a fresh database. Its successful
scan returned `state: granted`, `command_sent: true` and six matching frames.
Commands changed from `[STOP, LOCK, UNLOCK]` to
`[STOP, LOCK, UNLOCK, STOP, LOCK]` after the ten-second relock timer. An unknown
face then timed out with zero matches, `command_sent: false` and no new command.
The simulated status remained locked with ignition off.

The Linux native firmware build used 300,675 bytes of flash (22%) and 22,568
bytes of globals (6%). Windows baseline and pinned-profile builds also passed.

The [successful CI run](https://github.com/SJSU-CMPE-195/group-project-team-face-id/actions/runs/40206356972)
checks the software revision above on GitHub's Linux runners, including the same
270 Python tests. No browser automation or hardware acceptance was performed.

## Remaining limits

- `npm ci` reported eight existing dependency audit findings (three moderate,
  five high). Dependency upgrades were not part of this submission.
- Vite reported old Browserslist metadata. Android reported non-blocking native
  symbol-stripping and deprecated BLE API warnings.
- A bounded current-tree scan found no tracked runtime databases, device
  identities, private signing material or matching common secret patterns.
  This was not a full Git-history or security audit.
- Simulated recognition and controller commands do not establish physical motor
  motion, position, anti-spoofing or reliable wireless behavior.

## Submission status

- [x] Complete source, eleven-section README and grading manifest included.
- [x] Environment example, ignore rules and reproducible firmware build included.
- [x] Assignment-only source selected and published to `main`.
- [x] Final submission identified by `impl2-v1`.
- [ ] Team supplies the public 3-5 minute demo video; README and `impl.yaml` keep
  the URL visibly pending.
- [ ] Team submits the repository and video links on Canvas.

Repository: [SJSU-CMPE-195/group-project-team-face-id](https://github.com/SJSU-CMPE-195/group-project-team-face-id).
The video and Canvas submission are outside this repository task.
