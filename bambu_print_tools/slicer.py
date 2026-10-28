"""OrcaSlicer CLI preset resolution + headless slicing.

Why this exists: the OrcaSlicer CLI does NOT reliably resolve a bundled
preset's `inherits` chain when passed to --load-settings/--load-filaments.
It silently falls back to bare program defaults for any key the top-level
file doesn't state outright — which can slice at the wrong bed temperature
with no error (see a1-mini-painting-station's print-profiles/README.md for
the real failure this caused: 2026-09-10, bottle_row print #1 peeled off
the plate and spaghetti'd because a flattened fan/temp-only preset silently
dropped to 35C/Cool-Plate defaults instead of the inherited 220C/textured
PEI). Ported from that project's print-profiles/flatten.py, generalized
to take the profile root and overrides as parameters instead of assuming
one project's layout.

Machine presets are the one exception — do NOT flatten them. The stock
nozzle-suffixed machine file (e.g. "Bambu Lab A1 mini 0.4 nozzle.json")
resolves correctly through the CLI on its own, and a flattened one makes
the CLI exit with a bare "found error" on some merged key. Pass the stock
machine file directly; carry any machine-level override (e.g. bed type) in
the flattened *process* JSON instead — the slicer honours it from there.
"""
import glob
import json
import os
import subprocess

# Keys that describe the preset itself, not print settings — dropped from
# the flattened output so the CLI treats it as a plain settings blob.
META_KEYS = {
    "inherits", "from", "setting_id", "filament_id", "instantiation",
    "is_custom_defined", "version", "name",
}


def find_preset(profile_root: str, kind: str, name: str) -> str:
    """kind in {process, filament}. Search the whole profile tree for
    <name>.json under a matching category dir."""
    hits = glob.glob(os.path.join(profile_root, "**", kind, name + ".json"), recursive=True)
    if not hits:
        raise FileNotFoundError(f"{kind} preset not found under {profile_root}: {name}")
    return hits[0]


def resolve(profile_root: str, kind: str, name: str, _seen: set | None = None) -> dict:
    """Return the merged dict for `name` with its full inherits chain
    applied (parent first, child overrides)."""
    _seen = _seen or set()
    if name in _seen:
        raise RuntimeError(f"inherits cycle at {name}")
    _seen.add(name)

    data = json.load(open(find_preset(profile_root, kind, name), encoding="utf-8"))
    parent = data.get("inherits")
    merged = resolve(profile_root, kind, parent, _seen) if parent else {}
    for k, v in data.items():
        if k not in META_KEYS:
            merged[k] = v
    return merged


def flatten_preset(profile_root: str, kind: str, name: str, overrides: dict | None = None) -> dict:
    """resolve() plus the CLI-compatibility fixups (drop `inherits`, mark
    as a user preset so --load-settings accepts it) and project overrides."""
    if kind == "machine":
        raise ValueError(
            "machine presets are not flattened — the stock nozzle-suffixed "
            "file resolves correctly on its own; pass it directly"
        )
    flat = resolve(profile_root, kind, name)
    flat["type"] = kind
    # OrcaSlicer's CLI rejects a settings file with no `from`
    # ("... 's from unsupported"). A flattened, project-owned preset is a
    # user preset by definition.
    flat["from"] = "User"
    flat["name"] = f"{name} (flattened)"
    for k, v in (overrides or {}).items():
        flat[k] = v
    return flat


def write_flat_preset(profile_root: str, kind: str, name: str, out_path: str, overrides: dict | None = None) -> dict:
    flat = flatten_preset(profile_root, kind, name, overrides)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(flat, f, indent="\t", ensure_ascii=False)
    return flat


def slice_stl(
    orca_exe: str,
    stl_path: str,
    machine_preset_path: str,
    flat_process_path: str,
    flat_filament_path: str,
    out_3mf_path: str,
) -> subprocess.CompletedProcess:
    """Run the actual CLI slice. Caller is responsible for having already
    written the flattened process/filament JSON (see write_flat_preset) and
    for any Windows-path conversion (e.g. cygpath -w) needed on this OS —
    kept out of this function so it stays testable without a real OrcaSlicer
    install."""
    return subprocess.run(
        [
            orca_exe,
            stl_path,
            "--slice", "0",
            "--load-settings", f"{machine_preset_path};{flat_process_path}",
            "--load-filaments", flat_filament_path,
            "--export-3mf", out_3mf_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
