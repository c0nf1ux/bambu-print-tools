import trimesh

from bambu_print_tools.mesh_check import (
    check_bed_fit,
    contains_points,
    is_truly_fused,
    is_watertight,
)

BED_MM = (180.0, 180.0, 180.0)


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
