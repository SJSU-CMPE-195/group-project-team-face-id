# Implementation 2 repository preparation

## Problem and scope

Prepare the repository deliverables in the Implementation 2 assignment dated
October 25, 2026. The video and Canvas submission are outside this change.
Publish the assignment deliverables with software verification and the `impl2-v1`
tag. Preserve unrelated working-tree changes. Do not flash a board or change the
installed host/database.

The current checkout already contains the host, dashboard, Android client,
firmware, simulated Device API, and automated checks. The missing work is a
grader entry point, complete README sections, configuration examples, and a
reproducible firmware build. Hardware results must remain separate from
software compilation and scripted simulation.

## References and approach

- [GitHub README guidance](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-readmes):
  make setup and usage discoverable and use repository-relative documentation
  links.
- [Espressif Blink example](https://github.com/espressif/esp-idf/blob/master/examples/get-started/blink/README.md):
  explicitly distinguish required hardware, configuration, build, and observed
  output. Keep this project's existing Arduino firmware toolchain.
- Use the assignment's `impl.yaml` schema and existing CI commands rather than
  introduce another application or test framework.

## Steps

1. **Files: `README.md`, `impl.yaml`.** Add the eleven required README sections,
   a specific implementation claim, setup/build/test/run commands, and an
   explicitly limited hardware-free route. Preserve existing operating notes
   and identify stale firmware references from current source.
2. **Files: `.env.example`, `.gitignore`.** Describe supported configuration
   without secrets; exclude local runtime identities, databases, credentials,
   and development artifacts while keeping the example trackable.
3. **Files: `mock_pi_device_api.py`, `README.md`.** Give the standalone fixture
   its own `.cache/simulated_faceid.db`; the old default collides with the real
   PC host database. Document explicit liveness configuration for scripted
   frames, and disable account lockout only in this unauthenticated fixture.
   Keep the normal host's safety defaults and existing data unchanged.
4. **Files: `ESP32_Program/ESP32_Program.ino`,
   `ESP32_Program/src/Step_Motor_Lock.ino`, `ESP32_Program/sketch.yaml`,
   `ESP32_Program/ReadMe.md`.** Move the unchanged sketch to Arduino's required
   primary filename and retain the original include path for the C++ harness.
   Compare original/final source content, and add a pinned compile-only profile
   for the already-used platform and libraries. Keep downloaded build tools and
   caches in scratch space. Do not flash or modify firmware behavior.
5. **Files: `docs/implementation-2-verification.md`, `README.md`.** Record current
   compilation results, source revision, repository-only checks, and remaining
   release/manual steps. Record video as pending rather than invent a URL.
6. **Files: the assignment deliverables above.** Validate an isolated snapshot
   containing the base commit and only these changes. Run the manifest's setup,
   build and existing tests in Linux, the documented mock HTTP flow, and an
   Android debug build. Record failures and verification boundaries, push the
   selected changes, then publish `impl2-v1` on the final submission commit.

## Verification

- Before edits: project-local `vite build` and Python `compileall`, with output
  under the task's temporary scratch directory. Both passed on the initial
  working tree. Vite reported old Browserslist metadata; no dependency update is
  part of this task.
- After edits: compare applicable compiler output to that baseline, validate
  manifest types/paths, all eleven README headings, environment coverage,
  relative links, ignore rules, and the final diff.
- The initial baseline used Vite directly. The authorized completion now also
  runs `npm run build` (including the PWA check) and the existing Python suite.
- Build and lint the separate assignment-only Android source snapshot.
- Do not run browser automation or claim phone/Pi/ESP32 acceptance from software
  checks. Report clean-container results and remote publication only after they
  are observed.

## Expected result

A published assignment snapshot with complete grading metadata, reproducible
commands, recorded software checks and the `impl2-v1` tag. Video and Canvas
submission remain team actions outside this change.
