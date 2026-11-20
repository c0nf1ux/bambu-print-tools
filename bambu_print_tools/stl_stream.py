"""Low-RAM access to big binary STLs.

Why this exists: trimesh.load() costs roughly 12x the file size in RAM
(measured 2026-11-20: a 97 MB / 2.0M-face sculpt peaked at 1.17 GB, a
57 MB / 1.2M-face one at 726 MB), and the ray/graph checks stack more on
top. On an 8 GB machine that is the difference between a check and a
crash. Everything routine in this toolkit (a parametric module, OpenSCAD
render, slicing, FTP) peaks under ~110 MB; sculpted miniatures are the
only thing that blows the budget.

Two tools here, both streaming the file from disk in fixed-size chunks so
peak RAM does not grow with the file:

- stream_stats(): exact bounds, volume and surface area, ~tens of MB.
- load_proxy(): a reduced-face mesh made by vertex clustering, small
  enough for the overhang / bridge / wall-thickness heuristics. Those are
  static-geometry estimates anyway, so a proxy at ~0.1 mm resolution is
  plenty. The proxy is NOT for watertightness or true-fusion checks:
  clustering can open or close tiny gaps. Use the real file for those.

Binary STL only (50 bytes per triangle). ASCII STL raises ValueError so
callers can fall back to trimesh.load.
"""
from __future__ import annotations

import os
import struct

import numpy as np
import trimesh

_TRI_DTYPE = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")])  # 50 bytes
_HEADER_BYTES = 84
DEFAULT_CHUNK = 200_000  # triangles per chunk: ~15 MB of float64 working data
_CELL_AREA_FACTOR = 2.6
_AXIS_BITS = 21          # per-axis cell index budget when packing keys into an int64


def stl_face_count(path: str) -> int:
    """Triangle count from the 84-byte binary header, validated against the file
    size. Raises ValueError for ASCII or truncated files."""
    size = os.path.getsize(path)
    if size < _HEADER_BYTES:
        raise ValueError(f"{path}: too small to be a binary STL")
    with open(path, "rb") as f:
        f.seek(80)
        (count,) = struct.unpack("<I", f.read(4))
    if _HEADER_BYTES + _TRI_DTYPE.itemsize * count != size:
        raise ValueError(f"{path}: not a binary STL (ASCII or truncated); fall back to trimesh.load")
    return count


def _triangle_chunks(path: str, count: int, chunk: int):
    """Yield (k, 3, 3) float64 vertex arrays, reading only `chunk` triangles at a time."""
    mm = np.memmap(path, dtype=_TRI_DTYPE, mode="r", offset=_HEADER_BYTES, shape=(count,))
    try:
        for start in range(0, count, chunk):
            yield np.asarray(mm[start:start + chunk]["v"], dtype=np.float64)
    finally:
        del mm


def stream_stats(path: str, chunk: int = DEFAULT_CHUNK) -> dict:
    """Exact face count, bounds, volume (signed, so valid for closed meshes) and
    surface area without ever holding the whole mesh."""
    count = stl_face_count(path)
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    volume = 0.0
    area = 0.0
    for tri in _triangle_chunks(path, count, chunk):
        flat = tri.reshape(-1, 3)
        lo = np.minimum(lo, flat.min(axis=0))
        hi = np.maximum(hi, flat.max(axis=0))
        v0, v1, v2 = tri[:, 0], tri[:, 1], tri[:, 2]
        volume += float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum()) / 6.0
        area += float(0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1).sum())
    return {
        "faces": count,
        "bounds_min": lo.tolist(),
        "bounds_max": hi.tolist(),
        "extents": (hi - lo).tolist(),
        "volume_mm3": volume,
        "area_mm2": area,
    }


def load_proxy(path: str, max_faces: int = 100_000, chunk: int = DEFAULT_CHUNK) -> trimesh.Trimesh:
    """Mesh with roughly <= max_faces triangles built by streaming the STL and
    clustering vertices on a grid (each occupied cell becomes one vertex at the
    mean of its members; triangles that collapse are dropped). A file already
    within max_faces is returned as-is. mesh.metadata["cell_mm"] records the
    grid size, i.e. the resolution of the proxy (None when not reduced)."""
    count = stl_face_count(path)
    stats = stream_stats(path, chunk)
    if count <= max_faces:
        verts = np.concatenate([t.reshape(-1, 3) for t in _triangle_chunks(path, count, chunk)])
        mesh = trimesh.Trimesh(vertices=verts, faces=np.arange(len(verts)).reshape(-1, 3), process=True)
        mesh.metadata["cell_mm"] = None
        return mesh

    origin = np.array(stats["bounds_min"])
    # Calibrated on real sculpts (2026-11-20): factor 2.0 produced 0.7-1.3x the
    # requested face count; 2.6 lands at or just under it. Volume stayed within
    # 1.5% of the original even at half the target.
    cell = float(np.sqrt(_CELL_AREA_FACTOR * stats["area_mm2"] / max_faces))
    max_cells = (max(stats["extents"]) / cell) if cell > 0 else 0
    if max_cells >= 2 ** _AXIS_BITS:
        raise ValueError("grid too fine to pack cell keys; raise max_faces' cell size")

    def cell_keys(tri):
        idx = np.floor((tri.reshape(-1, 3) - origin) / cell).astype(np.int64)
        return idx[:, 0] | (idx[:, 1] << _AXIS_BITS) | (idx[:, 2] << (2 * _AXIS_BITS))

    # Pass 1: accumulate only the occupied cells (sum of member positions + count).
    # Working set is one chunk plus the cell table, not the whole mesh.
    cell_keys_seen = np.empty(0, dtype=np.int64)
    sums = np.empty((0, 3))
    counts = np.empty(0)
    for tri in _triangle_chunks(path, count, chunk):
        pts = tri.reshape(-1, 3)
        merged, inverse = np.unique(np.concatenate([cell_keys_seen, cell_keys(tri)]), return_inverse=True)
        n_old = len(cell_keys_seen)
        new_sums = np.zeros((len(merged), 3))
        new_counts = np.zeros(len(merged))
        new_sums[inverse[:n_old]] += sums
        new_counts[inverse[:n_old]] += counts
        for axis in range(3):
            new_sums[:, axis] += np.bincount(inverse[n_old:], weights=pts[:, axis], minlength=len(merged))
        new_counts += np.bincount(inverse[n_old:], minlength=len(merged))
        cell_keys_seen, sums, counts = merged, new_sums, new_counts
    verts = sums / counts[:, None]

    # Pass 2: re-read the file, map each triangle's corners to cell ids, and keep
    # only triangles that did not collapse. Only the survivors are retained.
    kept = []
    for tri in _triangle_chunks(path, count, chunk):
        ids = np.searchsorted(cell_keys_seen, cell_keys(tri)).reshape(-1, 3).astype(np.int32)
        ok = (ids[:, 0] != ids[:, 1]) & (ids[:, 1] != ids[:, 2]) & (ids[:, 0] != ids[:, 2])
        kept.append(ids[ok])
    faces = np.concatenate(kept) if kept else np.empty((0, 3), dtype=np.int32)
    keep = slice(None)
    mesh = trimesh.Trimesh(vertices=verts, faces=faces[keep], process=False)
    mesh.remove_unreferenced_vertices()
    mesh.metadata["cell_mm"] = cell
    return mesh
