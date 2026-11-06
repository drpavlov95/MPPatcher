r"""The MPF morph library for a headpart mod: build it, then prove it.

``ModernPhysiqueSystem`` reads ``Data\SKSE\Plugins\MPF\morphs`` directly. It
indexes every set it finds there by ``(sex, vertex count)``, confirms a candidate
against the live mesh with ``CentredMeanDistance``, and accepts it within
``kSameMeshLimit`` = 3.0. So the unit of work is a distinct *geometry*, not a
distinct file — one hairstyle shipped under several race paths needs one set —
and the only thing that makes a library correct is that every shape in the mod
can find its own set under those rules.

That is checked rather than assumed. Every failure found while this pipeline was
built was a silent one that the happy path reported as success, so
:func:`verify_against_clusters` replays the plugin's matching over the shapes
that went in, and a run that leaves one unmatched is a failed run.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

MORPHS = ("RLY_Heavy", "RLY_Muscular", "RLY_Thin")

# The plugin's own acceptance threshold.
SAME_MESH_LIMIT = 3.0
# Deliberately tighter. Clustering right up to the plugin's threshold leaves a
# cluster member sitting 2.95 from the representative that has to match it at
# runtime — no headroom at all if the live mesh differs from the file by even a
# little. Beards of Power clustered at 3.0 hit exactly that; at 1.5 its worst
# match distance dropped to 1.34, for nine more files.
DEFAULT_CLUSTER_LIMIT = 1.5
# Below this a shape is a placeholder, not a headpart.
MIN_VERTS = 8


@dataclass
class ShapeInput:
    """One classified shape on its way into the library."""

    relpath: str
    shape: str
    sex: str | None
    sex_source: str
    part: str
    verts: np.ndarray
    tris: np.ndarray

    @property
    def label(self) -> str:
        """How this shape is named in reports: one NIF can hold several."""
        return f"{self.relpath}#{self.shape}"


@dataclass
class GeometryCluster:
    """A distinct geometry, and every shape that will match the set for it."""

    sex: str
    part: str
    verts: np.ndarray
    tris: np.ndarray
    representative: str
    members: list[str] = field(default_factory=list)


@dataclass
class VerifyResult:
    """What replaying the plugin's matching found."""

    matched: int
    unmatched: list[str]
    worst_distance: float


def centred_mean_distance(a: np.ndarray, b: np.ndarray) -> float:
    """The plugin's own metric, replicated so this tool predicts its matching.

    Both meshes are centred on their own mean first, which is why a headpart
    shipped at the origin and the same headpart shipped at head height measure
    as one geometry.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    a = a - a.mean(0)
    b = b - b.mean(0)
    return float(np.linalg.norm(a - b, axis=1).mean())


def reject_reason(shape: ShapeInput) -> str | None:
    """Why this shape cannot become a morph set, or None when it can.

    A non-finite vertex is rejected rather than carried: ``CentredMeanDistance``
    over a NaN is NaN, which loses every comparison, so one poisoned shape would
    quietly stop every other shape sharing its vertex count from matching.
    """
    if len(shape.verts) < MIN_VERTS:
        return "placeholder"
    if not np.isfinite(np.asarray(shape.verts, dtype=np.float64)).all():
        return "non-finite vertices"
    return None


def cluster_geometries(shapes: list[ShapeInput],
                       limit: float = DEFAULT_CLUSTER_LIMIT
                       ) -> list[GeometryCluster]:
    """Group shapes by ``(sex, vertex count)`` and then by actual geometry.

    Several geometries may legitimately share a vertex count. The plugin stores
    a vector per key and picks the closest, so shipping more than one is
    correct; they only need distinct filenames.
    """
    buckets: dict[tuple[str, int], list[ShapeInput]] = defaultdict(list)
    for shape in shapes:
        if shape.sex is None:
            continue
        buckets[(shape.sex, len(shape.verts))].append(shape)

    out: list[GeometryCluster] = []
    for key in sorted(buckets):
        clusters: list[GeometryCluster] = []
        for shape in buckets[key]:
            for cluster in clusters:
                if centred_mean_distance(shape.verts, cluster.verts) < limit:
                    cluster.members.append(shape.relpath)
                    break
            else:
                clusters.append(GeometryCluster(
                    sex=shape.sex, part=shape.part, verts=shape.verts,
                    tris=shape.tris, representative=shape.relpath,
                    members=[shape.relpath]))
        out.extend(clusters)
    return out


def verify_against_clusters(shapes: list[ShapeInput],
                            clusters: list[GeometryCluster],
                            accept: float = SAME_MESH_LIMIT) -> VerifyResult:
    """Rebuild the library the way the plugin does, and match every shape to it.

    This is the invariant the whole mode rests on. It is deliberately written
    from the plugin's rules rather than from the clustering above: a bug shared
    by both would otherwise verify itself.
    """
    index: dict[tuple[str, int], list[GeometryCluster]] = defaultdict(list)
    for cluster in clusters:
        index[(cluster.sex, len(cluster.verts))].append(cluster)

    matched, unmatched, worst = 0, [], 0.0
    for shape in shapes:
        if shape.sex is None:
            continue
        candidates = index.get((shape.sex, len(shape.verts)), [])
        best = min((centred_mean_distance(shape.verts, c.verts)
                    for c in candidates), default=float("inf"))
        if best < accept:
            matched += 1
            worst = max(worst, best)
        else:
            unmatched.append(shape.relpath)
    return VerifyResult(matched=matched, unmatched=unmatched,
                        worst_distance=worst)
