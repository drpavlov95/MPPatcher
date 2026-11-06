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

import os
import re
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

import numpy as np

from fmp.morphs import find_morph, make_morph
from fmp.report import FileResult, RunReport
from fmp.tri import TriError, TriFile, read_tri, write_tri

# Where the plugin reads its library from, relative to Data. The output is a mod
# folder the user can enable directly.
MORPHS_SUBDIR = os.path.join("SKSE", "Plugins", "MPF", "morphs")
MANIFEST_PREFIX = "PhysiquesOfSkyrim_"

MORPHS = ("RLY_Heavy", "RLY_Muscular", "RLY_Thin")

# Translation from the MPF head frame — where the head TRIs are authored, near
# z=120 — to the headpart frame. Headparts are shipped either origin-centred or
# at head height, and a mod is not always self-consistent: Beards of Power ships
# 83 shapes at head height and 94 at the origin. The frames are ~120 units
# apart, so which one a shape is in is measured per shape, never declared.
HEAD_OFFSET = np.array([0.0, -1.633, 120.555])


@dataclass(frozen=True)
class WrapPreset:
    """How far from the reference surface a delta survives."""

    name: str
    falloff: float | None
    falloff_inner: float
    smooth: int = 0


# One preset for every part. An earlier design chose between this and a
# falloff-free variant from the HDPT part type; measurement killed that. For the
# Even More Brows meshes, which sit 0.03 units off the surface, the two produce
# bit-identical deltas; for BG3's gortash hair (median 1.02 off the skull) they
# differ by 0.0093 units. And the mapping was unsafe: BG3 declares its
# hair-*highlight* mesh as part "misc", which would have handed the on-skin
# variant to geometry standing off the skull exactly like the hair it belongs
# to. The part type is a label, not a measurement of standoff.
#
# Full delta out to 4.0 units, smoothly to zero at 14.0. Hair within a unit or
# two of the skin should follow it completely, including being pushed out of a
# neck that just got thicker; a strand hanging in the air well away from the
# body has nothing to follow, and carrying the skin's displacement out there
# only distorts the silhouette.
DEFAULT_PRESET = WrapPreset("default", falloff=14.0, falloff_inner=4.0,
                            smooth=0)

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
    # Where the shape comes closest to the head, in the chosen frame: the
    # distance of its single nearest sampled vertex. A headpart is attached to
    # the head somewhere, so this is near zero for one — measured across 240
    # real shapes, 234 contact within 1.0 unit. The six that do not sit at 14.5
    # to 31.4, with nothing in between, and for those the frame choice is close
    # to a coin flip because neither frame puts them against the skin.
    #
    # The minimum, not a percentile. A long braid with a dozen root vertices on
    # the scalp and three hundred hanging below has a 5th percentile far from
    # the head while its roots are touching it — and those roots take the full
    # delta. Reading a percentile as "the closest vertices" dropped exactly that
    # shape, at the hairline, where the loss shows most.
    contact_distance: float = 0.0


@dataclass
class VerifyResult:
    """What replaying the plugin's matching found."""

    matched: int
    unmatched: list[str]
    worst_distance: float
    # Shapes whose best match in the library is some *other* shape's set. They
    # would still be morphed, by a field authored for a different mesh, so this
    # is a defect of its own rather than a flavour of unmatched.
    wrong_set: list[str] = field(default_factory=list)
    source: str = "memory"


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
            best = FrameChoice(surface, name, median, float(dist.min()))
    return best


def minimal_tri(verts: np.ndarray) -> TriFile:
    """A TRI carrying nothing but base vertices — faces and UVs declared absent.

    Nothing reads these files except MPF, which keeps only the base vertices
    (to match) and the deltas (to apply), and skips every other block by its
    declared size. Carrying faces would add megabytes per download for data no
    reader looks at; the cost here is ~30 bytes per vertex, 12 for the position
    and 6 for each of the three morphs.

    Declaring ``polyTris`` 0 also sidesteps a sharp edge in the codec: the
    reader always consumes ``polyTris * 12`` bytes of UV faces, so a file
    written with faces but no UV-face block cannot be read back.
    """
    return TriFile(
        vertex_count=len(verts),
        verts=np.ascontiguousarray(verts, dtype="<f4"),
        faces=np.zeros((0, 3), dtype="<u4"),
        polyquads=0, unk2=0, unk3=0, uv_verts=0, flags=0,
        num_mods=0, mod_verts=0, unk7=0, unk8=0, unk9=0, unk10=0)


