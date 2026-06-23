---
name: snap-oci-container
description: >
  Orchestrates Docker/OCI container packaging into a snap from Docker Hub URLs, image
  references, `docker save` tarballs, or pre-extracted `config.json` + `rootfs/`.
  Downloads images with skopeo, runs docker-to-snap, delegates confinement inference,
  derives `--build-for` architecture from OCI metadata, patches `snapcraft.yaml`, then
  invokes `snap-iteration-workflow` for devmode and strict validation.
  WHEN: OCI config to snap, container to snap, config.json snap packaging, docker save
  tarball to snap, docker image to snap, snap interfaces from OCI, snap layout from rootfs,
  OCI architecture to snapcraft build-for, snap confinement from container image, mapping
  container capabilities to snap interfaces, hardcoded paths in snap, snap layout directives,
  convert OCI image to snap, analyze rootfs for snap, snap packaging from container,
  docker-to-snap, Docker Hub URL to snap, download container image for snap.
license: "Apache-2.0"
metadata:
  author: "Canonical"
  version: "2.2.0"
  summary: "Docker/OCI image URL, tarball, or rootfs → snap with extraction, analysis, recipe patching, and confinement validation."
  tags:
    - snap
    - snapcraft
    - oci
    - container
    - packaging
---

# OCI -> Snap

Convert a Docker/OCI container into a snap — starting from a Docker Hub URL,
image reference, `docker save` tarball, or pre-extracted `config.json` + `rootfs/`
— without repeating analysis already covered by `analyze-binary-for-snapping`.

## Workflow

Work through the phases in order.

> **⚠️ Rootfs Read-Only and Modification Tracking Protocol**
> The `rootfs/` directory extracted from the container image is a read-only
> source artifact. **Never write to `rootfs/` directly**: do not patch, chmod,
> delete, create files, rewrite symlinks, or mutate configs inside it. When a
> change is needed to make the snap build, install, or run correctly (e.g. ELF
> interpreter patching, symlink fixes, config mutations), encode that change as
> an `override-build:` or `override-prime:` step in `snapcraft.yaml`, or apply it
> to `prime/` during fast runtime experiments and immediately translate it into
> an override. This makes the snap recipe self-contained: replacing `rootfs/`
> with a new OCI image and re-running `snapcraft` produces a correct snap without
> any manual preprocessing. The final validation phase proves this by rebuilding
> from a newly extracted `rootfs/`.
>
> **Part-script cache rule:** If any change creates or edits a script that a
> snapcraft part runs (for example a `patch_scripts/*.sh`, `build_scripts/*.sh`,
> or other helper invoked from `override-build:` / `override-prime:`), always
> clean the affected part before rebuilding:
> `snapcraft clean <part-name> --use-lxd --build-for <target_arch>`.
> Snapcraft can otherwise reuse the old staged script from the part cache.
>
> **Pre-build clean decision:** Before every `snapcraft` build, check the files
> changed since the last build. If the only changed file is `snapcraft.yaml` (or
> `snap/snapcraft.yaml`, depending on the project layout), proceed directly with
> the build. If any other file changed, clean the project or affected part before
> building. This includes hook changes (`snap/hooks/*`, `hooks/*`) and helper
> scripts invoked by parts.
>
> See `references/override-steps-guide.md` for the complete catalog of
> modification patterns and their override equivalents. Phase 4b applies them
> to `snapcraft.yaml`.

---

### Phase 0 — Detect input type

Determine what the user has provided:

```bash
# Check for pre-extracted OCI artifacts
ls config.json rootfs/ 2>/dev/null

# Check for a tarball
ls *.tar 2>/dev/null
```

**If `config.json` and `rootfs/` exist** → skip Phase 0b and Phase 0c, proceed
to Phase 0d.

**If a Docker Hub URL or image reference was provided** (for example
`https://hub.docker.com/r/library/nginx`, `https://hub.docker.com/_/nginx`,
`nginx:1.27`, or `quay.io/org/app:tag`) → run Phase 0b to download it as a
docker-archive tarball, then continue with Phase 0c.

**If a `.tar` file was provided** (docker-archive or OCI-archive from `docker save`):
→ skip Phase 0b and proceed to Phase 0c to extract using `docker-to-snap`.

**If neither exists** → ask the user to provide either:
- a Docker Hub URL or container image reference,
- the path to a `.tar` file produced by `docker save`, or
- a directory containing `config.json` and `rootfs/`.

