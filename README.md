# OCI Snapping Skills

A collection of [Agent Skills](https://github.com/canonical/skills) that help an
AI agent package Docker/OCI container images and Linux binaries as
[snaps](https://snapcraft.io/), and iterate on their confinement until they run
under strict confinement.

The skills are designed to be composed: `snap-oci-container` orchestrates the
end-to-end flow, delegating confinement analysis to `analyze-binary-for-snapping`
and build/run/verify cycles to `snap-iteration-workflow`.

## Skills

### `snap-oci-container`

Orchestrates packaging a Docker/OCI container into a snap, starting from a Docker
Hub URL, image reference, `docker save` tarball, or a pre-extracted
`config.json` + `rootfs/`. It downloads images with skopeo, runs
`docker-to-snap`, derives the `--build-for` architecture from OCI metadata,
patches `snapcraft.yaml`, and hands off to the other skills for analysis and
validation.

Key contents:

- `scripts/download_image.py` — download/extract container images.
- `scripts/ensure_dependencies.py` — verify required tooling is present.
- `scripts/patch_snapcraft.py` — apply plugs, layouts, and other edits to `snapcraft.yaml`.
- `references/` — guides on docker-to-snap options, glibc compatibility, layout
  constraints, capability/interface mapping, override steps, and system usernames.

### `analyze-binary-for-snapping`

Analyzes Linux executables and runtime traces to infer snap confinement
requirements. It maps observed syscall behavior, accessed
filesystem/device/network paths, and OCI capability hints to suggested snap
plugs, layout entries, and explicit unmappable-path warnings. It can also patch
an existing `snapcraft.yaml`.

Key contents:

- `scripts/apply_snapcraft_suggestions.py` — apply suggested plugs/layouts to `snapcraft.yaml`.
- `references/` — syscall and path heuristics, capability/interface map, layout
  constraints, mount mapping, and an analysis checklist.

### `snap-iteration-workflow`

Guides the repeatable snap packaging iteration cycle: LXD environment setup,
building with `snapcraft --use-lxd --build-for <arch>`, installing and running
inside an isolated environment, and verifying through devmode and strict
confinement. It enforces a hard rule: **the host is never used for building or
testing** — builds use LXD and runtime validation uses an
architecture-compatible isolated environment.

Key contents:

- `references/build-environments.md` — LXD, remotes, QEMU/binfmt, and emulation options.
- `references/install-and-verify.md` — install, run, snappy-debug, and confinement hardening.

## Skill structure

Each skill follows the Agent Skills layout:

- `SKILL.md` — frontmatter (name, description, license, metadata) plus the skill instructions.
- `references/` — supporting documentation the agent can consult.
- `scripts/` — helper scripts the agent can run.
- `assets/` — templates and other files used during packaging.

## License

All skills in this repository are licensed under Apache-2.0.