@dataclass
class BuiltSet:
    """The bytes of one morph set, and what measuring it produced."""

    data: bytes
    metrics: dict[str, float]


def build_set(ref: HeadReference, cluster: GeometryCluster, *,
              preset: WrapPreset = DEFAULT_PRESET,
              frame: FrameChoice | None = None) -> BuiltSet:
    """Wrap the reference's three fields onto one geometry and serialise it.

    The field is sampled by surface wrap rather than by nearest vertex. A hair
    mesh stands 1.5 to 1.8 units off the skull, and neighbouring hair vertices
    can land on different head vertices carrying unrelated deltas — which shears
    the mesh along the boundary between them. Wrapping asks for the closest
    point on the reference *surface* and interpolates across the triangle that
    contains it, so the field is continuous wherever the surface is.
    """
    # Imported here rather than at module scope: fmp.wrap pulls in scipy, and
    # the clustering and classification half of this module is what the GUI's
    # scan uses. A heavy import on the scan path would be paid for nothing.
    from fmp.wrap import wrap_deltas

    surface = frame.surface if frame is not None else ref.surface_origin
    tri = minimal_tri(cluster.verts)
    metrics: dict[str, float] = {}

    for name in MORPHS:
        deltas, measured = wrap_deltas(
            surface, ref.faces, ref.deltas[name], cluster.verts,
            falloff=preset.falloff, falloff_inner=preset.falloff_inner,
            smooth_iterations=preset.smooth,
            tgt_faces=(cluster.tris if preset.smooth > 0
                       and len(cluster.tris) else None))
        tri.morphs.append(make_morph(name, deltas))
        if name == MORPHS[0]:
            metrics.update(measured)
        # The weakest of the three is what says whether this set does anything
        # at all: a set of zeros matches every shape and applies nothing.
        metrics["delta_max_min"] = min(
            metrics.get("delta_max_min", float("inf")),
            float(measured.get("delta_max", 0.0)))

    data = write_tri(tri)
    # Read it straight back. A file MPF cannot parse is worse than no file, and
    # the reader here implements the same layout as the plugin's.
    try:
        check = read_tri(data, source=f"{cluster.representative} set")
    except TriError as exc:
        raise ValueError(f"set for {cluster.representative} "
                         f"cannot be read back: {exc}") from exc
    if check.vertex_count != len(cluster.verts):
        raise ValueError(
            f"set for {cluster.representative} read back with "
            f"{check.vertex_count} vertices, not {len(cluster.verts)}")
    if check.morph_names() != list(MORPHS):
        raise ValueError(
            f"set for {cluster.representative} read back with morphs "
            f"{check.morph_names()}, not {list(MORPHS)}")
    return BuiltSet(data=data, metrics=metrics)


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
                    cluster.members.append(shape.label)
                    break
            else:
                clusters.append(GeometryCluster(
                    sex=shape.sex, part=shape.part, verts=shape.verts,
                    tris=shape.tris, representative=shape.label,
                    members=[shape.label]))
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
            unmatched.append(shape.label)
    return VerifyResult(matched=matched, unmatched=unmatched,
                        worst_distance=worst)