---

### Phase 0a — Ensure local dependencies are installed

Run the dependency helper before invoking local scripts or `docker-to-snap`.
It checks for `tar`, `skopeo`, `umoci`, `jq`, and the Python YAML library used by
`scripts/patch_snapcraft.py`.

```bash
python3 <skill-dir>/scripts/ensure_dependencies.py --install -y
```

Script behavior:
- Installs missing Ubuntu/Debian packages with `apt-get` (using `sudo` when not root).
- Exits 0 when all dependencies are present or installed successfully.
- Exits 1 when dependencies are missing and `--install` was not passed.
- Exits 2 when automatic installation fails or the system is not apt-based.
- Exits 3 when packages were installed but required commands/modules are still missing.

If the script exits non-zero, report the exact stderr and stop; do not proceed
with extraction or `snapcraft.yaml` patching until dependencies are available.

---

### Phase 0b — Download image link/reference to tarball

> **Only run this phase if Phase 0 determined the input is a Docker Hub URL or
> image reference.**

Use `scripts/download_image.py` to normalize the link/reference and download it
with `skopeo` as a docker-archive tarball. Do not require a local Docker daemon.

```bash
python3 <skill-dir>/scripts/download_image.py \
  "https://hub.docker.com/r/library/nginx" \
  --output nginx_latest.tar
```

Supported inputs:
- Docker Hub official image URLs: `https://hub.docker.com/_/nginx`
- Docker Hub repository URLs: `https://hub.docker.com/r/library/nginx`
- Docker Hub layer/tag URLs: `https://hub.docker.com/layers/.../<tag>`
- Image references: `nginx:1.27`, `docker.io/library/nginx:latest`, `quay.io/org/app:tag`
- `docker://` references, with the prefix stripped automatically

Script behavior:
- Normalizes Docker Hub links to pullable image references.
- Defaults missing tags to `latest`.
- Writes a local `docker-archive` tarball that can be passed to `docker-to-snap`.
- Prints the tarball path on success.
- Exits non-zero with a descriptive stderr error for unsupported URLs, missing
  `skopeo`, authentication failures, or download failures.

If the image is private or rate-limited, authenticate with `skopeo login <registry>`
outside the skill workflow, then rerun the same command.

After a successful download, treat the printed tarball path as the Phase 0c
`--tarball` input.

---

### Phase 0c — Extract tarball with docker-to-snap

> **Only run this phase if Phase 0 found a `.tar` file or Phase 0b created one.**

Read `references/docker-to-snap-options.md` for the full options reference, defaults,
filename inference rules, and example commands.

#### Gather required information from the user

Ask the user for the following. Do not assume values for required parameters.

**Required — always ask:**

1. **`--snap-store-prefix`**: The Brand Store namespace prefix (e.g. `acme`).
   The snap will be named `<prefix>-<application-name>`.

**Infer from filename — confirm or override:**

2. **`--application-name`**: Check whether the tarball filename matches
   `<name>_<version>.tar`. If it does, infer the name and confirm with the user.
   If not inferable, ask explicitly.

3. **`--application-version`**: Same inference logic. If not inferable (filename
   is e.g. `myapp.tar` or `myapp-image.tar`), ask explicitly. Default is `0.1`.

**Optional — prompt once, skip if user does not want to set:**

4. **`--output-folder`**: Where to create the snap project directory.
   Default: `<prefix>-<application-name>-snap` in the current directory.

5. **`--service-name`**: Hostname for local DNS advertisement in `/etc/hosts`.
   Default: same as application name. Only ask if service discovery hostname
   should differ.

6. **`--do-not-daemonize`**: Flag (no value). Decide based on the application's
   runtime model — **do not blindly ask the user to pick.** Classify the
   application first, then confirm your conclusion:

   - **Long-lived application → daemon (omit `--do-not-daemonize`).** If the
     application runs continuously — a server, listener, broker, scheduler,
     watcher, or a stage in a data pipeline that stays up consuming/producing a
     stream — it must be a daemon so systemd supervises and restarts it. This is
     the default; omit the flag.
   - **Run-to-completion application → not a daemon (pass `--do-not-daemonize`).**
     If the application is meant to be invoked, do some work, return a value or
     produce output, and then exit (a CLI tool, batch/one-shot job, converter,
     query/report generator, or interactive command), it must **not** be a
     daemon — a daemon that exits immediately is treated as a crash-looping
     failure by systemd. Pass `--do-not-daemonize`.

   Use the signals available before extraction to classify: the image's purpose
   and name, the user's description of how it is invoked, documented usage of the
   upstream image (e.g. `nginx`/`postgres` = daemon; a `*-cli`, `*-tools`, or
   `convert`/`report` image = run-to-completion), and whether the entrypoint
   blocks (listens on a port, loops) or returns. After Phase 1 parses
   `config.json`, re-check `process.args[0]` against this classification and, if
   it contradicts the choice made here, re-run `docker-to-snap` with the
   corrected flag. State your classification and reasoning to the user, and only
   ask them to confirm if the runtime model is genuinely ambiguous.

