---
name: snap-iteration-workflow
description: >
  Guides through the full snap packaging iteration cycle: LXD environment setup,
  building with snapcraft --use-lxd --build-for <arch>, installing and running inside
  an LXD container, and verifying through devmode and strict confinement. Enforces a
  hard rule: the host must NEVER be used for building or testing. LXD is required for builds; runtime
  validation must use an architecture-compatible isolated environment such as native
  LXD, an LXD remote, QEMU/binfmt smoke tests, or image-garden/full-system emulation
  with snapd. Covers snappy-debug, ELF interpreter verification, install hook testing,
  interface connection, and snap try.
  WHEN: snap packaging, iterate snap build, snap build loop, snapcraft iteration,
  snap install and test, snap confinement debugging, snappy-debug, snap devmode to strict,
  snap try prime, test snap locally, snap runtime verification, snap build and run,
  snap install verify, snap iteration workflow.
license: "Apache-2.0"
metadata:
  author: "Canonical"
  version: "2.0.7"
  summary: "End-to-end snap packaging iteration: build → install → run → verify → confinement hardening, with environment isolation enforcement."
  tags:
    - snap
    - snapcraft
    - packaging
    - confinement
    - lxd
---

# Snap Iteration Workflow

## Overview

This skill guides through the repeatable snap packaging iteration cycle — from setting
up an LXD build environment, to building, installing inside an LXD container, running,
and verifying a snap, through to confinement hardening. It enforces a hard rule:
**the host must NEVER be used for building or testing — LXD is required for builds,
and runtime validation must happen in an architecture-compatible isolated environment.**

When the project contains a `rootfs/` directory extracted from a container image,
treat that `rootfs/` as a read-only source artifact throughout the iteration
cycle. Never edit, patch, chmod, delete from, or otherwise write to the extracted
`rootfs/` directly; encode required changes in `snapcraft.yaml` override steps,
wrapper scripts, layouts, or prime/build-stage mutations instead.

---

## ⚠️ Isolation Rule (Enforce Before Proceeding)

**The host MUST NEVER be used for building or testing — not even for one of the two phases.**

Check whether LXD is available:

```bash
lxd version 2>/dev/null && echo "LXD available" || echo "LXD NOT available"
```

LXD is the **only** supported build backend. Every build command MUST include
`snapcraft --use-lxd --build-for <target_arch>`, even for native builds. Omitting
`--build-for` can build for all architectures, which is incorrect when producing
a snap for a known target. Use `qemu-user-static` only when `<target_arch>` differs
from the host architecture. Runtime testing must then use an isolated environment
that can execute the snap's target architecture: native matching-architecture LXD,
an LXD remote on target hardware, full-system emulation such as image-garden with
snapd running inside the emulated target, or a clearly scoped QEMU/binfmt smoke test.

**If LXD is not installed**, attempt to set it up before doing anything else:

```bash
sudo snap install lxd
sudo lxd init --auto
sudo usermod -aG lxd $USER
newgrp lxd   # or log out and back in for group membership to take effect
lxd version  # confirm setup succeeded
```

**If LXD setup fails or is not possible in this environment:**
1. Report the error clearly:
   > "LXD is required for all snap build and test phases. The host must not be used.
   > Please install and initialise LXD (`sudo snap install lxd && sudo lxd init --auto`),
   > then re-run. If LXD cannot be installed in this environment, this workflow cannot proceed."
2. **Stop. Do not proceed.**

---

## Workflow

The iteration cycle has five phases. Follow them in order; loop back to Phase 2
for each build-test cycle.

```
Phase 1: Environment Setup  (once per project)
Phase 2: Build              (each iteration)
Phase 3: Install & Run      (each iteration)
Phase 4: Verify & Harden    (each iteration, until strict confinement passes)
Phase 5: Reproducibility    (final validation for container-rootfs projects)
```

---

## Phase 1: Environment Setup

Read `references/build-environments.md` for full details on the LXD build environment.

### Set up and select build environment

LXD is the **only** supported build environment.

1. **Check LXD availability:**
   ```bash
   lxd version 2>/dev/null && echo "LXD available" || echo "LXD NOT available"
   ```