def verify_written_library(assignments: list[tuple[ShapeInput, str]],
                           morphs_dir: str,
                           accept: float = SAME_MESH_LIMIT) -> VerifyResult:
    """Replay the plugin's matching against the files actually on disk.

    Verifying against the in-memory clusters cannot see anything that goes
    wrong between those arrays and the bytes written — the serialiser, the
    float32 cast, or the wrong array reaching the wrong filename. The plugin
    reads files, so this reads files.

    It also checks that each shape's best match is *its own* set, not merely
    that something in its key is close enough: a representative assigned to the
    wrong filename would still morph the shape, with a field authored for a
    different mesh.
    """
    cache: dict[str, np.ndarray] = {}
    index: dict[tuple[str, int], list[tuple[str, np.ndarray]]] = defaultdict(list)
    for shape, name in assignments:
        if name in cache:
            continue
        path = os.path.join(morphs_dir, name)
        try:
            with open(path, "rb") as handle:
                tri = read_tri(handle.read(), source=path)
        except (OSError, TriError) as exc:
            raise ValueError(f"the set written to {name} cannot be read back, "
                             f"so the plugin could not read it either: {exc}"
                             ) from exc
        if tri.morph_names() != list(MORPHS):
            raise ValueError(f"{name} holds morphs {tri.morph_names()}, "
                             f"not {list(MORPHS)}")
        cache[name] = tri.verts.astype(np.float64)
        index[(shape.sex, tri.vertex_count)].append((name, cache[name]))

    matched, unmatched, wrong, worst = 0, [], [], 0.0
    for shape, expected in assignments:
        candidates = index.get((shape.sex, len(shape.verts)), [])
        best_name, best = None, float("inf")
        for name, verts in candidates:
            distance = centred_mean_distance(shape.verts, verts)
            if distance < best:
                best_name, best = name, distance
        if best >= accept:
            unmatched.append(shape.label)
            continue
        matched += 1
        worst = max(worst, best)
        if best_name != expected:
            wrong.append(f"{shape.label} -> {best_name} (expected {expected})")
    return VerifyResult(matched=matched, unmatched=unmatched,
                        worst_distance=worst, wrong_set=wrong,
                        source="written files")


def sanitize_tag(tag: str) -> str:
    """Reduce a mod name to something safe in a filename.

    Every content mod shares one morphs folder, so the tag is what keeps one
    mod's sets from colliding with another's. An empty tag would produce
    ``RLY__F_4014.tri``, which collides with every other untagged mod.
    """
    cleaned = re.sub(r"[^A-Za-z0-9]", "", str(tag))
    return cleaned or "Mod"


def set_filename(tag: str, sex: str, vertex_count: int,
                 used: set[str]) -> str:
    """A unique filename for one set, recording it in *used*.

    Several geometries may legitimately share a vertex count: the plugin stores
    a vector per key and picks the closest, so shipping all of them is correct.
    The manifest dedupes by filename, so they must not collide.
    """
    stem = (f"RLY_{sanitize_tag(tag)}_"
            f"{'F' if sex == 'female' else 'M'}_{vertex_count}")
    candidate, n = stem, 0
    while candidate.lower() in used:
        n += 1
        candidate = f"{stem}_{chr(ord('b') + n - 1)}"
    used.add(candidate.lower())
    return candidate + ".tri"


def manifest_filename(tag: str) -> str:
    return f"{MANIFEST_PREFIX}{sanitize_tag(tag)}.toml"


def write_manifest(path: str, rows: list[tuple[str, str, str]], *,
                   tag: str) -> None:
    """Write the ``[[sets]]`` manifest the plugin reads beside the TRIs.

    MPF reads *every* .toml in that folder, so this one is named after the mod
    and sits beside everybody else's instead of replacing it.
    """
    lines = [
        f"# Physique Wheel headpart morphs for {tag}.",
        f"# Generated by MPPatcher on {date.today().isoformat()}.",
        "#",
        "# One block per distinct geometry. MPF keys on (sex, vertex count) and",
        "# confirms the match geometrically, so entries sharing a count are fine.",
        "",
    ]
    for name, sex, part in rows:
        lines += ["[[sets]]", f'file = "{name}"', f'sex  = "{sex}"',
                  f'part = "{part}"', ""]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


@dataclass
class LibraryOptions:
    """What the caller chose for one library run."""

    tag: str
    default_part: str = "hair"
    default_sex: str | None = None
    unclassified_both: bool = False
    cluster_limit: float = DEFAULT_CLUSTER_LIMIT
    falloff: float | None = DEFAULT_PRESET.falloff
    falloff_inner: float = DEFAULT_PRESET.falloff_inner
    smooth: int = DEFAULT_PRESET.smooth
    dry_run: bool = False

    def preset(self) -> WrapPreset:
        return WrapPreset("run", falloff=self.falloff,
                          falloff_inner=self.falloff_inner, smooth=self.smooth)


@dataclass
class LibrarySummary:
    """The run-wide numbers. Read `verified` and `unmatched` first."""

    sets: int = 0
    shapes: int = 0
    verified: int = 0
    unmatched: list[str] = field(default_factory=list)
    median_wrap_p50: float = 0.0
    worst_wrap_p50: float = 0.0
    frames: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    bytes_written: int = 0
    manifest: str | None = None
    # Sets whose weakest morph moves nothing at all. A library of zeros matches
    # every shape and applies nothing, which is indistinguishable from a working
    # one unless this is measured.
    zero_delta_sets: int = 0
    # True when nothing at all came out with movement in it — a broken
    # reference head rather than an awkward mesh.
    all_sets_zero: bool = False
    weakest_delta_max: float = 0.0
    wrong_set: list[str] = field(default_factory=list)
    verified_from: str = "memory"