7. **`--envvars`**: Path to a `KEY=value` environment variables file. Ask if the
   application requires environment configuration.

#### Run docker-to-snap

Always include `--suppress-build` — the build is handled later by `snap-iteration-workflow`.

```bash
./docker-to-snap \
  --tarball <path-to-tar> \
  --snap-store-prefix <prefix> \
  [--application-name <name>] \
  [--application-version <version>] \
  [--output-folder <folder>] \
  [--service-name <name>] \
  [--do-not-daemonize] \
  [--envvars <file>] \
  --suppress-build
```

If `docker-to-snap` exits with an error listing missing tools (`tar`, `skopeo`,
`umoci`, `jq`), run the dependency helper once, then retry `docker-to-snap`:

```bash
python3 <skill-dir>/scripts/ensure_dependencies.py --install -y
```

If dependencies still cannot be installed or `docker-to-snap` fails again, report
the exact stderr and stop.

After a successful run, change the working context to the output folder:
```bash
cd <output-folder>
```

---

### Phase 0d — Locate target files

Locate OCI inputs and snapcraft target:

```bash
ls config.json rootfs/ 2>/dev/null
ls snapcraft.yaml snap/snapcraft.yaml 2>/dev/null
```

Record:
- `config.json` path
- `rootfs/` path
- `snapcraft.yaml` path (present if docker-to-snap was run, or if pre-existing project)
- app name(s) under `apps:` in `snapcraft.yaml` (needed for `--app` in Phase 4)

---

### Phase 1 — Parse OCI context

Read `config.json` and extract:

1. `process.args[0]` (main executable path inside container)
2. `process.capabilities` (all five sets)
3. `mounts`
4. `annotations["org.opencontainers.image.architecture"]` or equivalent OCI image architecture metadata
5. `process.user` (uid, gid, username — present if the container runs as non-root)

Resolve the executable under `rootfs` using `process.args[0]`.

#### 1.1 — Derive the required snap build architecture

Before any build, derive `target_arch` from the container metadata and carry it
through every later build command. **All snapcraft builds MUST include
`--build-for <target_arch>` — even when `target_arch` matches the host. Omitting
`--build-for` can produce snaps for all architectures, which is incorrect for a
single-container image.**

Use the OCI architecture annotation first, then equivalent image metadata if it
exists in the extracted `config.json`. Normalize OCI/Go architecture names to
Snapcraft/Debian names:

| OCI metadata value | `--build-for` value |
|---|---|
| `amd64`, `x86_64` | `amd64` |
| `arm64`, `aarch64` | `arm64` |
| `arm`, `arm/v7`, `armhf` | `armhf` |
| `386`, `i386` | `i386` |
| `ppc64le` | `ppc64el` |
| `s390x` | `s390x` |
| `riscv64` | `riscv64` |

```bash
python3 - <<'PY'
import json

c = json.load(open("config.json"))
annotations = c.get("annotations", {})
raw_arch = (
    annotations.get("org.opencontainers.image.architecture")
    or annotations.get("io.containerd.image.architecture")
    or c.get("architecture")
    or c.get("Architecture")
)
variant = (
    annotations.get("org.opencontainers.image.variant")
    or c.get("variant")
    or c.get("Variant")
)
if raw_arch == "arm" and variant:
    raw_arch = f"arm/{variant}"

mapping = {
    "amd64": "amd64",
    "x86_64": "amd64",
    "arm64": "arm64",
    "aarch64": "arm64",
    "arm": "armhf",
    "arm/v7": "armhf",
    "arm/v6": "armhf",
    "armhf": "armhf",
    "386": "i386",
    "i386": "i386",
    "ppc64le": "ppc64el",
    "s390x": "s390x",
    "riscv64": "riscv64",
}
target_arch = mapping.get(str(raw_arch).lower()) if raw_arch else None
if not target_arch:
    raise SystemExit(f"Cannot determine supported --build-for architecture from OCI metadata: {raw_arch!r}")
print(target_arch)
PY
```

