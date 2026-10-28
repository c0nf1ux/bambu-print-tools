"""Verify an STL (usually straight out of `openscad --render -o out.stl`)
before trusting it enough to slice/print. Ported from
a1-mini-painting-station's scripts/check_module.py plus the trimesh
snippets documented in that project's memory, generalized away from the
A1 mini's specific 180mm bed.

Render a PNG alongside the STL and actually look at it - none of these
checks replace that (caught real bugs twice in painting-station that these
numeric checks alone would have missed: overlapping wells, a step that
*looked* fine from the outside but had zero real depth):

    openscad -o preview.png --imgsize=1000,700 --render model.scad

Everything above catches geometric correctness bugs (does the design mean
what you think it means). Everything below estimates whether a correct
design will actually survive being printed: overhangs, bridges, and walls
thinner than the nozzle can extrude. None of these are a physics
simulator. They're the same kind of cheap static-geometry heuristic as
the checks above, not a prediction of warping, adhesion, or anything
thermal - see DECISIONS.md for why a real simulator isn't worth building
here.
"""
import numpy as np
import trimesh

# Build-up axis is always +Z for this toolkit's printers (standard FDM
# gantry orientation). If that ever stops being true, overhang/bridge
# checks would need this as a parameter.
_BUILD_UP = np.array([0.0, 0.0, 1.0])

