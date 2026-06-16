#!/usr/bin/env python3
"""
apply_snapcraft_suggestions.py — Adds inferred plugs and layouts to an existing snapcraft.yaml.

Usage:
    python3 scripts/apply_snapcraft_suggestions.py \\
        --snapcraft snap/snapcraft.yaml \\
        --app my-app \\
        --plugs network network-bind hardware-observe \\
        --layout /var/lib/myapp '$SNAP_COMMON/myapp' \\
        --layout /usr/lib/mylib '$SNAP/lib/mylib'

Options:
    --snapcraft PATH    Path to snapcraft.yaml (auto-detected if omitted)
    --app NAME          App to add plugs to (uses first app found if omitted)
    --plugs NAME ...    One or more snap interface names to add as plugs
    --layout PATH BIND  Add a layout entry binding PATH to BIND (repeatable)
    --dry-run           Print the patched YAML without writing to disk
    --no-backup         Skip creating the .bak backup file

Exit codes:
    0  Success (or --dry-run completed)
    1  snapcraft.yaml not found or unreadable
    2  Named --app not found in snapcraft.yaml
    3  YAML parse error
    4  Missing required arguments (need at least --plugs or --layout)

Layout target paths that violate the snapcraft layouts specification are
skipped with a WARNING rather than causing a hard failure. Forbidden paths
are listed in references/layout-constraints.md.

WARNING: this script uses PyYAML which does not preserve comments or custom
         formatting. A .bak backup is created before writing by default.
         Use 'diff snapcraft.yaml.bak snapcraft.yaml' to review changes.
"""

import argparse
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# YAML backend — prefer ruamel.yaml (preserves comments), fall back to PyYAML
# ---------------------------------------------------------------------------
try:
    from ruamel.yaml import YAML as _RYAML

    _ruamel_available = True

    def _load_yaml(text: str):
        ry = _RYAML()
        ry.preserve_quotes = True
        return ry.load(text), ry

    def _dump_yaml(data, stream, backend) -> None:
        backend.dump(data, stream)