Record the printed value as `target_arch`. If the command cannot determine a
supported architecture, stop and report the missing/unsupported metadata; do not
run `snapcraft`.

```bash
python3 -c "
import json, sys
c = json.load(open('config.json'))
p = c.get('process', {})
print('args:', p.get('args'))
print('user:', p.get('user', {}))
print('caps:', list(p.get('capabilities', {}).keys()))
print('arch:', c.get('annotations', {}).get('org.opencontainers.image.architecture') or c.get('architecture') or c.get('Architecture'))
"
```

---

### Phase 1b — Non-root user handling

> **Run this phase only if `process.user.uid` ≠ 0 (or `username` is set to a
> non-root value).** Skip if the container runs as root.

Read `references/system-usernames-guide.md` in full.

The OCI container specifies a non-root user, meaning the application requires
privilege separation. Inside a snap, daemons always start as root (systemd
launches them). The `system-usernames` snapcraft feature provides the correct
mechanism.

#### 1b.1 — Check user configurability

Run the configurability detection commands from
`references/system-usernames-guide.md §3` to determine whether the application
accepts a configurable user (env var, CLI flag, config file) or has it
hardcoded.

#### 1b.2 — Apply system-usernames

Add the `system-usernames` stanza to `snapcraft.yaml`:

```bash
# Append (or confirm present) in snapcraft.yaml
grep -q "system-usernames" snap/snapcraft.yaml \
  || echo "system-usernames:
  _daemon_: shared" >> snap/snapcraft.yaml
```

Then choose a privilege-drop method based on configurability:

| Configurability | Method | See |
|---|---|---|
| User set via env var or config file | Set the var/key to `_daemon_` in wrapper | §4a |
| User set via CLI flag | Pass `--user _daemon_` in wrapper | §4a |
| User hardcoded / binary calls `setuid()` itself | `setpriv` wrapper | §4b |
| Binary calls `getpwnam()` + `setuid()` with the config'd name | No wrapper change needed | §4c |

#### 1b.3 — Record the method for Phase 4b

Note the chosen privilege-drop method and any wrapper script changes needed
so they are captured as `override-build` steps in Phase 4b.

---

### Phase 1c — Merged-/usr detection and glibc compatibility check

> **Always run this phase before Phase 2.** Read `references/glibc-compat-guide.md`
> in full and apply both checks:

- **1c.1 — Merged-/usr:** run `[ -L rootfs/bin ]`; if merged, use `usr/bin/` paths
  in `snapcraft.yaml` `command:` fields (not `bin/`), and watch for stage collisions.
- **1c.2 — glibc compatibility:** compare OCI vs base snap glibc versions. If they
  differ, **never** add `LD_LIBRARY_PATH` to `environment:` — use RPATH embedding
  via `embed_rpath.sh` instead. See `references/override-steps-guide.md §2`.

---

### Phase 2 — Delegate binary analysis

Invoke the `analyze-binary-for-snapping` skill and pass:

- resolved binary path
- `config.json` path
- `snapcraft.yaml` path (if present; for context only — see note below)
- app name (if known)
- optional runtime command for `strace`

> **Patching protocol:** instruct `analyze-binary-for-snapping` to produce output only
> and skip its Step 7 (snapcraft.yaml patching). Patching is handled in Phase 4 of this
> skill to keep responsibility clear.

Require this output from the delegated skill:

1. **Plugs to use**
2. **Layouts to add**
3. **Paths that could not be mapped using layouts**
4. **Suggested next steps**
5. **Wrapper script hints**

**Do not re-run capability/mount/binary/path analysis in this skill when delegated output is available.**

---

### Phase 3 — Fallback analysis (only if delegation is unavailable)

Run local analysis only if `analyze-binary-for-snapping` cannot be used.

For fallback, use:
- `references/capability-interface-map.md`
- `references/mount-snap-map.md`
- `references/analysis-checklist.md`
- `references/layout-constraints.md`

---

### Phase 4 — Apply results to `snapcraft.yaml`

Apply the delegated plugs and layouts using `scripts/patch_snapcraft.py`.

**Dry run first**:

