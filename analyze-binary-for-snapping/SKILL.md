---
name: analyze-binary-for-snapping
description: >
  Analyzes Linux executables and runtime traces to infer snap confinement requirements.
  It maps observed syscall behavior, accessed filesystem/device/network paths, and OCI
  capability hints to suggested snap plugs, layout entries, and explicit unmappable-path
  warnings. It can also patch an existing snapcraft.yaml by adding vetted plugs/layouts
  and returns clear next steps for manual fixes and store-review-only permissions.
  WHEN: analyze binary for snap, infer snap interfaces from executable, suggest plugs for
  binary, inspect syscalls for snapping, map accessed paths to snap layouts, identify
  unmappable snap layout paths, patch snapcraft yaml with plugs and layouts, confinement
  guidance for strict snap, determine snap permissions from strace output, troubleshoot
  binary path issues in snap.
license: Apache-2.0
metadata:
  author: Canonical
  version: "1.1.3"
  summary: Analyzes a binary and trace evidence to suggest snap plugs, layouts, unmappable paths, and patch an existing snapcraft.yaml when present.
  tags:
    - snap
    - binary-analysis
    - syscalls
    - interfaces
    - layouts
---

# Analyze Binary For Snapping

## Overview

Use this skill to infer snap confinement requirements from a binary and optional runtime trace.
It produces a concrete migration output: suggested plugs, suggested layouts, unmappable paths, and next steps.

## Inputs

Collect these inputs before analysis:

- **Required:** path to the target binary (inside rootfs or host filesystem)
- **Optional but recommended:** command line to run under `strace` for runtime evidence
- **Optional:** path to `config.json` when available (for capabilities and mount entries)
- **Optional:** explicit app name in `snapcraft.yaml` for patching

If the target binary is inside a `rootfs/` directory extracted from a container
image, treat that `rootfs/` as a read-only input artifact. Never write to,
patch, chmod, delete from, or otherwise mutate the extracted `rootfs/` directly.
Report any required file changes as snapcraft override steps, wrapper changes,
layouts, or prime/build-stage mutations instead.

## Workflow

Follow all phases in order.

### 1. Discover project context

- Check for `snapcraft.yaml` or `snap/snapcraft.yaml`.
- If found, note available app names under `apps:` (for optional patching).
- If not found, still continue and return suggestions only.

### 2. Run static binary inspection

Use the checklist in `references/analysis-checklist.md` (Phase 2):
- resolve real binary path (`readlink -f`)
- extract interpreter and dynamic loader paths (`readelf`)
- extract RUNPATH/RPATH
- extract absolute path candidates (`strings`)

#### 2b. glibc version extraction and base snap comparison

Extract the maximum GLIBC_ version the binary was compiled against and compare
it to the base snap's glibc. A mismatch means `LD_LIBRARY_PATH` must never be
set in `environment:` blocks — use RPATH embedding instead.

```bash
# OCI image glibc version (use the libc.so.6 from the rootfs)
strings rootfs/lib/x86_64-linux-gnu/libc.so.6 2>/dev/null \
  | grep -oP 'GLIBC_\K[0-9]+\.[0-9]+' | sort -V | tail -1

# Host / base snap glibc version
strings /lib/x86_64-linux-gnu/libc.so.6 2>/dev/null \
  | grep -oP 'GLIBC_\K[0-9]+\.[0-9]+' | sort -V | tail -1
```

If versions differ, add to the **suggested next steps** output:
> ⚠️ glibc mismatch: OCI=X.Y vs base=X.Z. Set RPATH via `embed_rpath.sh`. 
> Do NOT use `LD_LIBRARY_PATH` in `environment:` sections.

#### 2c. Shebang chain analysis for PATH-exposed scripts

Some binaries in `usr/bin/` are symlinks to script files (Perl, Python, shell).
If the script's interpreter is absent from the base snap, it will fail silently
(no ELF error — just "command not found" or a missing-interpreter error from
the kernel).

