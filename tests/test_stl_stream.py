import pytest
import trimesh

from bambu_print_tools.mesh_check import check_module
from bambu_print_tools.stl_stream import load_proxy, stl_face_count, stream_stats

# Real geometry with analytically known answers (no mocks): an icosphere of
# known radius and a box of known extents, exported to a real binary STL.
RADIUS = 20.0


@pytest.fixture()
def sphere_stl(tmp_path):
    mesh = trimesh.creation.icosphere(subdivisions=5, radius=RADIUS)  # 20480 faces
    path = tmp_path / "sphere.stl"
    mesh.export(path)
    return str(path), mesh


@pytest.fixture()
def box_stl(tmp_path):
    mesh = trimesh.creation.box(extents=(10, 20, 30))
    path = tmp_path / "box.stl"
    mesh.export(path)
    return str(path)


def test_face_count_matches_trimesh(sphere_stl):
    path, mesh = sphere_stl
    assert stl_face_count(path) == len(mesh.faces) == 20480


def test_ascii_stl_is_rejected_so_callers_can_fall_back(tmp_path):
    path = tmp_path / "ascii.stl"
    path.write_text("solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\n"
                    "vertex 0 1 0\nendloop\nendfacet\nendsolid x\n")
    with pytest.raises(ValueError):
        stl_face_count(str(path))


def test_stream_stats_box_exact(box_stl):
    s = stream_stats(box_stl)
    assert s["faces"] == 12
    assert s["extents"] == pytest.approx([10, 20, 30], abs=1e-4)
    assert s["volume_mm3"] == pytest.approx(10 * 20 * 30, rel=1e-5)
    assert s["area_mm2"] == pytest.approx(2 * (200 + 300 + 600), rel=1e-5)


def test_stream_stats_chunking_gives_same_answer(sphere_stl):
    path, _ = sphere_stl
    whole = stream_stats(path, chunk=1_000_000)
    tiny = stream_stats(path, chunk=777)  # forces many uneven chunks
    assert tiny["volume_mm3"] == pytest.approx(whole["volume_mm3"], rel=1e-9)
    assert tiny["area_mm2"] == pytest.approx(whole["area_mm2"], rel=1e-9)
    assert tiny["extents"] == whole["extents"]


def test_stream_stats_sphere_matches_trimesh(sphere_stl):
    path, mesh = sphere_stl
    s = stream_stats(path)
    assert s["volume_mm3"] == pytest.approx(mesh.volume, rel=1e-4)
    assert s["area_mm2"] == pytest.approx(mesh.area, rel=1e-4)


def test_proxy_of_small_mesh_is_unchanged(box_stl):
    proxy = load_proxy(box_stl, max_faces=1000)
    assert len(proxy.faces) == 12
    assert proxy.volume == pytest.approx(6000, rel=1e-5)


def test_proxy_reduces_faces_and_keeps_shape(sphere_stl):
    path, mesh = sphere_stl
    proxy = load_proxy(path, max_faces=2000)
    assert 300 < len(proxy.faces) < 4000, len(proxy.faces)
    assert len(proxy.faces) < len(mesh.faces) / 4
    # shape survives: volume and extents stay close to the original
    assert proxy.volume == pytest.approx(mesh.volume, rel=0.08)
    assert proxy.extents == pytest.approx(mesh.extents, abs=2.5)


def test_proxy_chunking_gives_same_mesh(sphere_stl):
    path, _ = sphere_stl
    a = load_proxy(path, max_faces=2000, chunk=1_000_000)
    b = load_proxy(path, max_faces=2000, chunk=999)
    assert len(a.faces) == len(b.faces)
    assert a.volume == pytest.approx(b.volume, rel=1e-6)


def test_check_module_uses_proxy_above_threshold_and_says_so(sphere_stl):
    path, _ = sphere_stl
    result = check_module(path, (180, 180, 180), max_faces=2000)
    assert result["proxy_faces"] is not None
    assert result["bed_fit_failures"] == []
    assert result["original_faces"] == 20480


def test_check_module_small_file_is_not_proxied(box_stl):
    result = check_module(box_stl, (180, 180, 180), max_faces=2000)
    assert "proxy_faces" not in result
    assert result["watertight"] is True


def test_check_module_flags_wrong_units_from_stream_stats(tmp_path):
    """The CC0 Wendigo was authored 1.5 mm tall: bed-fit passes trivially, so
    a too-small model must be called out, not silently accepted."""
    mesh = trimesh.creation.box(extents=(0.9, 0.9, 1.5))
    path = tmp_path / "tiny.stl"
    mesh.export(path)
    result = check_module(str(path), (180, 180, 180), min_height_mm=10.0)
    assert any("smaller than" in f for f in result["bed_fit_failures"])