```bash
python3 <skill-dir>/scripts/patch_snapcraft.py \
  --snapcraft snap/snapcraft.yaml \
  --app <app-name> \
  --plugs network network-bind \
  --layout /var/lib/<app> '$SNAP_COMMON/<app>' \
  --layout /usr/lib/<libname> '$SNAP/lib/<libname>' \
  --dry-run
```

**Apply for real**:

```bash
python3 <skill-dir>/scripts/patch_snapcraft.py \
  --snapcraft snap/snapcraft.yaml \
  --app <app-name> \
  --plugs network network-bind \
  --layout /var/lib/<app> '$SNAP_COMMON/<app>' \
  --layout /usr/lib/<libname> '$SNAP/lib/<libname>'
```

Script behavior:
- Skips plugs/layouts already present (idempotent).
- Creates `snapcraft.yaml.bak` before writing.
- Warns and skips invalid layout targets.
- Exits non-zero on errors.

If the script fails:
- Exit 1 — `snapcraft.yaml` not found
- Exit 2 — app name not found
- Exit 3 — YAML parse error
- Exit 4 — no `--plugs` or `--layout` provided

---

### Phase 4b — Encode rootfs modifications as override steps

> **Run this phase after Phase 4 and before invoking `snap-iteration-workflow`.**
> Also revisit it after Phase 5 whenever a new rootfs modification is discovered
> during devmode or strict-confinement iteration.

Read `references/override-steps-guide.md` in full.

#### 4b.1 — Inventory all rootfs changes

Because the extracted container `rootfs/` is read-only and must never be written
to directly, first check whether any accidental `rootfs/` mutations occurred and
then list every intentional experiment made in `prime/` during this session:

```bash
# Accidental changes to rootfs/ that must be reverted and encoded as overrides
git diff --name-only rootfs/ 2>/dev/null

# Files modified compared to the extracted OCI tarball
diff -rq rootfs/ <original-rootfs-backup>/ 2>/dev/null
```

If no version-control baseline exists, check shell history for `patchelf`,
`ln -sf`, `chmod`, `sed -i`, `cp`, `rm`, or similar commands applied to paths
inside `rootfs/` or `prime/`. Any command that wrote to `rootfs/` directly is a
workflow violation: revert the `rootfs/` mutation and encode the equivalent
change as an override command instead.

#### 4b.2 — Map each change to an override command

Use the pattern catalog in `references/override-steps-guide.md` to find the
equivalent override command for each manual change (patchelf, symlinks, chmod,
sed edits, file deletions, new file injection, mkdir — all have dedicated
sections with examples).

#### 4b.3 — Apply override steps to snapcraft.yaml

> **Modular approach (recommended for complex mutations):** If there are more than
> ~3 mutations, or if the same mutations will be reused across projects, extract
> each logical action into a dedicated shell script in a `patch_scripts/` folder
> alongside `snapcraft.yaml`, then call them from `override-build:` via
> `"$CRAFT_PROJECT_DIR"/patch_scripts/<script>.sh`. See
> `references/override-steps-guide.md` → "Modularising Override Steps with
> patch_scripts/" for conventions, a folder layout example, and a minimal script
> template. After creating or editing any script called by the part, clean that
> part before rebuilding to avoid Snapcraft reusing a cached copy. Use inline
> commands (via `scripts/patch_snapcraft.py` below) for simple, one-off mutations.

Use `scripts/patch_snapcraft.py` with `--override-build` / `--override-prime`.
Always dry-run first:

```bash
# Dry run — verify the YAML output before writing
python3 <skill-dir>/scripts/patch_snapcraft.py \
  --snapcraft snap/snapcraft.yaml \
  --part oci-container \
  --override-build "patchelf --set-interpreter \$SNAPCRAFT_PART_INSTALL/lib/ld.so \$SNAPCRAFT_PART_INSTALL/usr/bin/myapp" \
  --override-build "chmod 755 \$SNAPCRAFT_PART_INSTALL/usr/bin/myapp" \
  --dry-run

# Apply
python3 <skill-dir>/scripts/patch_snapcraft.py \
  --snapcraft snap/snapcraft.yaml \
  --part oci-container \
  --override-build "patchelf --set-interpreter \$SNAPCRAFT_PART_INSTALL/lib/ld.so \$SNAPCRAFT_PART_INSTALL/usr/bin/myapp" \
  --override-build "chmod 755 \$SNAPCRAFT_PART_INSTALL/usr/bin/myapp"
```