```bash
# Find symlinks in usr/bin that point to Perl or Python scripts
for f in rootfs/usr/bin/*; do
    [ -L "$f" ] || continue
    target=$(readlink -f "$f" 2>/dev/null) || continue
    shebang=$(head -1 "$target" 2>/dev/null | head -c 128) || continue
    case "$shebang" in
        *perl*)   echo "PERL script: $f -> $target" ;;
        *python*) echo "PYTHON script: $f -> $target" ;;
    esac
done

# Also check non-symlink scripts directly in usr/bin
for f in rootfs/usr/bin/*; do
    [ -L "$f" ] && continue
    [ -f "$f" ] || continue
    shebang=$(head -1 "$f" 2>/dev/null | head -c 128) || continue
    case "$shebang" in
        *perl*)   echo "PERL script (direct): $f" ;;
        *python*) echo "PYTHON script (direct): $f" ;;
    esac
done
```

Report any found scripts in the output under **Interpreter script warnings**.
For each: note whether the interpreter is present in the OCI rootfs but not the
base snap, and suggest either adding it as a `stage-package` or patching the
entrypoint to call the underlying binary directly.

### 3. Run runtime syscall inspection (if executable can be run)

Use `strace` per `references/analysis-checklist.md` (Phase 3):
- capture file/network/process/syscall behavior
- derive observed resource usage from trace lines
- prefer observed behavior over weak static-only guesses

#### 3b. popen() / system() detection

C binaries that call `popen()` or `system()` fork the BASE SNAP's `/bin/sh`
(not the OCI shell). If the OCI image's glibc differs from the base snap's and
`LD_LIBRARY_PATH` is set, those base-shell forks will crash.

Detect such binaries statically:
```bash
# Check for popen/system dynamic symbol imports in all ET_EXEC binaries
for f in rootfs/usr/bin/* rootfs/usr/lib/postgresql/*/bin/*; do
    [ -f "$f" ] && ! [ -L "$f" ] || continue
    elf_type=$(readelf -h "$f" 2>/dev/null | sed -n 's/.*Type:[[:space:]]*\([A-Z_]*\).*/\1/p')
    [ "$elf_type" = "EXEC" ] || [ "$elf_type" = "DYN" ] || continue
    if objdump -T "$f" 2>/dev/null | grep -qE '\bpopen\b|\bsystem\b'; then
        echo "popen/system user: $f"
    fi
done
```

If any binary uses `popen`/`system`, add to the output:
> ⚠️ Binary X calls popen()/system() — will fork base snap /bin/sh.
> If glibc versions differ, LD_LIBRARY_PATH must not be set; use embed_rpath.sh.

### 4. Map evidence to interfaces (plugs)

- Use `references/syscall-interface-heuristics.md` for syscall/path evidence mapping.
- Use `references/capability-interface-map.md` when `config.json` capabilities are available.
- Use `references/mount-snap-map.md` when `config.json` mount entries are available (auto-provided vs plug-required mounts).
- Build deduplicated plug suggestions with confidence notes.
- Flag non-grantable or store-review-only cases explicitly.

### 5. Map evidence to layout candidates

- Use `references/mount-snap-map.md` for mount-derived layout candidates.
- Use `references/path-layout-heuristics.md` to classify paths as:
  - read-only shipped content
  - writable persistent
  - writable ephemeral
  - unknown / manual review
- Validate every layout target with `references/layout-constraints.md`.
- Keep forbidden or invalid targets in the **unmappable paths** list, not in `layout:`.

### 6. Produce mandatory output sections

Always return:
1. **Plugs to use** (with rationale/confidence)
2. **Layouts to add** (target -> source mapping)
3. **Paths that could not be mapped using layouts** (with reason)
4. **Suggested next steps**
5. **Wrapper script hints** — for each path that must be redirected via env var (forbidden/unmappable
   targets, or paths configured via environment variables in Phase 2c), list the export statement
   and any `mkdir -p` call needed in the snap wrapper script.