2. **If LXD is not available**, set it up now (see the isolation rule above for the
   full setup procedure and the STOP condition if setup fails).

3. **Determine `target_arch` before building.** Prefer the value passed by the
   caller (for OCI conversions, this comes from container metadata). If no caller
   value exists, derive it from `snapcraft.yaml` `architectures`, the snap filename
   being rebuilt, or explicit user/project requirements. If it cannot be determined,
   stop and ask; do not run `snapcraft`.

4. **For cross-architecture builds** (e.g. building arm64 on an amd64 host), install
   QEMU user-static emulation:
   ```bash
   sudo snap install qemu-user-static
   sudo systemctl restart systemd-binfmt
   ```
   Then build with `snapcraft --use-lxd --build-for <target_arch>`.

5. For same-architecture builds, still use `snapcraft --use-lxd --build-for <target_arch>`.

`snapcraft --destructive-mode` (host build) is **never** acceptable regardless of the
test environment. If LXD is unavailable and cannot be set up, stop here.

### Select architecture-compatible test environment

Read `references/install-and-verify.md` → "Where to Install (Test Environments)".

1. If the snap architecture matches the local host, use a local LXD container with
   `security.nesting=true`.
2. If the snap architecture differs from the host, use one of:
   - an LXD remote on target-architecture hardware,
   - a full-system emulator such as image-garden that boots a target-architecture
     system with snapd,
   - QEMU/binfmt LXD smoke testing for early checks only.
3. If none of these are available, stop and report that runtime validation cannot
   proceed for this architecture.

The host snapd is **never** an acceptable test environment. Do not install or test
the snap directly on the host under any circumstances.

---

## Phase 2: Build

Read `references/build-environments.md` for environment-specific commands.

**Standard build (LXD, always architecture-scoped):**
```bash
snapcraft --use-lxd --build-for <target_arch>
```

**Minimise rebuild time (reset only the changed part):**
```bash
snapcraft clean <part-name> --use-lxd --build-for <target_arch>
snapcraft --use-lxd --build-for <target_arch>
```

**Mandatory when part-run scripts change:** If you create or edit any script
that a part executes (for example a script called from `override-build:` or
`override-prime:`), run the selective clean above for that part before rebuilding.
Otherwise Snapcraft can reuse the old staged script from the part cache.

**Do not run `snapcraft clean` without a part name** unless a full reset is required —
it destroys the LXD container and all cached packages.

**Interactive debugging inside the build container:**
```bash
snapcraft --use-lxd --build-for <target_arch> --shell-after   # drops into container after build scripts run
```

When the build produces a `.snap` file, proceed to Phase 3.

---

## Phase 3: Install & Run

When working with LXD for testing, volumes should not be used, only `lxc push` and `lxc pull`.

Read `references/install-and-verify.md` for full installation and environment setup commands.

### First run: devmode

Always start with devmode. It bypasses confinement, confirming application correctness
independently from confinement issues.

```bash
snap install --dangerous --devmode myapp_1.0_amd64.snap
snap logs -f myapp.entrypoint
```

**Pass criteria:** Service starts, application runs, no crashes in logs.

**If service crashes immediately:** Check ELF interpreter before debugging confinement.
See `references/install-and-verify.md` → "ELF interpreter crash pattern".

### Fast iteration without rebuilding

After the first successful build, use `snap try prime/` to iterate on runtime
behaviour without repacking:

```bash
snap try prime/
snap restart myapp
snap logs -f myapp.entrypoint
```

Modify files in `prime/` directly (wrapper scripts, environment files) and restart.
Do not modify an extracted container `rootfs/` directly; keep it read-only and
make reproducible fixes through snapcraft overrides or wrapper changes.
Note: install and remove hooks do **not** run with `snap try`.
If the application is not "daemonized", the restart logic should be removed from the hooks.

### Test the refresh path

```bash
snap refresh --dangerous myapp_1.1_amd64.snap
```

This project implements `post-refresh` differently from `install`. Always test
the refresh path in addition to fresh install.

---

## Phase 4: Verify & Harden

Classic confinement should never be used. Always aim for strict confinement.

Read `references/install-and-verify.md` → "Confinement Iteration Workflow" and
"Runtime Verification" for full commands.