Script behaviour:
- Inserts `snapcraftctl build` (or `snapcraftctl prime`) as the first line if absent.
- Skips commands already present (idempotent).
- Creates `snapcraft.yaml.bak` before writing.
- Exit 5 — `--part` name not found; Exit 6 — `--override-*` used without `--part`.

#### 4b.4 — Verify the override reproduces the manual change

After applying override steps, or after creating/editing any helper script that
the part runs, clean only the affected part and rebuild:

```bash
snapcraft clean oci-container --use-lxd --build-for <target_arch>
snapcraft --use-lxd --build-for <target_arch>
```

Inspect `prime/` to confirm the mutation was applied:

```bash
ldd prime/usr/bin/myapp              # ELF interpreter correct?
ls -la prime/usr/lib/libfoo.so*      # symlinks present?
cat prime/etc/myapp/myapp.conf       # config mutations in place?
```

Revert any manual `rootfs/` changes that are now covered by override steps, then
rebuild a second time to confirm the snap still works from a clean rootfs.

---

### Phase 5 — Build, install, and iterative confinement validation

Invoke the `snap-iteration-workflow` skill to build the snap and validate it at runtime.
Pass it the `snapcraft.yaml` path, the project directory, and the `target_arch`
derived from OCI metadata as context. Instruct it that every build command must
include `--build-for <target_arch>`.

This phase loops until the snap passes under strict confinement with no denials.

#### 5.1 — Environment check and build

Follow the `snap-iteration-workflow` skill through its **Phase 1 (Environment Setup)**
and **Phase 2 (Build)** using `snapcraft --use-lxd --build-for <target_arch>`.
The build must succeed before proceeding.

Before running each build, inspect changes made since the previous successful
build:

```bash
# If the project is tracked by git:
git diff --name-only
git ls-files --others --exclude-standard

# If the project is not tracked by git, compare against the file list or notes
# recorded immediately after the previous build.
```

Apply the clean decision strictly:

- If the only changed file is `snapcraft.yaml` or `snap/snapcraft.yaml`, run the
  build without cleaning.
- If any other path changed, clean first, then build. Prefer cleaning the
  affected part when it is clear; otherwise clean the whole project. Hook
  changes always require cleaning before the build.

```bash
# Affected part known
snapcraft clean <part-name> --use-lxd --build-for <target_arch>

# Fallback when the affected part is unclear
snapcraft clean --use-lxd --build-for <target_arch>

snapcraft --use-lxd --build-for <target_arch>
```

If the build fails due to a missing plug, layout, or missing package that static
analysis did not catch, fix `snapcraft.yaml` and rebuild before continuing.

#### 5.2 — Devmode install and application validation

Follow `snap-iteration-workflow` **Phase 3 (Install & Run)** — install with `--devmode` first.

Confirm:
- The daemon or entrypoint starts without crashing
- `snap logs -f <snap-name>.entrypoint` shows no ELF interpreter or library errors

> **ELF crash check:** If the service starts but immediately exits with no output,
> the interpreter layout or `LD_LIBRARY_PATH` is wrong — this is a build correctness
> issue, not a confinement issue. Recheck `patch_interpreter.sh` output and layout
> entries before proceeding.

> **Modification tracking:** If devmode reveals a fix that requires changing a file
> in `prime/` (e.g. adjusting a wrapper script or patching a library path), encode
> the fix as an `override-build` step via `scripts/patch_snapcraft.py` before
> continuing. If the fix changes a script invoked by a part, clean that affected
> part before rebuilding. Return to Phase 4b.3 to apply it and Phase 4b.4 to verify it.

**Do not proceed to strict confinement until devmode confirms the application works.**

#### 5.3 — Strict confinement iteration loop

Classic confinement should never be used.

Follow `snap-iteration-workflow` **Phase 4 (Verify & Harden)** — "Confinement hardening loop".

> **All snap install and test commands run inside the LXD test container, never on the host.**
> See `snap-iteration-workflow` `references/install-and-verify.md` → Option 1 for container setup.

For each iteration:

1. Remove devmode install and reinstall in strict mode (inside the LXD container):
   ```bash
   # lxc exec snap-test -- bash
   snap remove <snap-name>
   snap install --dangerous <snap-name>_<ver>_<arch>.snap
   ```

2. Run `snappy-debug` and exercise all application functionality (inside the LXD container):
   ```bash
   snap install snappy-debug
   sudo journalctl --output=short --follow --all | sudo snappy-debug
   ```