# trimesh's non-pyembree ray intersector builds a ray-x-candidate-triangle
# array internally; on a mesh with a few tens of thousands of faces this
# can blow past available memory in one call (observed: a 63k-face statue
# mesh tried to allocate 4.75GiB in a single intersects_location call).
# Batching bounds peak memory per call regardless of mesh complexity, at
# the cost of some speed.
_RAY_BATCH_SIZE = 2000
_RAY_ORIGIN_EPSILON = 1e-4


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
    fused into one physical piece - two touching-but-disjoint watertight
    shells both read is_watertight=True. OpenSCAD's own CGAL console
    output ("Volumes: N") isn't reliable either - a properly-fused single
    solid can report "Volumes: 2". The real check is connected components."""
    return len(mesh.split(only_watertight=False)) == 1


def contains_points(mesh: trimesh.Trimesh, points: list[tuple[float, float, float]]) -> list[bool]:
    """Point-containment check for cavities near a connector edge/slot cut
    - watertight checks do NOT catch a tunnel (a tunnel through a wall is
    still a closed, watertight manifold). Work out the real nearest-
    approach geometry between the two features first; don't guess a point
    near where you think the risk is."""
    return list(mesh.contains(points))


def _overhang_angle_deg(normal: np.ndarray) -> float:
    """Angle from vertical, the standard slicer convention: 0deg is a
    straight vertical wall (always printable), 90deg is a flat horizontal
    ceiling (needs support / is a bridge). Only meaningful for
    downward-facing normals; a face whose normal points up or sideways-up
    returns a value <= 0, which will never trip a positive threshold."""
    down_component = float(np.clip(normal @ -_BUILD_UP, -1.0, 1.0))
    theta = np.degrees(np.arccos(down_component))
    return 90.0 - theta


def _facet_regions(mesh: trimesh.Trimesh) -> list[tuple[np.ndarray, np.ndarray, float]]:
    """Coplanar facets, plus any face `mesh.facets` left out entirely.

    `mesh.facets` only groups a face with at least one coplanar neighbor -
    a single isolated triangle with no adjacent coplanar face is silently
    absent from both `mesh.facets` and `mesh.facets_normal`, not present
    as a size-1 group. Found by this module's own tests: a standalone
    single-triangle test mesh produced `facets == []`. Iterating
    `mesh.facets_normal` alone would make check_overhangs/check_bridges
    blind to exactly the simplest possible overhang. Each region is
    (face_indices, normal, area_mm2)."""
    regions = []
    grouped = set()
    for face_indices, normal, area in zip(mesh.facets, mesh.facets_normal, mesh.facets_area):
        face_indices = np.asarray(face_indices)
        regions.append((face_indices, normal, float(area)))
        grouped.update(face_indices.tolist())
    for i in range(len(mesh.faces)):
        if i not in grouped:
            regions.append((np.array([i]), mesh.face_normals[i], float(mesh.area_faces[i])))
    return regions


def _on_bed(mesh: trimesh.Trimesh, vert_idx: np.ndarray, tolerance_mm: float) -> bool:
    """True if every vertex in this region sits within tolerance_mm of the
    mesh's own lowest point - i.e. this region is the part of the model
    actually resting on the build plate, which is supported by the plate
    itself regardless of its angle. Found by testing check_overhangs
    against a real printed module: its flat base (normal straight down,
    0mm off the plate) was flagged as a 90deg overhang on every single
    model, which is wrong - the build plate *is* the support there."""
    z_min = mesh.bounds[0][2]
    return bool(np.all(mesh.vertices[vert_idx][:, 2] <= z_min + tolerance_mm))


def check_overhangs(
    mesh: trimesh.Trimesh,
    max_angle_deg: float = 45.0,
    exclude_bed_contact: bool = True,
    bed_tolerance_mm: float = 0.05,
) -> list[dict]:
    """Flags coplanar regions (see _facet_regions - a flat shelf is
    usually several triangles merged into one facet, and triangulation
    density shouldn't change what gets flagged) whose overhang angle
    exceeds max_angle_deg, the angle past which FDM printers typically
    need support material. 45deg is a common conservative default; a
    well-tuned printer/filament can sometimes manage steeper, unsupported
    overhangs.

    exclude_bed_contact (default True) skips any region resting on the
    model's own lowest point, on the assumption this mesh will be printed
    sitting on a build plate at that height - see _on_bed. Pass False if
    you're checking a sub-component that won't actually touch the bed."""
    flagged = []
    for face_indices, normal, area in _facet_regions(mesh):
        angle = _overhang_angle_deg(normal)
        if angle <= max_angle_deg:
            continue
        if exclude_bed_contact and _on_bed(mesh, np.unique(mesh.faces[face_indices]), bed_tolerance_mm):
            continue
        flagged.append({
            "face_indices": face_indices.tolist(),
            "overhang_angle_deg": angle,
            "area_mm2": area,
        })
    return flagged


def check_bridges(
    mesh: trimesh.Trimesh,
    max_span_mm: float = 8.0,
    ceiling_angle_deg: float = 80.0,
    exclude_bed_contact: bool = True,
    bed_tolerance_mm: float = 0.05,
) -> list[dict]:
    """Flags near-horizontal downward-facing regions (closer to a flat
    ceiling than a steep overhang) whose own bounding-box diagonal exceeds
    max_span_mm, the rough safe unsupported span for PLA.

    This approximates the bridge span as the region's own size. It does
    NOT ray-cast to confirm the void beneath actually extends that far -
    a flagged region sitting directly above something solid just below it
    is a false positive, and a real bridge built from several small
    faces that aren't coplanar enough to merge into one region could be a
    false negative. Treat a flag here as worth a second look, not a
    certainty - same spirit as check_overhangs, just a cruder proxy.

    exclude_bed_contact: see check_overhangs - the model's own base is
    not a bridge, the plate is holding it up."""
    flagged = []
    for face_indices, normal, _area in _facet_regions(mesh):
        angle = _overhang_angle_deg(normal)
        if angle < ceiling_angle_deg:
            continue
        vert_idx = np.unique(mesh.faces[face_indices])
        if exclude_bed_contact and _on_bed(mesh, vert_idx, bed_tolerance_mm):
            continue
        verts = mesh.vertices[vert_idx]
        span = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0)))
        if span > max_span_mm:
            flagged.append({
                "face_indices": face_indices.tolist(),
                "span_mm": span,
                "overhang_angle_deg": angle,
            })
    return flagged


def compute_face_thickness(mesh: trimesh.Trimesh) -> np.ndarray:
    """For each face, cast a ray inward along its negative normal and
    measure the distance to the nearest opposite-facing intersection.
    Returns one value per face, in the mesh's native units. NaN where no
    intersection is found (e.g. non-manifold or open geometry).

    Ported from print-scale-guard (which keeps a thin re-export for its
    own API, see that repo's thickness.py) - same technique, same batching
    fix for the same real memory-blowup bug found there."""
    origins = mesh.triangles_center - mesh.face_normals * _RAY_ORIGIN_EPSILON
    directions = -mesh.face_normals

    result = np.full(len(mesh.faces), np.nan)
    for start in range(0, len(mesh.faces), _RAY_BATCH_SIZE):
        end = start + _RAY_BATCH_SIZE
        batch_origins = origins[start:end]

        locations, index_ray, _ = mesh.ray.intersects_location(
            ray_origins=batch_origins,
            ray_directions=directions[start:end],
            multiple_hits=False,
        )

        if len(index_ray) > 0:
            distances = np.linalg.norm(locations - batch_origins[index_ray], axis=1)
            result[start + index_ray] = distances

    return result


def check_wall_thickness(mesh: trimesh.Trimesh, floor_mm: float) -> dict:
    """Flags faces thinner than floor_mm (typically ~2x nozzle diameter,
    so a wall prints as more than a single perimeter line) using the same
    inward ray-cast as print-scale-guard. Answers "does this wall survive
    as designed", not "how far can I scale this down" - see
    print_scale_guard.scaling for that question."""
    face_thickness = compute_face_thickness(mesh)
    valid = face_thickness[~np.isnan(face_thickness)]
    too_thin = int(np.sum(valid < floor_mm)) if valid.size else 0
    return {
        "min_thickness_mm": float(np.min(valid)) if valid.size else None,
        "thin_face_count": too_thin,
        "faces_checked": int(valid.size),
    }


def check_module(
    stl_path: str,
    bed_mm: tuple[float, float, float],
    require_watertight: bool = False,
    max_overhang_deg: float | None = None,
    max_bridge_mm: float | None = None,
    wall_floor_mm: float | None = None,
) -> dict:
    """Geometric-correctness checks always run. The printability checks
    (overhangs, bridges, wall thickness) only run when their threshold is
    given, so existing callers that just want bed-fit/watertight keep
    getting exactly the same result shape as before."""
    mesh = trimesh.load(stl_path)
    result = {
        "bed_fit_failures": check_bed_fit(mesh, bed_mm),
        "watertight": is_watertight(mesh),
    }
    if require_watertight and not result["watertight"]:
        result["bed_fit_failures"].append("not watertight")
    if max_overhang_deg is not None:
        result["overhangs"] = check_overhangs(mesh, max_overhang_deg)
    if max_bridge_mm is not None:
        result["bridges"] = check_bridges(mesh, max_bridge_mm)
    if wall_floor_mm is not None:
        result["wall_thickness"] = check_wall_thickness(mesh, wall_floor_mm)
    return result
