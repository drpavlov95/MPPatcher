"""Surface-wrap transfer: sample a reference morph over its surface, not at its vertices.

Nearest-vertex mapping is right for assets that share the skin: brows and beards sit
on the face, so the nearest head vertex is the correct source. Hair does not. A hair
mesh stands off the skull - median 1.5 to 1.8 units for vanilla-style hair, with long
strands reaching 19 - and neighbouring hair vertices can land on different head
vertices with unrelated deltas. The result is a discontinuous field: the hair shears
along the boundary between the two source vertices.

Wrapping fixes the discontinuity by asking a different question. For each target
vertex it finds the closest point on the reference *surface* and interpolates the
delta across the triangle that contains it. The field is then continuous everywhere
the surface is, so the hair deforms smoothly.

Distance falloff is separate and optional. Hair within a unit or two of the skin
should follow it fully, including being pushed out of a neck that just got thicker.
A strand hanging in the air well away from the body has nothing to follow, and
carrying the skin's full displacement out there only distorts the silhouette.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial import cKDTree


def _vertex_triangle_adjacency(faces: np.ndarray, vertex_count: int):
    """CSR-style map from vertex index to the triangles touching it."""
    corner_vert = faces.reshape(-1)
    corner_tri = np.repeat(np.arange(len(faces), dtype=np.int64), 3)
    order = np.argsort(corner_vert, kind="stable")
    tri_of_corner = corner_tri[order]
    counts = np.bincount(corner_vert, minlength=vertex_count)
    starts = np.concatenate(([0], np.cumsum(counts)))
    return starts, tri_of_corner


def _closest_point_on_triangles(p, a, b, c):
    """Closest point to p on each triangle (a, b, c), plus its barycentric weights.

    Vectorised form of the region test in Ericson, Real-Time Collision Detection.
    Every branch is evaluated and selected with np.where: the arrays are large and
    the branches are cheap, so this beats looping per region.
    """
    ab = b - a
    ac = c - a
    ap = p - a

    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)

    bp = p - b
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)

    cp = p - c
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)

    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2

    # Start from the interior case, then override with each boundary region.
    denom = va + vb + vc
    # A degenerate (zero-area) triangle would divide by zero; fall back to vertex a.
    safe = np.where(np.abs(denom) > 1e-20, denom, 1.0)
    v = vb / safe
    w = vc / safe
    u = 1.0 - v - w
    bary = np.stack([u, v, w], axis=1)
    bary = np.where((np.abs(denom) > 1e-20)[:, None], bary,
                    np.array([1.0, 0.0, 0.0])[None, :])

    def override(mask, new_bary):
        nonlocal bary
        bary = np.where(mask[:, None], new_bary, bary)

    # vertex regions
    override((d1 <= 0) & (d2 <= 0), np.array([1.0, 0.0, 0.0])[None, :])
    override((d3 >= 0) & (d4 <= d3), np.array([0.0, 1.0, 0.0])[None, :])
    override((d6 >= 0) & (d5 <= d6), np.array([0.0, 0.0, 1.0])[None, :])

    # edge ab
    m = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
    t = np.where(np.abs(d1 - d3) > 1e-20, d1 / np.where(np.abs(d1 - d3) > 1e-20, d1 - d3, 1.0), 0.0)
    t = np.clip(t, 0.0, 1.0)
    override(m, np.stack([1.0 - t, t, np.zeros_like(t)], axis=1))

    # edge ac
    m = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
    t = np.where(np.abs(d2 - d6) > 1e-20, d2 / np.where(np.abs(d2 - d6) > 1e-20, d2 - d6, 1.0), 0.0)
    t = np.clip(t, 0.0, 1.0)
    override(m, np.stack([1.0 - t, np.zeros_like(t), t], axis=1))

    # edge bc
    dd = (d4 - d3) + (d5 - d6)
    m = (va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0)
    t = np.where(np.abs(dd) > 1e-20, (d4 - d3) / np.where(np.abs(dd) > 1e-20, dd, 1.0), 0.0)
    t = np.clip(t, 0.0, 1.0)
    override(m, np.stack([np.zeros_like(t), 1.0 - t, t], axis=1))

    pt = (bary[:, 0:1] * a + bary[:, 1:2] * b + bary[:, 2:3] * c)
    return pt, bary


def closest_surface_samples(ref_verts: np.ndarray, ref_faces: np.ndarray,
                            tgt_verts: np.ndarray, *, k: int = 8,
                            chunk: int = 4096):
    """For each target vertex: (triangle index, barycentric weights, distance).

    Only triangles touching one of the k nearest reference vertices are tested. For
    a closed surface sampled far more densely than the offset distance - which a head
    mesh is - the true closest triangle always touches one of them.
    """
    ref_verts = np.asarray(ref_verts, dtype=np.float64)
    tgt_verts = np.asarray(tgt_verts, dtype=np.float64)
    faces = np.asarray(ref_faces, dtype=np.int64)

    tree = cKDTree(ref_verts)
    starts, tri_of_corner = _vertex_triangle_adjacency(faces, len(ref_verts))

    n = len(tgt_verts)
    out_tri = np.zeros(n, dtype=np.int64)
    out_bary = np.zeros((n, 3), dtype=np.float64)
    out_dist = np.zeros(n, dtype=np.float64)

    k = min(k, len(ref_verts))
    for s in range(0, n, chunk):
        block = tgt_verts[s:s + chunk]
        m = len(block)
        _, nn = tree.query(block, k=k)
        nn = np.atleast_2d(nn).astype(np.int64)

        # Gather every candidate triangle for the whole chunk into one flat array,
        # tagged with the target vertex that owns it, so the point-triangle test is
        # a single vectorised pass. Python-level gathering here would dominate the
        # runtime: this is the hot loop of the whole transfer.
        pair_vert = nn.reshape(-1)                       # (m*k,)
        pair_owner = np.repeat(np.arange(m, dtype=np.int64), k)
        pair_count = starts[pair_vert + 1] - starts[pair_vert]

        total = int(pair_count.sum())
        if total == 0:
            out_tri[s:s + m] = 0
            out_bary[s:s + m] = np.array([1.0, 0.0, 0.0])
            out_dist[s:s + m] = np.linalg.norm(block - ref_verts[faces[0, 0]], axis=1)
            continue

        # ragged-range expansion: base offset per pair, plus the position within it
        pair_start = np.concatenate(([0], np.cumsum(pair_count)[:-1]))
        within = np.arange(total, dtype=np.int64) - np.repeat(pair_start, pair_count)
        cand = tri_of_corner[np.repeat(starts[pair_vert], pair_count) + within]
        owner = np.repeat(pair_owner, pair_count)

        tri = faces[cand]
        pt, bary = _closest_point_on_triangles(
            block[owner], ref_verts[tri[:, 0]], ref_verts[tri[:, 1]], ref_verts[tri[:, 2]])
        d = np.linalg.norm(pt - block[owner], axis=1)

        # Nearest candidate per owner: sort by (owner, distance) and take the first
        # row of each owner's run.
        order = np.lexsort((d, owner))
        owner_sorted = owner[order]
        have = np.bincount(owner, minlength=m) > 0
        firsts = np.searchsorted(owner_sorted, np.arange(m), side="left")
        sel = order[np.clip(firsts, 0, len(order) - 1)]

        out_tri[s:s + m] = np.where(have, cand[sel], 0)
        out_bary[s:s + m] = np.where(have[:, None], bary[sel],
                                     np.array([1.0, 0.0, 0.0])[None, :])
        out_dist[s:s + m] = np.where(have, d[sel], np.inf)

    return out_tri, out_bary, out_dist


def falloff_weights(dist: np.ndarray, scale: float, *, inner: float = 0.0) -> np.ndarray:
    """1 up to `inner`, smoothly to 0 at `scale`, using smoothstep.

    Smoothstep rather than a linear ramp because its derivative vanishes at both
    ends: a linear ramp leaves a visible crease in the mesh at the cut-off radius.
    """
    if scale <= inner:
        return (dist <= inner).astype(np.float64)
    t = (np.asarray(dist, dtype=np.float64) - inner) / (scale - inner)
    t = np.clip(t, 0.0, 1.0)
    return 1.0 - (t * t * (3.0 - 2.0 * t))


def smooth_over_mesh(values: np.ndarray, faces: np.ndarray, vertex_count: int,
                     iterations: int = 1, strength: float = 0.5) -> np.ndarray:
    """Laplacian smoothing of a per-vertex vector field over the target mesh.

    Wrapping already gives a continuous field, so this is a finishing pass for
    meshes whose own topology is coarse relative to the head's.
    """
    if iterations <= 0:
        return values
    faces = np.asarray(faces, dtype=np.int64)
    a = faces[:, [0, 1, 2]].reshape(-1)
    b = faces[:, [1, 2, 0]].reshape(-1)
    edges_i = np.concatenate([a, b])
    edges_j = np.concatenate([b, a])
    deg = np.bincount(edges_i, minlength=vertex_count).astype(np.float64)
    deg[deg == 0] = 1.0

    out = np.asarray(values, dtype=np.float64).copy()
    for _ in range(iterations):
        acc = np.zeros_like(out)
        for c in range(out.shape[1]):
            acc[:, c] = np.bincount(edges_i, weights=out[edges_j, c],
                                    minlength=vertex_count)
        avg = acc / deg[:, None]
        out = (1.0 - strength) * out + strength * avg
    return out


def wrap_deltas(ref_verts, ref_faces, ref_deltas, tgt_verts, *,
                falloff: float | None = None, falloff_inner: float = 0.0,
                smooth_iterations: int = 0, smooth_strength: float = 0.5,
                tgt_faces=None, k: int = 8):
    """Transfer per-vertex deltas from a reference surface onto target vertices.

    Returns (deltas, metrics).
    """
    tri, bary, dist = closest_surface_samples(ref_verts, ref_faces, tgt_verts, k=k)
    faces = np.asarray(ref_faces, dtype=np.int64)
    d = np.asarray(ref_deltas, dtype=np.float64)

    corners = faces[tri]
    out = (bary[:, 0:1] * d[corners[:, 0]]
           + bary[:, 1:2] * d[corners[:, 1]]
           + bary[:, 2:3] * d[corners[:, 2]])

    if falloff is not None:
        out *= falloff_weights(dist, falloff, inner=falloff_inner)[:, None]

    if smooth_iterations > 0 and tgt_faces is not None:
        out = smooth_over_mesh(out, tgt_faces, len(tgt_verts),
                               iterations=smooth_iterations, strength=smooth_strength)

    mag = np.linalg.norm(out, axis=1)
    return out, {
        "wrap_dist_p50": float(np.percentile(dist, 50)) if len(dist) else 0.0,
        "wrap_dist_p95": float(np.percentile(dist, 95)) if len(dist) else 0.0,
        "wrap_dist_max": float(dist.max()) if len(dist) else 0.0,
        "delta_max": float(mag.max()) if len(mag) else 0.0,
        "delta_mean": float(mag.mean()) if len(mag) else 0.0,
    }