3. Collect all denials. For each denial:
   - Map to a snap interface using `references/capability-interface-map.md` or the
     `analyze-binary-for-snapping` skill's interface suggestions
   - If the denial is a hardcoded path, add a layout using `references/layout-constraints.md`
     to validate the target

4. Apply new plugs and layouts with `scripts/patch_snapcraft.py`:
   ```bash
   python3 <skill-dir>/scripts/patch_snapcraft.py \
     --snapcraft snap/snapcraft.yaml \
     --app <app-name> \
     --plugs <new-plug-1> <new-plug-2> \
     --layout /hardcoded/path '$SNAP/hardcoded/path' \
     --dry-run
   # then apply without --dry-run
   ```

5. Rebuild (return to Phase 2 / `snap-iteration-workflow` Phase 2) and reinstall.

6. Repeat until `snappy-debug` shows **no denials** during normal application operation.

#### 5.4 — Store-review-only interface check

Before declaring confinement complete, check for interfaces that require Snap Store
manual review and cannot be self-connected. See `snap-iteration-workflow`
`references/install-and-verify.md` → "Identify store-review-only interfaces early".

Common store-review-only interfaces: `snapd-control`, `system-files`, `docker-support`,
`kubernetes-support`.

If any are needed, note them explicitly in the final report (Phase 7) so the user
can plan for store review time.

---

### Phase 6 — Final rootfs reproducibility validation

Run this phase only after Phase 5 has produced a snap that runs successfully
under strict confinement with no expected denials. This phase is mandatory for
container-image snaps and is the final proof that `snapcraft.yaml` reproduces
all required changes from a clean extraction.

#### 6.1 — Re-extract the container filesystem as `rootfs_original/`

Use the same source image or tarball and the same docker-to-snap options recorded
in Phase 0c. Extract into a temporary output folder, then move only the newly
extracted `rootfs/` into the working project as `rootfs_original/`.

```bash
[ ! -e .rootfs-reextract ] && [ ! -e rootfs_original ] || {
  echo ".rootfs-reextract or rootfs_original already exists; move it aside before continuing" >&2
  exit 1
}
./docker-to-snap \
  --tarball <path-to-original-tar> \
  --snap-store-prefix <prefix> \
  [--application-name <name>] \
  [--application-version <version>] \
  [--service-name <name>] \
  [--do-not-daemonize] \
  [--envvars <file>] \
  --output-folder .rootfs-reextract \
  --suppress-build
mv .rootfs-reextract/rootfs rootfs_original
```

If the workflow started from a Docker Hub URL or image reference, reuse the
tarball downloaded in Phase 0b. If no tarball is available, download the same
image reference again with `scripts/download_image.py`, then extract from that
tarball. If the original image cannot be reproduced, stop and report that final
recipe reproducibility cannot be proven.

#### 6.2 — Compare `rootfs_original/` with the working `rootfs/`

```bash
diff -rq rootfs_original/ rootfs/
```

Treat every reported difference as a required recipe input:
- files present only in `rootfs/` → create them via `override-build:` or
  `override-prime:`
- files present only in `rootfs_original/` → delete them via an override step
- files that differ → reproduce the content, symlink target, permission, owner,
  or metadata change via an override step

Use `references/override-steps-guide.md` to map each delta to a deterministic
override command. If there are more than ~3 changes, prefer a helper script in
`patch_scripts/` and invoke it from the affected part's override. After creating
or editing any part-run script, clean that part before rebuilding.

#### 6.3 — Add every delta to `snapcraft.yaml`

Use `scripts/patch_snapcraft.py` to add the required `override-build:` or
`override-prime:` entries. Always dry-run first:

```bash
python3 <skill-dir>/scripts/patch_snapcraft.py \
  --snapcraft snap/snapcraft.yaml \
  --part oci-container \
  --override-build "<command-reproducing-delta-1>" \
  --override-build "<command-reproducing-delta-2>" \
  --dry-run
# then apply without --dry-run
```

Do not proceed until every difference from `diff -rq rootfs_original/ rootfs/`
has a corresponding recipe change or an explicit documented reason why it is not
needed.

#### 6.4 — Swap in the clean rootfs and rebuild

Preserve the edited working tree for inspection, then make the clean extraction
the active rootfs:

```bash
mv rootfs rootfs_edited
mv rootfs_original rootfs
snapcraft clean oci-container --use-lxd --build-for <target_arch>
snapcraft --use-lxd --build-for <target_arch>
```

