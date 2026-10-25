# Implementation 2 repository verification

Snapshot: October 25, 2026 (America/Los_Angeles).
Base commit: `66c3d30dfcde47c150b08369f220b0487221fbee` on local `main`, with
pre-existing Android/BLE/emulator changes. The selected submission contains only
the assignment deliverables. Additional isolated release checks are in progress;
the table below records completed preparation checks until those results arrive.

## Prepared deliverables

- Root [impl.yaml](../impl.yaml) uses the assignment's fields. Both `run` and
  `mock_mode` start the hardware-free fixture; native firmware compilation is
  listed in `build`, while real Pi provisioning/operation is in README.
- [README](../README.md) contains all eleven required sections, setup and run
  commands, configuration names, existing test commands, architecture and limits.
- [.env.example](../.env.example) lists supported optional configuration without
  credentials. It is documentation; the host does not automatically load it.
- [.gitignore](../.gitignore) excludes runtime databases, identities, private
  signing material, local environments and assistant attachment files.
- The standalone mock now uses `.cache/simulated_faceid.db`, distinct from the
  normal PC host's `.cache/mock_faceid.db`. Existing files are not moved, deleted
  or reset. Its walkthrough explicitly configures liveness for scripted faces.
- [Pinned firmware profile](../ESP32_Program/sketch.yaml) and verified local
  Arduino CLI installation commands are included. The full unchanged sketch is
  now [ESP32_Program.ino](../ESP32_Program/ESP32_Program.ino), as required by the
  Arduino primary-sketch naming convention; the old source path remains a
  compatibility include for the existing C++ harness.

## Verification results

| Check | Result | Scope |
|---|---|---|
| Baseline frontend compile | PASS | Project-local Vite 7.3.6; Node 22.14.0; no browser |
| Baseline Python compile | PASS | Python 3.14.3 in the existing local wireless venv; syntax/bytecode only |
| Final frontend/Python compile comparison | PASS | No new diagnostics; frontend assets and output identical apart from elapsed time |
| README headings, YAML structure, relative paths and ignore rules | PASS | 11 required headings, assignment field types, local Markdown links/anchors and 12 ignore examples |
| Native ESP32 compile | PASS | Unchanged-source baseline and final pinned-profile build; no board flashing |
| Existing C++ harness include compatibility | PASS | Translation unit compiled to an object only; harness was not executed |
| Existing automated tests and PWA behavior check | NOT RUN | Test commands documented; not executed in this preparation |
| Clean Linux container dependency setup | NOT RUN | Commands derived from tracked CI; local installed dependencies reused |
| Android APK build/lint | NOT RUN | Android source was not changed by this preparation |
| Mock HTTP walkthrough | NOT RUN | Expected behavior documented from source and existing tests |
| Pi/phone/BLE/camera/motor acceptance | NOT RUN | No hardware or user UI acceptance performed |
| Remote CI/deployment | NOT RUN | No push, workflow run or deployment performed |

The user requested removal of local Android emulator support. Emulator-only files
and branches are being removed; unrelated Android, USB and BLE changes are kept
outside the submission. Private attachment files remain ignored.

A limited current-tree scan found no tracked runtime databases, PEM/key files,
keystores, device identity files or environment files with credentials. Common
private-key/GitHub-token/AWS-access-key patterns produced no matches in tracked
text. This is a bounded repository check, not a full history or security audit.

The frontend compile used the project-local `vite build` directly. The package
script `npm run build` additionally executes `scripts/check-pwa.mjs`, which
includes behavior checks; that extra check was not run here. Python 3.11 remains
the documented grader version, matching the tracked CI configuration.

Baseline frontend compiler output:

```text
vite v7.3.6 building client environment for production...
1772 modules transformed.
dist/index.html                   1.44 kB | gzip:   0.62 kB
dist/assets/index-Bu9rZcHR.css   33.41 kB | gzip:   6.73 kB
dist/assets/index-CA1jkf7J.js   336.30 kB | gzip: 100.68 kB
Build exit: 0
```

Vite also reported that `caniuse-lite` data was seven months old. No dependency
update was performed for that advisory. Python `compileall` returned exit 0
without diagnostics. Compiler logs and temporary bytecode were kept outside
the repository in the task scratch directory.

## Native firmware compiler evidence

Arduino CLI 1.5.1 was downloaded to temporary storage and verified against the
official release checksum. The build used `esp32:esp32` 3.3.12, AccelStepper
1.64.0 and LiquidCrystal 1.0.7. These are the firmware's existing dependencies;
the profile makes their build versions explicit.

The final command used an empty Arduino configuration directory and a temporary
build directory, equivalent to the repository command below:

```bash
arduino-cli compile --profile impl2 ESP32_Program
```

```text
Sketch uses 300803 bytes (22%) of program storage space. Maximum is 1310720 bytes.
Global variables use 22568 bytes (6%) of dynamic memory,
leaving 305112 bytes for local variables. Maximum is 327680 bytes.
Build exit: 0
```

The unchanged-source baseline used 300,787 bytes of flash and 22,568 bytes of
globals. Baseline and final primary source SHA-256 match exactly:
`8341242a35d181a99e6de1e0be4d1c1ed4d94ac3aa438fb36effe04542508831`.
The firmware behavior was not edited. The existing C++ harness translation
unit also compiled with the ESP32 toolchain in C++17 mode; it was not linked or
executed. Neither compile establishes motor motion, physical position, sensor
accuracy or Pi-to-controller communication.

These native builds ran on Windows. The Linux x86_64 archive URL/checksum and
commands are documented in `impl.yaml`, but the complete Linux setup pipeline
was not run in a fresh container.

## Submission status

- [x] Repository source includes the dashboard, Python host, Android app and
  ESP32 firmware.
- [x] README and machine-readable grading instructions prepared locally.
- [x] Environment example and runtime-file ignore rules prepared locally.
- [x] Local `main` history contains author names corresponding to all four
  listed team members (Taanish Patel, Adam, Nick/Nicholas Thi and Greg).
  This checks local history; it does not independently verify account ownership.
- [x] Select/review the assignment-only source changes. Unrelated working-tree
  changes are excluded.
- [ ] Commit the intended submission, then create and publish `impl2-v1`.
  The tag is absent locally and remotely as of the release preparation check.
- [ ] Add the public 3-5 minute video URL to README and `impl.yaml`.
- [ ] Submit the repository link and video link on Canvas.

Repository URL:
[SJSU-CMPE-195/group-project-team-face-id](https://github.com/SJSU-CMPE-195/group-project-team-face-id).
Commit/push and the final tag are authorized and in progress. Video and Canvas
submission remain team actions. The assignment's publication commands are:

```bash
git tag impl2-v1
git push origin HEAD
git push origin impl2-v1
```

Inspect the selected commit and check that the tag is absent locally and remotely
before creating it. Do not move or replace an existing grading tag silently.
The tag includes committed files only; local untracked/modified files are not
part of the grader's checkout until deliberately committed.