### Runtime checks

```bash
snap services myapp                    # daemon running?
snap logs myapp.entrypoint             # any crash or exec errors?
grep myservice /etc/hosts              # install hook wrote the entry?
snap connections myapp                 # expected plugs connected?
snap run --shell myapp.entrypoint      # inspect snap environment interactively
```

Inside the snap shell, verify:
- `echo $LD_LIBRARY_PATH` — library paths from `create_wrapper.sh`
- `ls $SNAP/usr/lib/myapp-ld-linux` — interpreter symlink created by `patch_interpreter.sh`
- `env | grep MY_VAR` — variables injected by `env-exporter.sh` / `snappy-env`

### Confinement hardening loop

1. Install in strict mode (no `--devmode`):
   ```bash
   snap remove myapp && snap install --dangerous myapp_1.0_amd64.snap
   ```
2. Run `snappy-debug` in one terminal, exercise the app in another:
   ```bash
   snap install snappy-debug
   sudo journalctl --output=short --follow --all | sudo snappy-debug
   ```
3. Add suggested plugs or layouts to `snapcraft.yaml`.
4. Rebuild (Phase 2) and reinstall (Phase 3).
5. Repeat until `snappy-debug` shows no denials during normal operation.

For layout and plug guidance, see `references/install-and-verify.md` →
"Handle layouts for hardcoded paths" and "Identify store-review-only interfaces early".

### Quick verification checklist

Read `references/install-and-verify.md` → "Quick Verification Checklist" and
work through each item before declaring a build complete.

---

## Phase 5: Final Rootfs Reproducibility Validation

Run this phase only for projects built from a container-extracted `rootfs/`.
Run it after the snap first runs successfully under strict confinement with no
expected denials. This is the final validation gate before declaring the snap
recipe complete.

1. **Re-extract the original container filesystem as `rootfs_original/`.** Use the
   same container image, tarball, and extraction options that created the working
   project. If `snap-oci-container` invoked this workflow, follow its final
   validation phase for the exact extraction command.

2. **Compare the clean extraction against the working `rootfs/`:**
   ```bash
   diff -rq rootfs_original/ rootfs/
   ```

3. **If the directories differ, encode every delta in the recipe.** Convert each
   added, removed, modified, permission-changed, or symlink-changed path into
   `snapcraft.yaml` `override-build:` or `override-prime:` steps. Use the
   project conventions or the `snap-oci-container` `references/override-steps-guide.md`
   catalog when available. Do not continue until every local `rootfs/` change is
   represented in `snapcraft.yaml`.

4. **Swap back to a clean rootfs for the proof build:**
   ```bash
   mv rootfs rootfs_edited
   mv rootfs_original rootfs
   ```

5. **Rebuild from the clean rootfs and retest strict confinement:**
   ```bash
   snapcraft clean <part-name> --use-lxd --build-for <target_arch>
   snapcraft --use-lxd --build-for <target_arch>
   ```
   Install the rebuilt snap in the isolated test environment and repeat Phase 4
   strict-confinement verification. The final pass condition is: the snap built
   from the newly extracted `rootfs/` runs successfully under strict confinement
   with no expected denials.

If the rebuilt snap fails, treat the failure as evidence that the recipe is not
yet reproducible. Restore or inspect `rootfs_edited/`, encode the missing change
in `snapcraft.yaml`, re-extract/swap a clean `rootfs/` again, and repeat this
phase until the strict-confined rebuild passes.

---

## References

### references/build-environments.md
Detailed commands for the LXD build environment, QEMU cross-build setup,
and manual LXD container workflow. Includes the build environment selection
decision tree, STOP conditions when LXD is unavailable, and tips for minimising rebuild time.

**Read when:** Selecting or setting up a build environment, or when troubleshooting
build failures.

### references/install-and-verify.md
Installation methods (`--devmode`, strict, `snap try`, `snap refresh`), test environment
setup, runtime verification commands, and the full confinement iteration workflow.

**Read when:** Installing the snap, verifying runtime behaviour, or debugging
confinement denials.

For large reference files, search with:
```bash
grep -n "interpreter" references/install-and-verify.md
grep -n "decision tree" references/build-environments.md
```