except ImportError:
    _ruamel_available = False
    try:
        import yaml as _pyyaml


        def _load_yaml(text: str):  # type: ignore[misc]
            return _pyyaml.safe_load(text), None

        def _dump_yaml(data, stream, backend) -> None:  # type: ignore[misc]
            _pyyaml.dump(data, stream, default_flow_style=False, allow_unicode=True, sort_keys=False)

    except ImportError:
        print(
            "ERROR: Neither ruamel.yaml nor PyYAML is installed.\n"
            "  Install one with:  pip install ruamel.yaml\n"
            "  or:                pip install pyyaml",
            file=sys.stderr,
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# Layout target path validation
# Source: https://documentation.ubuntu.com/snapcraft/stable/reference/layouts/#requirements
# ---------------------------------------------------------------------------

# Paths explicitly forbidden by the snapcraft layouts specification.
FORBIDDEN_LAYOUT_TARGETS: frozenset[str] = frozenset([
    "/boot",
    "/dev",
    "/home",
    "/lib/firmware",
    "/usr/lib/firmware",
    "/lib/modules",
    "/usr/lib/modules",
    "/lost+found",
    "/media",
    "/proc",
    "/run",
    "/var/run",
    "/sys",
    "/tmp",
    "/var/lib/snapd",
    "/var/snap",
])


def validate_layout_target(path: str) -> list[str]:
    """
    Return a list of human-readable violation strings for the given layout
    target path.  An empty list means the path is valid.

    Rules enforced:
      1. Path must be absolute (starts with '/').
      2. Path must not exactly match or be a subdirectory of any entry in
         FORBIDDEN_LAYOUT_TARGETS (the denylist applies to the entire subtree).
      3. Path must not be a direct child of '/' (depth == 1 component).
    """
    errors: list[str] = []

    if not path.startswith("/"):
        errors.append(f"  '{path}': not an absolute path — must start with '/'.")
        return errors  # remaining checks require absolute path

    for forbidden in FORBIDDEN_LAYOUT_TARGETS:
        if path == forbidden or path.startswith(forbidden + "/"):
            errors.append(
                f"  '{path}': falls within the snapcraft layouts denylist entry '{forbidden}'.\n"
                "    The denylist applies to the listed paths and all their subdirectories.\n"
                "    See references/layout-constraints.md §2 for the full list and §4 for workarounds."
            )
            break  # one match is enough

    components = [p for p in path.split("/") if p]
    if len(components) == 1:
        errors.append(
            f"  '{path}': is a direct child of '/' — root-level layout targets are not allowed.\n"
            "    See references/layout-constraints.md §4 for workarounds (patchelf, env-var overrides)."
        )

    return errors


def filter_valid_layouts(layouts: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Return only the layout pairs whose target path is valid.
    Invalid targets are printed as WARNINGs and silently dropped so the script
    can still apply the remaining (valid) entries.
    """
    valid: list[tuple[str, str]] = []
    for dest, bind in layouts:
        errors = validate_layout_target(dest)
        if errors:
            print(
                f"WARNING: Skipping layout '{dest}' — invalid target path:\n"
                + "\n".join(errors)
                + "\n  If the application lets you configure this path (e.g. via an"
                + "\n  environment variable or config file), you can ignore this warning"
                + "\n  and redirect the path at runtime instead:"
                + "\n    - For writable paths: point to $SNAP_COMMON/<subpath> (persists"
                + "\n      across upgrades) or $HOME/<subpath> (per-user, needs home plug)."
                + "\n    - For read-only paths shipped in the snap: point to $SNAP/<subpath>."
                + "\n  Otherwise, consult references/layout-constraints.md for alternatives.",
                file=sys.stderr,
            )
        else:
            valid.append((dest, bind))
    return valid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CANDIDATE_PATHS = [
    "snapcraft.yaml",
    "snap/snapcraft.yaml",
    ".snapcraft.yaml",
]


def find_snapcraft(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            print(f"ERROR: snapcraft.yaml not found at: {explicit}", file=sys.stderr)
            sys.exit(1)
        return p
    for candidate in CANDIDATE_PATHS:
        p = Path(candidate)
        if p.is_file():
            return p
    print(
        "ERROR: Could not find snapcraft.yaml. Tried:\n"
        + "\n".join(f"  {c}" for c in CANDIDATE_PATHS)
        + "\nPass --snapcraft <path> to specify the location.",
        file=sys.stderr,
    )
    sys.exit(1)


def ensure_list(obj, key: str) -> list:
    """Return obj[key] as a list, creating it if absent."""
    if key not in obj or obj[key] is None:
        obj[key] = []
    elif not isinstance(obj[key], list):
        # Shouldn't happen in valid snapcraft.yaml but be safe
        obj[key] = list(obj[key])
    return obj[key]


def ensure_dict(obj, key: str) -> dict:
    """Return obj[key] as a dict, creating it if absent."""
    if key not in obj or obj[key] is None:
        obj[key] = {}
    return obj[key]


# ---------------------------------------------------------------------------
# Core patch logic
# ---------------------------------------------------------------------------

def patch(data: dict, app_name: str | None, plugs: list[str], layouts: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """
    Mutate *data* in-place, adding plugs and layout entries.

    Returns (added_plugs, added_layouts) — the items actually inserted
    (already-present items are silently skipped).
    """
    apps = data.get("apps", {})
    if not apps:
        print("ERROR: snapcraft.yaml has no 'apps' section.", file=sys.stderr)
        sys.exit(2)

    # Resolve app name
    if app_name is None:
        app_name = next(iter(apps))
        print(f"INFO: --app not specified, using first app: '{app_name}'", file=sys.stderr)
    elif app_name not in apps:
        available = ", ".join(apps.keys())
        print(
            f"ERROR: App '{app_name}' not found in snapcraft.yaml.\n"
            f"  Available apps: {available}",
            file=sys.stderr,
        )
        sys.exit(2)

    app = apps[app_name]

    # --- Add plugs ---
    added_plugs: list[str] = []
    if plugs:
        current_plugs = ensure_list(app, "plugs")
        for plug in plugs:
            if plug not in current_plugs:
                current_plugs.append(plug)
                added_plugs.append(plug)

    # --- Add layouts ---
    added_layouts: list[str] = []
    if layouts:
        top_layout = ensure_dict(data, "layout")
        for dest, bind_target in layouts:
            if dest in top_layout:
                print(f"INFO: layout '{dest}' already present — skipping.", file=sys.stderr)
            else:
                top_layout[dest] = {"bind": bind_target}
                added_layouts.append(dest)

    return added_plugs, added_layouts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class LayoutAction(argparse.Action):
    """Collect --layout PATH BIND pairs into a list of (path, bind) tuples."""

    def __call__(self, parser, namespace, values, option_string=None):
        items = getattr(namespace, self.dest, None) or []
        items.append(tuple(values))
        setattr(namespace, self.dest, items)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Patch snapcraft.yaml with OCI-derived plugs and layout entries.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--snapcraft", metavar="PATH", help="Path to snapcraft.yaml (auto-detected if omitted)")
    p.add_argument("--app", metavar="NAME", help="App name to add plugs to (uses first app if omitted)")
    p.add_argument("--plugs", nargs="+", metavar="NAME", default=[], help="Snap interface names to add as plugs")
    p.add_argument(
        "--layout",
        nargs=2,
        metavar=("PATH", "BIND"),
        action=LayoutAction,
        dest="layouts",
        default=[],
        help="Add a layout entry: --layout /dest $SNAP/src  (repeatable)",
    )
    p.add_argument("--dry-run", action="store_true", help="Print patched YAML without writing")
    p.add_argument("--no-backup", action="store_true", help="Skip .bak backup")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    if not args.plugs and not args.layouts:
        print("ERROR: Provide at least --plugs or --layout.", file=sys.stderr)
        sys.exit(4)

    # Filter layout targets — invalid ones are warned and skipped.
    if args.layouts:
        args.layouts = filter_valid_layouts(args.layouts)

    snapcraft_path = find_snapcraft(args.snapcraft)
    raw = snapcraft_path.read_text(encoding="utf-8")

    try:
        data, backend = _load_yaml(raw)
    except Exception as exc:
        print(f"ERROR: Failed to parse {snapcraft_path}: {exc}", file=sys.stderr)
        sys.exit(3)

    if not _ruamel_available:
        print(
            "WARNING: ruamel.yaml not found; using PyYAML. Comments and custom "
            "formatting will not be preserved.\n"
            "  Install ruamel.yaml for lossless editing:  pip install ruamel.yaml",
            file=sys.stderr,
        )

    added_plugs, added_layouts = patch(data, args.app, args.plugs, args.layouts)

    # Serialise
    import io
    buf = io.StringIO()
    _dump_yaml(data, buf, backend)
    patched_yaml = buf.getvalue()

    if args.dry_run:
        print(patched_yaml)
        print(f"\n# Dry run — {snapcraft_path} was NOT modified.", file=sys.stderr)
        return

    # Backup
    if not args.no_backup:
        backup = snapcraft_path.with_suffix(snapcraft_path.suffix + ".bak")
        shutil.copy2(snapcraft_path, backup)
        print(f"INFO: Backup saved to {backup}", file=sys.stderr)

    snapcraft_path.write_text(patched_yaml, encoding="utf-8")

    # Report
    print(f"SUCCESS: Patched {snapcraft_path}")
    if added_plugs:
        print(f"  Added plugs:   {', '.join(added_plugs)}")
    if added_layouts:
        print(f"  Added layouts: {', '.join(added_layouts)}")
    skipped_plugs = [p for p in args.plugs if p not in added_plugs]
    skipped_layouts = [d for d, _ in args.layouts if d not in added_layouts]
    if skipped_plugs:
        print(f"  Already present (skipped): plugs — {', '.join(skipped_plugs)}")
    if skipped_layouts:
        print(f"  Already present (skipped): layouts — {', '.join(skipped_layouts)}")


if __name__ == "__main__":
    main()
