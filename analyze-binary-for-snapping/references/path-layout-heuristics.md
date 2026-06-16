# Path Classification → Layout Heuristics

Use this guide to map observed/static paths to layout candidates and to decide
whether a path is likely writable or read-only.

Always validate target paths with `references/layout-constraints.md`.

---

## Classification Buckets

1. **Read-only shipped content**
   - Typical mapping: `$SNAP/...`
2. **Writable persistent content**
   - Typical mapping: `$SNAP_COMMON/...`
3. **Writable ephemeral content**
   - Prefer runtime redirects (`$XDG_RUNTIME_DIR/...` or `$SNAP_DATA/run/...`)
4. **Unknown / ambiguous**
   - Do not auto-map; report for manual review

---

## Writable vs Read-only Heuristics

## High-confidence writable signals

- Runtime trace shows:
  - `open(..., O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND, ...)`
  - `mkdir`, `rename`, `unlink`, `ftruncate`, `link`, `symlink`
- Path contains state-like patterns:
  - `/var/lib/<app>`, `/var/cache/<app>`, `/var/log/<app>`
  - `.db`, `.sqlite`, `.lock`, `cache`, `state`, `session`

## High-confidence read-only signals

- Runtime trace shows mostly:
  - `open(..., O_RDONLY, ...)`, `stat`, `access`
- Path resembles shipped artifacts:
  - `/usr/lib/...`, `/usr/share/...`, `/etc/<app>/defaults...`
- Path appears in ELF interpreter / RUNPATH

## Medium-confidence signals

- Only static strings evidence, no runtime confirmation.
- Path prefix suggests intent but no write syscall observed.

## Low-confidence signals

- Generic top-level paths with no context.
- Paths appearing once in strings with no corroboration.

---

## Mapping Rules

| Path class | Recommended mapping | Notes |
|---|---|---|
| Read-only shipped | `layout: <target> -> bind: $SNAP/<subpath>` | Use when content is packaged in snap |
| Writable persistent | `layout: <target> -> bind: $SNAP_COMMON/<subpath>` | Preserves across upgrades |
| Writable ephemeral | Prefer env/config redirect to `$XDG_RUNTIME_DIR` or `$SNAP_DATA/run` | Layout may be forbidden for `/run`, `/tmp` |
| Unknown | No auto layout | Report in unmappable/needs-review list |

---

## Common Path Patterns

| Target path | Default interpretation | Typical action |
|---|---|---|
| `/etc/<app>` | config (can be RO or RW) | RO -> `$SNAP/etc/<app>`; RW -> `$SNAP_COMMON/etc/<app>` |
| `/var/lib/<app>` | persistent state | `$SNAP_COMMON/<app>` |
| `/var/cache/<app>` | cache | `$SNAP_COMMON/cache/<app>` or app-level override |
| `/usr/lib/<name>` | libraries/runtime assets | `$SNAP/lib/<name>` or packaged equivalent |
| `/usr/share/<app>` | read-only data | `$SNAP/usr/share/<app>` |
| `/run/<app>` | runtime ephemeral | usually env redirect; often not layout-mappable |
| `/tmp/<app>` | temporary scratch | usually env redirect; often not layout-mappable |
| `/<single-component>` (`/data`, `/certs`, `/nix`) | root-level target | forbidden layout target; use override/patch strategy |

---

## Forbidden / Unmappable Handling

When a target is denylisted or root-level:

1. Do **not** add a layout.
2. Add the item to "paths that could not be mapped using layouts".
3. Apply the workaround that matches the path's prefix:

**Path under `/home/`** → the entire `/home/` subtree is forbidden (including
`/home/root`); no layout can be used. Add the `home` plug and configure the app
to redirect to `$HOME/<subpath>` or `$SNAP_USER_COMMON/<subpath>`. See
`references/layout-constraints.md §4a` for the exact steps and wrapper snippet.

**All other forbidden/root-level paths** → configure the application at runtime to
use an explicit `$SNAP_COMMON/<path>` via env var export, config flag, or build-time
patching. See `references/layout-constraints.md §4b` for the option table.

---

## Confidence Scoring Guidance

Use one confidence level per proposed layout:

- **High:** Runtime write/read evidence + clear path purpose.
- **Medium:** Static + partial runtime hints.
- **Low:** Strings-only or contradictory clues.

Auto-apply only high-confidence layouts. Keep medium/low as suggestions unless
the user explicitly requests aggressive application.

