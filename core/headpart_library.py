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
from pathlib import Path

import numpy as np

from fmp.morphs import find_morph
from fmp.tri import read_tri

MORPHS = ("RLY_Heavy", "RLY_Muscular", "RLY_Thin")

# Translation from the MPF head frame — where the head TRIs are authored, near
# z=120 — to the headpart frame. Headparts are shipped either origin-centred or
# at head height, and a mod is not always self-consistent: Beards of Power ships
# 83 shapes at head height and 94 at the origin. The frames are ~120 units
# apart, so which one a shape is in is measured per shape, never declared.
HEAD_OFFSET = np.array([0.0, -1.633, 120.555])

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
class HeadReference:
    """A reference head, its RLY_* fields, and both candidate frames."""

    sex: str
    verts: np.ndarray
    faces: np.ndarray
    deltas: dict[str, np.ndarray]
    surface_head: np.ndarray             # as authored, at head height
    surface_origin: np.ndarray           # translated to origin-centred


@dataclass
class FrameChoice:
    """Which frame a shape turned out to be in, and how well it fits."""

    surface: np.ndarray
    name: str
    median_distance: float
    # Where the shape comes closest to the head: the 5th percentile of its
    # vertex distances, in the chosen frame. A headpart is attached to the head
    # somewhere, so this is near zero for one — measured across 240 real shapes,
    # 234 contact within 1.0 unit. The six that do not sit at 14.5 to 31.4, with
    # nothing in between, and for those the frame choice is close to a coin flip
    # because neither frame puts them against the skin. This is the number that
    # says so.
    contact_distance: float = 0.0


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


def load_head_reference(path: str, sex: str) -> HeadReference:
    """Read a reference head TRI and keep the three RLY_* fields from it.

    Morphs are looked up by name, never by position. The shipped
    ``FACE_FemaleHead.tri`` holds ``['OLD_Heavy', 'RLY_Muscular', 'RLY_Thin',
    'RLY_Heavy']`` — a stale morph first and RLY_Heavy last — so reading by
    index would write the wrong field under the right name, and no downstream
    check could tell.
    """
    source = Path(path)
    tri = read_tri(source.read_bytes(), source=str(source))

    absent = [name for name in MORPHS if find_morph(tri, name) is None]
    if absent:
        raise ValueError(
            f"{source.name} carries no {', '.join(absent)}; it is not a current "
            f"Physiques of Skyrim head reference (it has: "
            f"{', '.join(tri.morph_names()) or 'no morphs at all'})")
    if len(tri.faces) == 0:
        raise ValueError(
            f"{source.name} carries no faces, and a surface wrap has nothing "
            "to sample without them")

    verts = tri.verts.astype(np.float64)
    return HeadReference(
        sex=sex, verts=verts, faces=np.asarray(tri.faces, dtype=np.int64),
        deltas={name: find_morph(tri, name).deltas() for name in MORPHS},
        surface_head=verts, surface_origin=verts - HEAD_OFFSET)


def pick_frame(ref: HeadReference, verts: np.ndarray, *,
               sample: int = 256) -> FrameChoice:
    """Choose the frame a shape is in, by which one puts it against the skin.

    Decided on a subsample: the two frames are a whole body length apart, so
    256 vertices settle it, while querying every vertex against both surfaces
    would double the cost of the entire run.
    """
    from fmp.wrap import closest_surface_samples

    verts = np.asarray(verts, dtype=np.float64)
    if not len(verts):
        return FrameChoice(ref.surface_head, "head", float("inf"),
                           float("inf"))

    picked = np.linspace(0, len(verts) - 1,
                         min(sample, len(verts))).astype(np.int64)
    probe = verts[picked]

    best = FrameChoice(ref.surface_head, "head", float("inf"), float("inf"))
    for surface, name in ((ref.surface_head, "head"),
                          (ref.surface_origin, "origin")):
        _tri, _bary, dist = closest_surface_samples(surface, ref.faces, probe,
                                                    k=4)
        if not len(dist):
            continue
        median = float(np.median(dist))
        if median < best.median_distance:
            best = FrameChoice(surface, name, median,
                               float(np.percentile(dist, 5)))
    return best


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
