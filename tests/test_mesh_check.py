import numpy as np
import pytest
import trimesh

from bambu_print_tools.mesh_check import (
    check_bed_fit,
    check_bridges,
    check_module,
    check_overhangs,
    check_wall_thickness,
    compute_face_thickness,
    contains_points,
    is_truly_fused,
    is_watertight,
)

BED_MM = (180.0, 180.0, 180.0)


def _single_triangle_mesh(normal: tuple[float, float, float]) -> trimesh.Trimesh:
    """Builds a one-facet mesh with an exactly-known face normal, so
    overhang-angle math can be tested against a precise expected value
    instead of just a qualitative direction."""
    n = np.array(normal, dtype=float)
    n /= np.linalg.norm(n)
    # Any vector not parallel to n works as the first in-plane axis.
    u = np.array([0.0, 1.0, 0.0]) if abs(n[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = u - n * np.dot(u, n)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)  # (u x v) == n by construction

    a = np.array([0.0, 0.0, 0.0])
    b = a + u
    c = a + v
    return trimesh.Trimesh(vertices=[a, b, c], faces=[[0, 1, 2]], process=False)


def test_check_bed_fit_passes_for_box_within_bed():
    mesh = trimesh.creation.box(extents=(100, 100, 100))
    assert check_bed_fit(mesh, BED_MM) == []


def test_check_bed_fit_flags_oversized_axis():
    mesh = trimesh.creation.box(extents=(200, 100, 100))
    failures = check_bed_fit(mesh, BED_MM)
    assert len(failures) == 1
    assert "X" in failures[0]


def test_box_is_watertight():
    mesh = trimesh.creation.box(extents=(10, 10, 10))
    assert is_watertight(mesh) is True


def test_single_box_is_truly_fused():
    mesh = trimesh.creation.box(extents=(10, 10, 10))
    assert is_truly_fused(mesh) is True


def test_two_disjoint_watertight_shells_are_not_truly_fused():
    """Reproduces the real gotcha documented in painting-station memory:
    two touching-but-disjoint watertight shells both individually read
    is_watertight=True, but they're not one physical piece. Here the two
    boxes don't even touch, which is the clearest version of the same
    failure mode a CSG union() can silently produce."""
    box_a = trimesh.creation.box(extents=(10, 10, 10))
    box_b = trimesh.creation.box(extents=(10, 10, 10))
    box_b.apply_translation((100, 0, 0))
    combined = trimesh.util.concatenate([box_a, box_b])

    assert is_watertight(combined) is True  # each shell alone is watertight
    assert is_truly_fused(combined) is False  # but they're two separate pieces


def test_contains_points_distinguishes_inside_from_outside():
    mesh = trimesh.creation.box(extents=(10, 10, 10))
    results = contains_points(mesh, [(0, 0, 0), (100, 100, 100)])
    assert results == [True, False]


# --- check_overhangs ---------------------------------------------------
#
# These single-triangle fixtures are the entire mesh, so they're always
# "resting on their own lowest point" by construction - pass
# exclude_bed_contact=False to isolate the angle math itself from the
# separate bed-exclusion behavior, which gets its own tests below.

def test_vertical_wall_is_never_an_overhang():
    mesh = _single_triangle_mesh((1, 0, 0))  # purely horizontal normal
    assert check_overhangs(mesh, max_angle_deg=0.0, exclude_bed_contact=False) == []


def test_flat_downward_ceiling_is_the_worst_case_overhang():
    mesh = _single_triangle_mesh((0, 0, -1))  # straight down
    flagged = check_overhangs(mesh, max_angle_deg=45.0, exclude_bed_contact=False)
    assert len(flagged) == 1
    assert flagged[0]["overhang_angle_deg"] == pytest.approx(90.0)


def test_upward_face_is_never_an_overhang():
    mesh = _single_triangle_mesh((0, 0, 1))  # straight up
    assert check_overhangs(mesh, max_angle_deg=0.0, exclude_bed_contact=False) == []


def test_overhang_below_threshold_is_not_flagged():
    # theta=60deg from straight-down -> overhang_angle = 90-60 = 30deg
    mesh = _single_triangle_mesh((np.sin(np.radians(60)), 0, -np.cos(np.radians(60))))
    assert check_overhangs(mesh, max_angle_deg=45.0, exclude_bed_contact=False) == []


def test_overhang_above_threshold_is_flagged():
    # theta=30deg from straight-down -> overhang_angle = 90-30 = 60deg
    mesh = _single_triangle_mesh((np.sin(np.radians(30)), 0, -np.cos(np.radians(30))))
    flagged = check_overhangs(mesh, max_angle_deg=45.0, exclude_bed_contact=False)
    assert len(flagged) == 1
    assert flagged[0]["overhang_angle_deg"] == pytest.approx(60.0, abs=0.01)


# --- bed-contact exclusion (both check_overhangs and check_bridges) -----
#
# Found by running check_overhangs against a real printed module: its
# flat base, resting on the build plate, was flagged as a 90deg overhang
# on every single model - the plate *is* the support there, so this isn't
# optional behavior, it's a correctness fix for real use.

def test_overhang_touching_bed_is_excluded_by_default():
    mesh = trimesh.creation.box(extents=(20, 20, 2))  # bottom face rests on the plate
    assert check_overhangs(mesh, max_angle_deg=45.0) == []


def test_overhang_touching_bed_is_flagged_when_exclusion_disabled():
    mesh = trimesh.creation.box(extents=(20, 20, 2))
    flagged = check_overhangs(mesh, max_angle_deg=45.0, exclude_bed_contact=False)
    assert len(flagged) == 1
    assert flagged[0]["overhang_angle_deg"] == pytest.approx(90.0)


def test_overhang_not_touching_bed_is_still_flagged():
    # A floating shelf well above the mesh's own lowest point - a real
    # overhang the bed-exclusion logic must not swallow.
    leg = trimesh.creation.box(extents=(2, 2, 20))
    leg.apply_translation((0, 0, 10))  # spans z: 0 to 20, touches the plate
    shelf = trimesh.creation.box(extents=(20, 20, 2))
    shelf.apply_translation((0, 0, 21))  # spans z: 20 to 22, floating above the leg
    combined = trimesh.util.concatenate([leg, shelf])

    flagged = check_overhangs(combined, max_angle_deg=45.0)
    assert any(f["overhang_angle_deg"] == pytest.approx(90.0) for f in flagged)


# --- check_bridges -------------------------------------------------------

def test_small_flat_ceiling_is_not_a_flagged_bridge():
    mesh = trimesh.creation.box(extents=(5, 5, 2))  # bottom facet spans ~7mm diagonal
    assert check_bridges(mesh, max_span_mm=8.0, exclude_bed_contact=False) == []


def test_wide_flat_ceiling_is_flagged_as_a_likely_bridge():
    mesh = trimesh.creation.box(extents=(20, 20, 2))  # bottom facet spans ~28mm diagonal
    flagged = check_bridges(mesh, max_span_mm=8.0, exclude_bed_contact=False)
    assert len(flagged) == 1
    assert flagged[0]["span_mm"] > 8.0


def test_bridge_touching_bed_is_excluded_by_default():
    mesh = trimesh.creation.box(extents=(20, 20, 2))
    assert check_bridges(mesh, max_span_mm=8.0) == []


def test_vertical_side_wall_is_never_a_bridge_candidate():
    # A tall column: top/bottom caps are small (4x4, under the span
    # threshold) so this isolates the real claim - large VERTICAL side
    # walls (4x20, normal horizontal) are never bridge candidates no
    # matter how big, because check_bridges only looks at near-horizontal
    # facets in the first place.
    mesh = trimesh.creation.box(extents=(4, 4, 20))
    assert check_bridges(mesh, max_span_mm=8.0) == []


# --- compute_face_thickness / check_wall_thickness -----------------------

def test_thin_box_measures_thin_along_its_short_axis():
    mesh = trimesh.creation.box(extents=(10, 10, 1))
    thickness = compute_face_thickness(mesh)
    top_bottom = thickness[np.abs(mesh.face_normals[:, 2]) > 0.9]
    assert np.nanmax(top_bottom) == pytest.approx(1.0, abs=0.01)


def test_thick_box_measures_thick_along_its_long_axis():
    mesh = trimesh.creation.box(extents=(10, 10, 1))
    thickness = compute_face_thickness(mesh)
    sides = thickness[np.abs(mesh.face_normals[:, 2]) < 0.1]
    assert np.nanmin(sides) == pytest.approx(10.0, abs=0.01)


def test_compute_face_thickness_handles_many_faces_without_memory_blowup():
    # Regression test ported from print-scale-guard: trimesh's
    # non-pyembree ray intersector allocated 4.75GiB in one call against a
    # real 63k-face statue mesh - this icosphere (5120 faces, spans
    # multiple ray batches at the current batch size of 2000) is real
    # generated geometry, enough to exercise the batching loop without a
    # third-party model checked into the repo.
    mesh = trimesh.creation.icosphere(subdivisions=4)
    result = compute_face_thickness(mesh)
    assert result.shape == (len(mesh.faces),)
    assert not np.isnan(result).any()
    # A ray through the center of a unit sphere should measure ~2x radius.
    assert result == pytest.approx(2.0, abs=0.01)


def test_check_wall_thickness_flags_faces_below_floor():
    mesh = trimesh.creation.box(extents=(10, 10, 1))  # top/bottom are 1mm thick
    result = check_wall_thickness(mesh, floor_mm=0.8)
    assert result["min_thickness_mm"] == pytest.approx(1.0, abs=0.01)
    assert result["thin_face_count"] == 0  # 1.0mm is above the 0.8mm floor


def test_check_wall_thickness_counts_faces_thinner_than_floor():
    mesh = trimesh.creation.box(extents=(10, 10, 1))
    result = check_wall_thickness(mesh, floor_mm=1.2)  # now the 1mm faces fail
    assert result["thin_face_count"] > 0


# --- check_module (aggregate) --------------------------------------------

def test_check_module_omits_printability_keys_by_default(tmp_path):
    """Existing callers that only care about bed-fit/watertight keep
    getting exactly the same result shape as before these checks existed."""
    mesh = trimesh.creation.box(extents=(10, 10, 10))
    stl_path = tmp_path / "box.stl"
    mesh.export(stl_path)

    result = check_module(str(stl_path), bed_mm=BED_MM)

    assert set(result) == {"bed_fit_failures", "watertight"}


def test_check_module_includes_printability_keys_when_requested(tmp_path):
    # A floating shelf well above the model's own base, so the overhang/
    # bridge checks have something real to flag (the base itself is
    # excluded by default - see the bed-contact exclusion tests above).
    leg = trimesh.creation.box(extents=(2, 2, 20))
    leg.apply_translation((0, 0, 10))
    shelf = trimesh.creation.box(extents=(20, 20, 1))
    shelf.apply_translation((0, 0, 20.5))
    mesh = trimesh.util.concatenate([leg, shelf])
    stl_path = tmp_path / "shelf.stl"
    mesh.export(stl_path)

    result = check_module(
        str(stl_path), bed_mm=BED_MM,
        max_overhang_deg=45.0, max_bridge_mm=8.0, wall_floor_mm=1.5,
    )

    assert len(result["overhangs"]) > 0
    assert len(result["bridges"]) > 0
    assert result["wall_thickness"]["thin_face_count"] > 0