6. **Rootfs reproducibility notes** — when analysis uses a container-extracted
   `rootfs/`, list any observed or recommended filesystem mutations that must be
   encoded as `override-build:` or `override-prime:` commands before the final
   strict-confinement reproducibility validation.

Follow the output format in `references/analysis-checklist.md` (Phase 6).
If no `snapcraft.yaml` is present, still provide a merge-ready snippet based on
`assets/snapcraft-snippet-template.yaml`.

### 7. Apply suggestions to snapcraft.yaml when present

> **Delegation note:** When this skill is invoked as a delegated analysis step by an
> orchestrating skill (e.g. `snap-oci-container`), skip this step unless the orchestrator
> explicitly requests patching. The orchestrator is responsible for applying the
> results to avoid double-patching.

If a snapcraft file exists in the current folder:

1. Run dry-run first:
```bash
python3 scripts/apply_snapcraft_suggestions.py \
  --snapcraft snap/snapcraft.yaml \
  --app <app-name> \
  --plugs <plug1> <plug2> \
  --layout /var/lib/<app> '$SNAP_COMMON/<app>' \
  --dry-run
```

2. If dry-run output is correct, apply:
```bash
python3 scripts/apply_snapcraft_suggestions.py \
  --snapcraft snap/snapcraft.yaml \
  --app <app-name> \
  --plugs <plug1> <plug2> \
  --layout /var/lib/<app> '$SNAP_COMMON/<app>'
```

If only `snapcraft.yaml` exists at root, pass that path instead.

### 8. Hand off final rootfs reproducibility validation

When this analysis is part of an OCI/container snap workflow, explicitly require
the orchestrating workflow to perform the final reproducibility validation after
the snap first passes strict confinement:

1. Re-extract the container image filesystem as `rootfs_original/`.
2. Compare `rootfs_original/` with the working `rootfs/`.
3. Convert every detected working-tree/rootfs delta into snapcraft
   `override-build:` or `override-prime:` steps.
4. Rename `rootfs/` to `rootfs_edited/` and `rootfs_original/` to `rootfs/`.
5. Rebuild with `snapcraft --use-lxd --build-for <target_arch>`.
6. Reinstall and verify the rebuilt snap again under strict confinement.

Do not perform direct writes to the extracted `rootfs/` during this handoff; the
final test proves the recipe can reproduce required mutations from a clean image
filesystem.

## Resources

### references/analysis-checklist.md
Primary operational checklist and command set for static + runtime analysis.

### references/syscall-interface-heuristics.md
Evidence-to-plug mapping from observed syscalls and accessed resources.

### references/path-layout-heuristics.md
Path classification rules, writable/non-writable heuristics, and confidence model.

### references/capability-interface-map.md
OCI capability-to-interface mapping reused for `config.json`-aware analysis.

### references/mount-snap-map.md
OCI mount-to-snap mapping for auto-provided mounts, plug requirements, and
mount-derived layout candidates.

### references/layout-constraints.md
Authoritative allow/deny rules for snap layout targets.

### assets/snapcraft-snippet-template.yaml
Ready-to-merge snippet template for `apps.<app>.plugs` and `layout:` sections.

### scripts/apply_snapcraft_suggestions.py
Deterministic patcher that adds plugs/layouts to an existing `snapcraft.yaml`.

## Constraints

- Do not claim certainty when evidence is weak; label confidence explicitly.
- Do not add forbidden layout targets; report them under unmappable paths.
- Do not silently drop high-risk permissions; flag for manual review/store review.
- Prefer observed runtime behavior (`strace`) over static strings-only inference.
- Treat any container-extracted `rootfs/` directory as read-only evidence; never
  write to it directly during analysis or patching.
- If this skill is explicitly asked to patch snapcraft logic and that change
  creates or edits a script run by a part, require
  `snapcraft clean <part-name> --use-lxd --build-for <target_arch>` before the
  next rebuild so Snapcraft does not reuse the old cached script.