def _expand(shape: ShapeInput, options: LibraryOptions) -> list[ShapeInput]:
    """One shape per sex it will be shipped for.

    A genuinely ambiguous part — vanilla's khajiit ear tufts, a hair with no
    opposite-sex counterpart — is shipped for both sexes rather than dropped
    when the caller asks for that. The cost is one duplicate file; the
    alternative is the part silently never morphing.
    """
    if shape.sex is not None:
        return [shape]
    if not options.unclassified_both:
        return []
    return [replace(shape, sex=sex) for sex in ("female", "male")]


def build_library(shapes: list[ShapeInput], refs: dict[str, HeadReference],
                  output_dir: str, options: LibraryOptions
                  ) -> tuple[RunReport, LibrarySummary]:
    """Build every morph set a mod needs, then prove the plugin can find them.

    The report carries one row per input shape, so the caller can say what
    happened to each; the summary carries everything run-wide. A shape the
    verification pass leaves unmatched is reported ``failed`` rather than
    warned about: a library the plugin cannot match is the exact failure this
    mode exists to prevent, and it is invisible from the inside.
    """
    report = RunReport(
        tool="headparts-library",
        reference="; ".join(f"{sex}={len(ref.verts)}v"
                            for sex, ref in sorted(refs.items())),
        selected_morphs=list(MORPHS), targets=[],
        output_dir=str(output_dir), mode="library")
    summary = LibrarySummary(shapes=len(shapes))
    preset = options.preset()

    def skip(result: FileResult, reason: str) -> None:
        result.status = "skipped"
        result.warnings.append(reason)
        key = reason.split("(")[0].strip()
        summary.skipped[key] = summary.skipped.get(key, 0) + 1

    # Stage 1: decide what goes in, keeping one report row per input shape.
    accepted: list[tuple[ShapeInput, FileResult]] = []
    for shape in shapes:
        result = FileResult(path=shape.relpath, relpath=shape.label)
        report.files.append(result)

        reason = reject_reason(shape)
        if reason:
            skip(result, reason)
            continue
        expanded = _expand(shape, options)
        if not expanded:
            skip(result, "sex could not be determined from the plugin, the "
                         "path or the filename")
            continue
        if shape.sex is None:
            result.warnings.append(
                "sex could not be determined; shipped for both")
        for variant in expanded:
            if variant.sex not in refs:
                result.status = "failed"
                result.error = (f"no reference head for {variant.sex}; "
                                "nothing can be built for it")
                break
            accepted.append((variant, result))

    # Stage 2: one set per distinct geometry.
    clusters = cluster_geometries([shape for shape, _result in accepted],
                                  options.cluster_limit)
    rows: list[tuple[str, str, str]] = []
    used: set[str] = set()
    p50s: list[float] = []
    kept: list[GeometryCluster] = []
    set_of_member: dict[tuple[str, str], str] = {}
    zero_sets: list[str] = []
    weakest: list[float] = []

    for cluster in clusters:
        frame = pick_frame(refs[cluster.sex], cluster.verts)
        # Past the falloff radius every delta is zero by construction, so a set
        # here would be a file of zeros and the game behaves identically
        # without one. Measured: 234 of 240 real shapes contact the head within
        # 1.0 unit, and the six that do not sit at 14.5 to 31.4 with nothing in
        # between, so this test falls inside an empty band rather than on a
        # tuned edge.
        #
        # The test is on the single nearest vertex, not a percentile: a braid
        # whose dozen root vertices lie on the scalp and whose three hundred
        # others hang below is attached, and those roots take the full delta.
        if (preset.falloff is not None
                and frame.contact_distance >= preset.falloff):
            members = set(cluster.members)
            for shape, result in accepted:
                if (shape.label in members and shape.sex == cluster.sex
                        and result.status == "pending"):
                    skip(result,
                         "detached from the head surface (its nearest vertex "
                         f"is {frame.contact_distance:.1f} units away, past "
                         "the falloff radius, so every delta would be zero)")
            continue

        built = build_set(refs[cluster.sex], cluster, preset=preset,
                          frame=frame)

        # A set whose weakest morph is flat matches the shape and applies
        # nothing, which from inside the program is indistinguishable from a
        # working one. It is this shape's problem, not the run's — the same
        # thing detachment is, found after building rather than before — so it
        # is skipped with the reason and the other shapes still ship. Every set
        # coming out flat is a different matter: that is a broken reference
        # head, and the caller is told so below.
        floor = built.metrics.get("delta_max_min", 0.0)
        if floor <= 0.0:
            zero_sets.append(cluster.representative)
            members = set(cluster.members)
            for shape, result in accepted:
                if (shape.label in members and shape.sex == cluster.sex
                        and result.status == "pending"):
                    skip(result,
                         "its morph field came out all zeros: the reference "
                         "head does not move where this mesh sits, so no set "
                         "was written")
            continue

        name = set_filename(options.tag, cluster.sex, len(cluster.verts), used)
        summary.bytes_written += len(built.data)
        summary.frames[frame.name] = summary.frames.get(frame.name, 0) + 1
        p50s.append(built.metrics.get("wrap_dist_p50", 0.0))
        rows.append((name, cluster.sex, cluster.part))
        kept.append(cluster)
        for member in cluster.members:
            set_of_member[(cluster.sex, member)] = name

        weakest.append(floor)

        if not options.dry_run:
            destination = os.path.join(output_dir, MORPHS_SUBDIR, name)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            with open(destination, "wb") as handle:
                handle.write(built.data)

    summary.sets = len(rows)
    if p50s:
        summary.median_wrap_p50 = float(np.median(p50s))
        summary.worst_wrap_p50 = float(max(p50s))

    summary.zero_delta_sets = len(zero_sets)
    if weakest:
        summary.weakest_delta_max = float(min(weakest))

    # Every candidate coming out flat is not a property of the meshes: it is a
    # reference head with no usable RLY_ fields, or a falloff smaller than the
    # standoff of the whole mod. That is the caller's setup, so it is reported
    # as a failure of each shape rather than a skip.
    summary.all_sets_zero = bool(zero_sets) and not rows
    if summary.all_sets_zero:
        for _shape, result in accepted:
            if result.status in ("skipped", "pending"):
                result.status = "failed"
                result.error = (
                    "every set in this run came out all zeros. The reference "
                    "head carries no usable RLY_ morphs where these meshes "
                    "sit, or --falloff is smaller than their standoff.")

    # Stage 3: replay the plugin's matching over everything that went in. Off
    # the written files whenever there are files — the plugin reads files, and
    # the serialiser sits between the arrays above and what it will see.
    assignments = [(shape, set_of_member[(shape.sex, shape.label)])
                   for shape, _result in accepted
                   if (shape.sex, shape.label) in set_of_member]
    if options.dry_run:
        verdict = verify_against_clusters(
            [shape for shape, _name in assignments], kept)
        verdict.source = "memory (dry run)"
    else:
        verdict = verify_written_library(
            assignments, os.path.join(output_dir, MORPHS_SUBDIR))
    summary.verified = verdict.matched
    summary.unmatched = verdict.unmatched
    summary.wrong_set = verdict.wrong_set
    summary.verified_from = verdict.source

    unmatched = set(verdict.unmatched)
    wrong = {entry.split(" -> ")[0] for entry in verdict.wrong_set}
    for shape, result in accepted:
        if result.status != "pending":
            continue
        name = set_of_member.get((shape.sex, shape.label))
        if name is None or shape.label in unmatched:
            result.status = "failed"
            result.error = ("no set in the written library matches this shape; "
                            "the plugin would not apply anything to it")
            continue
        if shape.label in wrong:
            result.status = "failed"
            result.error = ("the closest set in the library is one authored for "
                            "a different mesh; the plugin would morph this "
                            "shape with the wrong field")
            continue
        result.status = "patched"
        result.method = "wrap"
        result.added = list(MORPHS)
        result.output = os.path.join(output_dir, MORPHS_SUBDIR, name)

    if rows and not options.dry_run:
        manifest = os.path.join(output_dir, MORPHS_SUBDIR,
                                manifest_filename(options.tag))
        write_manifest(manifest, rows, tag=options.tag)
        summary.manifest = manifest
    return report, summary
