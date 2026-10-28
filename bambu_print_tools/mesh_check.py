"""Verify an STL (usually straight out of `openscad --render -o out.stl`)
before trusting it enough to slice/print. Ported from
a1-mini-painting-station's scripts/check_module.py plus the trimesh
snippets documented in that project's memory, generalized away from the
A1 mini's specific 180mm bed.

Render a PNG alongside the STL and actually look at it — none of these
checks replace that (caught real bugs twice in painting-station that these
numeric checks alone would have missed: overlapping wells, a step that
*looked* fine from the outside but had zero real depth):

    openscad -o preview.png --imgsize=1000,700 --render model.scad
"""
import trimesh


def check_bed_fit(mesh: trimesh.Trimesh, bed_mm: tuple[float, float, float]) -> list[str]:
    """Returns a list of failure reasons; empty list means it fits."""
    extents = mesh.bounding_box.extents
    failures = []
    for label, dim, limit in zip("XYZ", extents, bed_mm):
        if dim > limit:
            failures.append(f"{label} extent {dim:.1f}mm exceeds {limit:.1f}mm bed edge")
    return failures


def is_watertight(mesh: trimesh.Trimesh) -> bool:
    return bool(mesh.is_watertight)


def is_truly_fused(mesh: trimesh.Trimesh) -> bool:
    """`mesh.is_watertight` alone does NOT confirm a CSG union() actually
    fused into one physical piece — two touching-but-disjoint watertight
    shells both read is_watertight=True. OpenSCAD's own CGAL console
    output ("Volumes: N") isn't reliable either — a properly-fused single
    solid can report "Volumes: 2". The real check is connected components."""
    return len(mesh.split(only_watertight=False)) == 1


def contains_points(mesh: trimesh.Trimesh, points: list[tuple[float, float, float]]) -> list[bool]:
    """Point-containment check for cavities near a connector edge/slot cut
    — watertight checks do NOT catch a tunnel (a tunnel through a wall is
    still a closed, watertight manifold). Work out the real nearest-
    approach geometry between the two features first; don't guess a point
    near where you think the risk is."""
    return list(mesh.contains(points))


def check_module(stl_path: str, bed_mm: tuple[float, float, float], require_watertight: bool = False) -> dict:
    mesh = trimesh.load(stl_path)
    result = {
        "bed_fit_failures": check_bed_fit(mesh, bed_mm),
        "watertight": is_watertight(mesh),
    }
    if require_watertight and not result["watertight"]:
        result["bed_fit_failures"].append("not watertight")
    return result
