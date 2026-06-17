# docker-to-snap Options Reference

Reference for preparing and invoking `docker-to-snap` to extract a Docker/OCI
tarball into the `rootfs/`, `config.json`, `snapcraft.yaml`, and `build_scripts/`
artifacts needed by the `snap-oci-container` skill workflow.

The tool is located in the current working directory (the docker-to-snap repository root).

**Always use `--suppress-build`** when invoking from this skill — the build and
confinement iteration are handled by `snap-iteration-workflow` in Phase 5.

---

## Required parameters

| Parameter | Flag | Notes |
|---|---|---|
| Tarball path | `--tarball <path>` | Path to the `.tar` file from `docker save` or OCI export |
| Brand Store prefix | `--snap-store-prefix <prefix>` | Namespace prefix for the snap name (e.g. `acme` → snap named `acme-myapp`). **Always prompt the user for this.** |

---

## Optional parameters — prompt the user

Prompt for these if the tarball filename does **not** follow the `<name>_<version>.tar`
convention, or if the user has specific requirements:

| Parameter | Flag | Default / inference | Prompt condition |
|---|---|---|---|
| Application name | `--application-name <name>` | Inferred from tarball filename (`myapp_1.2.tar` → `myapp`) | Prompt if filename does not match `name_version.tar` pattern, or if user wants to override |
| Application version | `--application-version <ver>` | Inferred from tarball filename | Prompt if not inferable or user wants to override |
| Output folder | `--output-folder <folder>` | `<prefix>-<name>-snap` | Prompt if user wants a custom destination |
| Service name | `--service-name <name>` | Same as application name | Prompt if the DNS service hostname should differ from the app name |

---

## Optional parameters — offer but do not require

Offer these as optional; skip unless the user mentions them:

| Parameter | Flag | Notes |
|---|---|---|
| OCI image tag | `--oci-image-tag <tag>` | Default: `latest`. Only relevant if tarball is already in OCI archive format |
| Environment variables file | `--envvars <file>` | File of `KEY=value` pairs to embed in the snap recipe |
| Do not daemonize | `--do-not-daemonize` | Flag only (no value). Makes the snap a **run-to-completion app** instead of a daemon. See the decision rule below — pass it for run-to-completion apps, omit it for long-lived apps. |

---

## Daemon vs. run-to-completion decision

`docker-to-snap` makes the snap a **daemon** by default (systemd-supervised,
auto-restarted). Choose based on the application's runtime model:

| Application runtime model | Examples | Flag |
|---|---|---|
| **Long-lived** — runs continuously, stays up | web/API server, database, message broker, scheduler, watcher, a streaming stage in a data pipeline | **Omit** `--do-not-daemonize` (daemon — default) |
| **Run-to-completion** — invoked, does work, returns a value/output, then exits | CLI tool, batch/one-shot job, file converter, query/report generator, interactive command | **Pass** `--do-not-daemonize` |

A run-to-completion app packaged as a daemon will be treated by systemd as a
crash-looping failure (it exits immediately), so this distinction matters.
Classify from the image purpose/name, the entrypoint behaviour (blocks/listens
vs. returns), and upstream documentation before invoking the tool.

---

## Parameters to never use from this skill

| Parameter | Reason |
|---|---|
| *(no `--suppress-build`)* | **Always** pass `--suppress-build` — the build is handled by `snap-iteration-workflow` |
| `--preserve-image-contents` | Only for re-packaging without re-downloading; not applicable to a fresh tarball |
| `--preserve-snap-recipe` | Only when updating an existing project; not applicable to first-time extraction |

---

## Filename inference rules

`docker-to-snap` infers the application name and version from the tarball filename
when it matches the pattern `<name>_<version>.tar`:

| Filename | Inferred name | Inferred version |
|---|---|---|
| `myapp_1.2.3.tar` | `myapp` | `1.2.3` |
| `my-service_2024.01.tar` | `my-service` | `2024.01` |
| `myapp.tar` | *(not inferable)* | `0.1` (default) |
| `myapp_latest.tar` | `myapp` | `latest` |

If inference is not possible, `docker-to-snap` will still run but the snap name
may be wrong — always confirm with the user.

---

## Output directory structure

After a successful `docker-to-snap --suppress-build` run, the output folder contains:

```
<output-folder>/
├── rootfs/                     ← OCI container filesystem (input for Phase 1+)
├── snap/
│   ├── snapcraft.yaml          ← generated recipe (input for Phase 4+)
│   └── hooks/
│       ├── install
│       ├── post-refresh
│       ├── remove
│       └── configure
├── build_scripts/
│   ├── create_wrapper.sh
│   ├── patch_interpreter.sh
│   └── replace_absolute_symlinks.sh
├── config.json                 ← OCI image config (input for Phase 1+)
├── umoci.json
└── version
```

After extraction, set the working context for subsequent phases to `<output-folder>/`.

---

## Example commands

**Docker Hub URL or image reference — download first, then extract:**
```bash
python3 <skill-dir>/scripts/download_image.py \
  "https://hub.docker.com/_/nginx" \
  --output nginx_latest.tar

./docker-to-snap \
  --tarball nginx_latest.tar \
  --snap-store-prefix acme \
  --application-name nginx \
  --application-version latest \
  --suppress-build
```

**Minimal — tarball filename encodes name and version:**
```bash
./docker-to-snap \
  --tarball myapp_1.2.3.tar \
  --snap-store-prefix acme \
  --suppress-build
```

**Tarball with non-standard filename:**
```bash
./docker-to-snap \
  --tarball myapp-image.tar \
  --snap-store-prefix acme \
  --application-name myapp \
  --application-version 1.2.3 \
  --suppress-build
```

**With custom output folder and service name:**
```bash
./docker-to-snap \
  --tarball myapp_1.2.3.tar \
  --snap-store-prefix acme \
  --output-folder /tmp/acme-myapp-snap \
  --service-name myapp-svc \
  --suppress-build
```

**Run-to-completion (CLI / one-shot / interactive) application:**
```bash
./docker-to-snap \
  --tarball myapp_1.2.3.tar \
  --snap-store-prefix acme \
  --do-not-daemonize \
  --suppress-build
```

---

## Prerequisite check

Before running `docker-to-snap`, install any missing host dependencies:

```bash
python3 <skill-dir>/scripts/ensure_dependencies.py --install -y
```

The helper checks `tar`, `skopeo`, `umoci`, `jq`, and the Python YAML library
used by `scripts/patch_snapcraft.py`. If `docker-to-snap` still exits with a
clear error listing missing tools, run the helper once more and retry the
original `docker-to-snap` command. Report the exact stderr if the retry fails.