#### 6.5 — Reinstall and verify strict confinement again

Return to Phase 5.3 and install the newly rebuilt snap inside the isolated test
environment in strict mode. Exercise normal application functionality and run
`snappy-debug` again. The final pass condition is:

> A snap built from the newly extracted `rootfs/` and the updated
> `snapcraft.yaml` runs successfully under strict confinement with no expected
> denials.

If it fails, inspect `rootfs_edited/` and the diff output to identify the missing
recipe step, add it to `snapcraft.yaml`, re-extract a fresh `rootfs_original/`,
repeat the swap, rebuild, and re-run strict validation until it passes.

---

### Phase 7 — Final report

Return:

1. **Rationale table** (one row per evidence item mapped to a snap construct; use
   delegated `analyze-binary-for-snapping` output as the source of evidence items
   when delegation was used, plus any additional plugs/layouts discovered during
   confinement iteration in Phase 5)
2. **Applied changes** (what was added vs skipped — from both static analysis and runtime iteration)
3. **Unmappable paths** with reason and workaround
4. **Store-review-only interfaces** required (if any), with a note that these need Snap Store approval
5. **Target build architecture** — the OCI metadata value and normalized
   `--build-for <target_arch>` value used for every snapcraft build
6. **Wrapper script hints** — taken from delegated skill output; supplement with any
   OCI-specific observations not already captured there
7. **Reproducibility proof** — confirm whether Phase 6 found rootfs differences,
   which override steps were added, and that the rebuilt snap from the clean
   `rootfs/` passed strict confinement

#### Rationale table format

| OCI/evidence item | Type | Snap construct | Rationale |
|---|---|---|---|
| `CAP_NET_BIND_SERVICE` | capability | `plugs: [network-bind]` | Bind to ports < 1024 |
| `/etc/resolv.conf` mount | mount | `plugs: [network]` | DNS resolution |
| `/usr/lib/<libname>` path | binary path | `layout: /usr/lib/<libname> -> $SNAP/lib/<libname>` | Hardcoded at link time |

---

## Reference Files

| Resource | When to use |
|---|---|
| `references/docker-to-snap-options.md` | Options, defaults, filename inference rules, and example commands for Phase 0c tarball extraction |
| `references/glibc-compat-guide.md` | Merged-/usr detection and glibc compatibility checks; when to avoid LD_LIBRARY_PATH (Phase 1c) |
| `references/override-steps-guide.md` | Pattern catalog: convert rootfs/prime mutations (patchelf, symlinks, chmod, sed, etc.) to override-build/override-prime steps (Phase 4b) |
| `references/system-usernames-guide.md` | Non-root user handling: system-usernames YAML syntax, configurability detection, setpriv wrapper, ownership rules (Phase 1b) |
| Skill: `analyze-binary-for-snapping` | Primary analysis path for plugs/layouts/unmappable paths |
| Skill: `snap-iteration-workflow` | Build, install, devmode validation, strict confinement iteration, and final clean-rootfs reproducibility validation (Phases 5-6) |
| `references/capability-interface-map.md` | Map runtime denial capabilities to snap interfaces; fallback capability mapping |
| `references/mount-snap-map.md` | Fallback mount mapping |
| `references/analysis-checklist.md` | Fallback binary/rootfs analysis checklist |
| `references/layout-constraints.md` | Validate layout targets (both static and runtime-discovered paths) |
| `scripts/ensure_dependencies.py` | Checks and installs local tool/Python dependencies needed by this skill |
| `scripts/download_image.py` | Downloads Docker Hub URLs or image references as docker-archive tarballs |
| `scripts/patch_snapcraft.py` | Applies plugs, layouts, and override steps to `snapcraft.yaml` |

Quick lookup examples:

```bash
grep "store-prefix" references/docker-to-snap-options.md
grep "merged-usr\|glibc\|LD_LIBRARY_PATH" references/glibc-compat-guide.md
grep "patchelf" references/override-steps-guide.md
grep "symlink" references/override-steps-guide.md
grep "setpriv\|_daemon_\|snap_daemon" references/system-usernames-guide.md
grep "CAP_AUDIT_WRITE" references/capability-interface-map.md
grep "resolv.conf" references/mount-snap-map.md
grep "RUNPATH" references/analysis-checklist.md
grep "store-review" <snap-iteration-workflow-dir>/references/install-and-verify.md
```
