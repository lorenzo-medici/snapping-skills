# Merged-/usr Detection and glibc Compatibility

Always run both checks **before Phase 2** (binary analysis delegation). They
identify structural issues that cause hard-to-debug runtime crashes if missed.

---

## 1c.1 — Merged-/usr detection

Modern Debian/Ubuntu images (Bookworm+, Ubuntu 24.04+) use a merged `/usr`
layout where `/bin`, `/lib`, and `/sbin` are symlinks to their `usr/`
counterparts.

```bash
[ -L rootfs/bin ] && echo "merged-/usr image" || echo "split-/usr image"
```

**If merged `/usr`:**
- `snap pack` does NOT follow symlinks for `command:` validation. Commands
  in `snapcraft.yaml` must use the `usr/bin/` path (e.g. `command: usr/bin/library_wrapper.sh`),
  not `bin/library_wrapper.sh`.
- `create_wrapper.sh` detects this automatically and places the wrapper at
  `usr/bin/library_wrapper.sh`. No manual change needed if using the template.
- Watch for `stage collision` build errors where a part installs into `bin/`
  but the `bin → usr/bin` symlink is already in the stage directory.
- **`env-exporter-bash` part staging collision:** The `env-exporter-bash` part
  must stage its script to `usr/bin/env-exporter.sh` (not `bin/env-exporter.sh`)
  and the `command-chain:` must reference `usr/bin/env-exporter.sh`. On a
  merged-/usr image, `bin/` is a symlink staged by the `oci-container` part;
  staging a file into `bin/` from a different part creates a type conflict
  (symlink vs directory) that fails the build with:
  > `Parts 'oci-container' and 'env-exporter-bash' list the following files,
  > but with different contents or permissions: bin`

  The `docker-to-snap` template already uses `usr/bin/env-exporter.sh`; verify
  the generated `snapcraft.yaml` uses this path if you customise the template.

---

## 1c.2 — glibc version compatibility check

When the OCI image's glibc version differs from the base snap's glibc version,
a subtle but fatal issue arises: **snapcraft automatically injects
`LD_LIBRARY_PATH` into `meta/snap.yaml`** pointing at the OCI image's libraries,
even when you never set it in `environment:`.

**Why this is dangerous:** The base snap shells (`/bin/sh`, `/bin/bash` from
core26/core24) run under this injected `LD_LIBRARY_PATH`. Everything that uses
these shells will crash with `GLIBC_X.Y not found` (SIGSEGV):
- Install/post-refresh/remove/configure **hooks** — `snapd` runs them with the
  base snap's `/bin/sh`, which inherits the injected path.
- **command-chain scripts** such as `env-exporter.sh` that have a `#!/bin/bash`
  shebang — also run under the base snap's bash.
- C binaries that call `popen()` or `system()` — these fork the base snap's
  `/bin/sh` at runtime.

**This means:** a snap using the `docker-to-snap` template with a glibc-mismatched
OCI image will fail at install time (`snap install`) with a confusing error:
> `run hook "install": /bin/sh: /snap/.../lib/x86_64-linux-gnu/libc.so.6: version 'GLIBC_2.XX' not found`

```bash
# Check OCI image glibc version
strings rootfs/lib/x86_64-linux-gnu/libc.so.6 2>/dev/null \
  | grep -oP 'GLIBC_\K[0-9]+\.[0-9]+' | sort -V | tail -1

# Check host / core26 base glibc version
strings /lib/x86_64-linux-gnu/libc.so.6 2>/dev/null \
  | grep -oP 'GLIBC_\K[0-9]+\.[0-9]+' | sort -V | tail -1
```

**If versions differ — two-part fix:**

1. **Neutralise the auto-injected `LD_LIBRARY_PATH`** by explicitly setting it
   to empty in the global `environment:` block of `snapcraft.yaml`:
   ```yaml
   environment:
     LD_LIBRARY_PATH: ""
     env_alias: entrypoint
   ```
   The `docker-to-snap` generator detects a glibc mismatch and emits this line
   automatically. Do not remove it. Do not set any other value for
   `LD_LIBRARY_PATH` in global or per-app `environment:` blocks.

2. **Embed RPATH into all ELF executables** using the `embed_rpath.sh` build
   step, so the OCI app can find its own libraries without `LD_LIBRARY_PATH`.
   This step is included in the template's `override-build:` after
   `patch_interpreter.sh`. See `references/override-steps-guide.md §2` for the
   ET_EXEC-only RPATH rule.

Together, (1) prevents the base-snap shell crash and (2) ensures the OCI
application still finds its libraries.

The `docker-to-snap` script runs the glibc check automatically, prints a warning,
and injects `LD_LIBRARY_PATH: ""` into the generated `snapcraft.yaml` when a
mismatch is detected.
