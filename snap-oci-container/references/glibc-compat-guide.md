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

---

## 1c.2 — glibc version compatibility check

When the OCI image's glibc version differs from the base snap's glibc version,
setting `LD_LIBRARY_PATH` in `snapcraft.yaml` `environment:` blocks is
**dangerous and must be avoided entirely**.

**Why:** The base snap shell (`/bin/sh` from core26/core24) inherits
`LD_LIBRARY_PATH`. When a C binary calls `popen()` or `system()`, it forks the
base snap's `/bin/sh` with the inherited library path. If the OCI image's glibc
is older than the base snap's, the base shell will crash immediately with
`GLIBC_X.Y not found` (SIGSEGV).

```bash
# Check OCI image glibc version
strings rootfs/lib/x86_64-linux-gnu/libc.so.6 2>/dev/null \
  | grep -oP 'GLIBC_\K[0-9]+\.[0-9]+' | sort -V | tail -1

# Check host / core26 base glibc version
strings /lib/x86_64-linux-gnu/libc.so.6 2>/dev/null \
  | grep -oP 'GLIBC_\K[0-9]+\.[0-9]+' | sort -V | tail -1
```

**If versions differ:**
- **Never** add `LD_LIBRARY_PATH` to any `environment:` block in `snapcraft.yaml`
  (not global, not per-app, not in hooks).
- The `embed_rpath.sh` build step embeds RPATH directly into ELF executables,
  making `LD_LIBRARY_PATH` unnecessary. Ensure this step is present in the
  `override-build` section (it is included in the template after `patch_interpreter.sh`).
- See `references/override-steps-guide.md §2` for the ET_EXEC-only RPATH rule.

The `docker-to-snap` script runs this check automatically and prints a warning.
