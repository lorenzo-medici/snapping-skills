# Binary-to-Snap Analysis Checklist

Use this checklist to analyze a binary, infer confinement requirements, and produce
snap suggestions in a repeatable format.

---

## Phase 1 — Gather Inputs

- [ ] Locate target binary path.
- [ ] Locate `snapcraft.yaml` (`snap/snapcraft.yaml` or root) if present.
- [ ] Optionally locate `config.json` for OCI capabilities and mount entries.
- [ ] Confirm whether the binary can be executed for runtime tracing.

---

## Phase 2 — Static Binary Analysis

### 2a. Resolve binary path and symlinks

```bash
ls -la <binary-path>
readlink -f <binary-path>
file <resolved-binary-path>
```

- [ ] Record resolved binary path.
- [ ] Record architecture and binary type.

### 2b. Extract interpreter, dependencies, and runtime linker paths

```bash
readelf -p .interp <resolved-binary-path>
readelf -d <resolved-binary-path> | grep -E '(NEEDED|RUNPATH|RPATH)'
```

- [ ] Record interpreter path (e.g., `/lib64/ld-linux-...`).
- [ ] Record RUNPATH/RPATH directories.

### 2c. Extract absolute path candidates from strings

```bash
strings <resolved-binary-path> \
  | grep -oE '(/[a-zA-Z0-9._+-]+){2,}' \
  | sort -u
```

- [ ] Keep likely runtime paths.
- [ ] Drop obvious source/build paths (toolchain/debug paths).

---

## Phase 3 — Runtime Syscall Analysis (Preferred)

If runnable, trace real behavior and prioritize this evidence.

```bash
strace -f -o /tmp/binary.trace -qq -s 256 -yy \
  -e trace=%file,%network,%process,%ipc,%desc,%memory,%signal \
  <command> [args...]
```

Quick evidence extraction:

```bash
grep -E 'openat?\(|statx?\(|access\(|mkdir(at)?\(|unlink(at)?\(|rename(at)?\(|bind\(|connect\(|socket\(|ioctl\(|mount\(|umount2\(|ptrace\(' /tmp/binary.trace
```

- [ ] Record accessed paths.
- [ ] Record device node usage (`/dev/...`).
- [ ] Record network behavior (bind/connect/socket family).
- [ ] Record privileged/process control syscalls (`ptrace`, `mount`, etc.).

---

## Phase 4 — Interface (Plug) Mapping

Use:
- `references/syscall-interface-heuristics.md`
- `references/capability-interface-map.md` (if `config.json` available)
- `references/mount-snap-map.md` (if `config.json.mounts` is available)

For each evidence item:

- [ ] Map to interface candidate or “drop/manual-review”.
- [ ] Assign confidence (`high`, `medium`, `low`).
- [ ] Deduplicate plugs.
- [ ] Flag store-review or non-grantable cases explicitly.

For each mount entry in `config.json.mounts`:

- [ ] Mark auto-provided mounts as no-action.
- [ ] Add required plugs for mount-backed resources (network, devices, etc.).
- [ ] Record mount-driven layout candidates for Phase 5.

---

## Phase 5 — Layout Candidate Mapping

Use:
- `references/path-layout-heuristics.md`
- `references/layout-constraints.md`
- `references/mount-snap-map.md` (for mount-derived layout candidates)

For each path candidate:

- [ ] Classify as read-only shipped / writable persistent / writable ephemeral / unknown.
- [ ] Propose source target:
  - read-only shipped → `$SNAP/...`
  - writable persistent → `$SNAP_COMMON/...`
  - writable ephemeral → prefer runtime redirect (`$XDG_RUNTIME_DIR` or `$SNAP_DATA/run`)
- [ ] Validate target path against denylist/root-level rules.
- [ ] Move invalid/forbidden paths to unmappable list.

---

## Phase 6 — Required Output Format

Always return these four sections:

### 1) Plugs to use

| Plug | Evidence | Confidence | Notes |
|---|---|---|---|
| `network` | `connect()` to DNS/remote endpoint | high | Required for outbound networking |

### 2) Layouts to add

| Target path | Mapping | Confidence | Rationale |
|---|---|---|---|
| `/var/lib/myapp` | `$SNAP_COMMON/myapp` | high | Runtime state writes observed |

### 3) Paths that could not be mapped using layouts

| Path | Why not mappable | Suggested workaround |
|---|---|---|
| `/run/myapp` | `/run` denylisted in layouts | use env var redirect to `$XDG_RUNTIME_DIR/myapp` |

### 4) Suggested next steps

1. Connect required interfaces.
2. Apply compatible env-var overrides for forbidden targets.
3. Re-run trace inside the snap (`--devmode` first) to confirm.
4. Iterate to remove unnecessary interfaces.

### 5) Wrapper script hints

For each path that must be redirected at runtime (forbidden layout target, unmappable path,
or env-var-configurable path found in Phase 2c), provide the export statement and mkdir call:

```bash
export <APP>_CONFIG_PATH="$SNAP_COMMON/config"
mkdir -p "$SNAP_COMMON/config"
```

If multiple paths redirect to the same directory, emit one `mkdir -p` covering all of them.

If no `snapcraft.yaml` exists, also provide a merge-ready snippet based on:
- `assets/snapcraft-snippet-template.yaml`
- The plugs/layouts selected in this analysis

---

## Phase 7 — Optional snapcraft.yaml Patching

If `snapcraft.yaml` exists, patch suggestions:

1. Dry-run:
```bash
python3 scripts/apply_snapcraft_suggestions.py \
  --snapcraft <path-to-snapcraft.yaml> \
  --app <app-name> \
  --plugs <plug1> <plug2> \
  --layout /var/lib/<app> '$SNAP_COMMON/<app>' \
  --dry-run
```

2. Apply:
```bash
python3 scripts/apply_snapcraft_suggestions.py \
  --snapcraft <path-to-snapcraft.yaml> \
  --app <app-name> \
  --plugs <plug1> <plug2> \
  --layout /var/lib/<app> '$SNAP_COMMON/<app>'
```
