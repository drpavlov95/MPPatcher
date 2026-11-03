"""Target-vertex -> reference-vertex mapping strategies."""
from __future__ import annotations

import numpy as np

from .tri import TriFile


def is_same_topology(ref: TriFile, tgt: TriFile) -> bool:
    return (ref.vertex_count == tgt.vertex_count
            and ref.faces.shape == tgt.faces.shape
            and np.array_equal(ref.faces, tgt.faces))


def nearest_vertex_indices(ref_verts: np.ndarray, tgt_verts: np.ndarray,
                           chunk: int = 512) -> np.ndarray:
    ref = np.asarray(ref_verts, dtype=np.float64)
    tgt = np.asarray(tgt_verts, dtype=np.float64)
    idx = np.empty(len(tgt), dtype=np.int64)
    for s in range(0, len(tgt), chunk):
        block = tgt[s:s + chunk]
        d2 = np.sum((block[:, None, :] - ref[None, :, :]) ** 2, axis=2)
        idx[s:s + len(block)] = np.argmin(d2, axis=1)
    return idx


def build_mapping(ref: TriFile, tgt: TriFile):
    """Returns (method, index_array_or_None, metrics)."""
    if is_same_topology(ref, tgt):
        return "identity", None, {"map_dist_max": 0.0, "map_dist_p95": 0.0}
    idx = nearest_vertex_indices(ref.verts, tgt.verts)
    dist = np.linalg.norm(
        tgt.verts.astype(np.float64) - ref.verts.astype(np.float64)[idx], axis=1)
    return "nearest", idx, {
        "map_dist_max": float(dist.max()) if len(dist) else 0.0,
        "map_dist_p95": float(np.percentile(dist, 95)) if len(dist) else 0.0,
    }
