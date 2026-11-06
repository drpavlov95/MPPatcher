"""Coverage for the MPF morph library: clustering, and the verification of it.

The library is read by the plugin, not by this program, so the tests that matter
replay the plugin's own behaviour: it indexes sets by ``(sex, vertex count)``,
confirms a candidate with ``CentredMeanDistance``, and accepts it within 3.0. A
library that matches nothing is indistinguishable from a working one from the
inside, which is why verification is a test here and an always-on pass in the
code.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.headpart_library import (
    DEFAULT_CLUSTER_LIMIT, DEFAULT_PRESET, HEAD_OFFSET, MIN_VERTS, MORPHS,
    MORPHS_SUBDIR, SAME_MESH_LIMIT, LibraryOptions, ShapeInput, WrapPreset,
    build_library, build_set, centred_mean_distance, cluster_geometries,
    load_head_reference, minimal_tri, pick_frame, reject_reason, sanitize_tag,
    set_filename, verify_against_clusters, write_manifest,
)
from fmp.morphs import make_morph
from fmp.tri import TriFile, read_tri, write_tri


def _grid(n: int = 20, *, scale: float = 1.0, seed: int = 0) -> np.ndarray:
    """A deterministic scatter of `n` vertices."""
    rng = np.random.default_rng(seed)
    return rng.normal(scale=scale, size=(n, 3))


def _surface(side: int = 10):
    """A flat triangulated sheet at head height, standing in for a head."""
    xs, ys = np.meshgrid(np.linspace(-5.0, 5.0, side),
                         np.linspace(-5.0, 5.0, side))
    verts = np.stack([xs.ravel(), ys.ravel(),
                      np.full(xs.size, HEAD_OFFSET[2])], axis=1)
    faces = []
    for row in range(side - 1):
        for col in range(side - 1):
            a = row * side + col
            faces.append((a, a + 1, a + side))
            faces.append((a + 1, a + side + 1, a + side))
    return verts, np.asarray(faces, dtype=np.uint32)


def _write_reference(folder: str, name: str, morphs, *,
                     with_faces: bool = True) -> str:
    """A reference TRI carrying `morphs` as (name, uniform delta) in order."""
    verts, faces = _surface()
    if not with_faces:
        faces = np.zeros((0, 3), dtype="<u4")
    tri = TriFile(
        vertex_count=len(verts),
        verts=np.ascontiguousarray(verts, dtype="<f4"),
        faces=faces,
        polyquads=0, unk2=0, unk3=0, uv_verts=0, flags=0,
        num_mods=0, mod_verts=0, unk7=0, unk8=0, unk9=0, unk10=0,
        # read_tri always consumes polyTris * 12 bytes of UV faces, regardless
        # of uv_verts, so a file written with faces must carry that block or it
        # cannot be read back. minimal_tri avoids the whole question by
        # declaring polyTris 0.
        uvfaces_raw=b"\x00" * (len(faces) * 12))
    for morph_name, amount in morphs:
        deltas = np.full((len(verts), 3), 0.0)
        deltas[:, 2] = amount
        tri.morphs.append(make_morph(morph_name, deltas))
    path = os.path.join(folder, name)
    with open(path, "wb") as handle:
        handle.write(write_tri(tri))
    return path


def _shape(relpath: str, verts: np.ndarray, *, sex: str = "female",
           part: str = "hair", shape: str = "Hair") -> ShapeInput:
    return ShapeInput(relpath=relpath, shape=shape, sex=sex,
                      sex_source="plugin", part=part, verts=verts,
                      tris=np.zeros((0, 3), dtype=np.uint32))


class ConstantsTests(unittest.TestCase):
    def test_the_thresholds_are_the_measured_ones(self):
        # The plugin's own kSameMeshLimit, and a deliberately tighter clustering
        # limit: clustering up to 3.0 leaves a member 2.95 from the
        # representative that has to match it at runtime.
        self.assertEqual(SAME_MESH_LIMIT, 3.0)
        self.assertEqual(DEFAULT_CLUSTER_LIMIT, 1.5)
        self.assertLess(DEFAULT_CLUSTER_LIMIT, SAME_MESH_LIMIT)

    def test_the_morph_names_are_exact_and_ordered(self):
        self.assertEqual(MORPHS, ("RLY_Heavy", "RLY_Muscular", "RLY_Thin"))


class CentredMeanDistanceTests(unittest.TestCase):
    def test_the_metric_ignores_a_pure_translation(self):
        verts = _grid()
        self.assertAlmostEqual(centred_mean_distance(verts, verts + 50.0), 0.0)

    def test_identical_geometry_measures_zero(self):
        verts = _grid()
        self.assertEqual(centred_mean_distance(verts, verts.copy()), 0.0)

    def test_a_real_difference_is_measured(self):
        verts = _grid()
        moved = verts.copy()
        moved[0] += 10.0
        self.assertGreater(centred_mean_distance(verts, moved), 0.0)


class ClusterTests(unittest.TestCase):
    def test_identical_shapes_collapse_to_one_cluster_listing_both_members(self):
        verts = _grid()
        clusters = cluster_geometries([_shape("a.nif", verts),
                                       _shape("b.nif", verts.copy())])
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].members, ["a.nif", "b.nif"])
        self.assertEqual(clusters[0].representative, "a.nif")

    def test_a_translated_copy_is_the_same_geometry(self):
        verts = _grid()
        clusters = cluster_geometries([_shape("a.nif", verts),
                                       _shape("b.nif", verts + 120.0)])
        self.assertEqual(len(clusters), 1)

    def test_shapes_further_apart_than_the_limit_stay_separate(self):
        clusters = cluster_geometries([_shape("a.nif", _grid(seed=1)),
                                       _shape("b.nif", _grid(seed=2))])
        self.assertEqual(len(clusters), 2)

    def test_shapes_of_different_vertex_counts_never_cluster(self):
        clusters = cluster_geometries([_shape("a.nif", _grid(20)),
                                       _shape("b.nif", _grid(21))])
        self.assertEqual(len(clusters), 2)

    def test_clusters_never_mix_sexes(self):
        verts = _grid()
        clusters = cluster_geometries([_shape("a.nif", verts, sex="female"),
                                       _shape("b.nif", verts.copy(), sex="male")])
        self.assertEqual(len(clusters), 2)
        self.assertEqual({c.sex for c in clusters}, {"female", "male"})

    def test_a_cluster_inherits_the_representative_part_and_geometry(self):
        verts = _grid()
        clusters = cluster_geometries([_shape("a.nif", verts, part="eyebrows")])
        self.assertEqual(clusters[0].part, "eyebrows")
        np.testing.assert_array_equal(clusters[0].verts, verts)

    def test_a_tighter_limit_splits_what_a_looser_one_merged(self):
        verts = _grid(seed=3)
        near = verts + np.array([0.0, 0.0, 0.0])
        near[0] += 8.0                     # one vertex moved, ~0.4 mean over 20
        loose = cluster_geometries([_shape("a.nif", verts),
                                    _shape("b.nif", near)], limit=1.5)
        tight = cluster_geometries([_shape("a.nif", verts),
                                    _shape("b.nif", near)], limit=0.01)
        self.assertEqual((len(loose), len(tight)), (1, 2))

    def test_an_empty_input_clusters_to_nothing(self):
        self.assertEqual(cluster_geometries([]), [])


class VerifyTests(unittest.TestCase):
    def test_every_input_shape_matches_a_cluster_it_produced(self):
        shapes = [_shape("a.nif", _grid(seed=1)),
                  _shape("b.nif", _grid(seed=2)),
                  _shape("c.nif", _grid(30, seed=3), sex="male")]
        clusters = cluster_geometries(shapes)
        result = verify_against_clusters(shapes, clusters)
        self.assertEqual(result.matched, 3)
        self.assertEqual(result.unmatched, [])

    def test_a_shape_whose_cluster_was_dropped_is_reported_unmatched(self):
        # The two geometries must be further apart than the plugin's own
        # acceptance, not merely than the clustering limit: at scale 1.0 these
        # measure 1.81 apart, which is a separate cluster the plugin would
        # still accept for the other shape. That headroom is the reason the
        # clustering limit is tighter than SAME_MESH_LIMIT, and a test that
        # ignored it would be asserting the wrong thing.
        shapes = [_shape("a.nif", _grid(scale=4.0, seed=1)),
                  _shape("b.nif", _grid(scale=4.0, seed=2))]
        self.assertGreater(
            centred_mean_distance(shapes[0].verts, shapes[1].verts),
            SAME_MESH_LIMIT)
        clusters = cluster_geometries(shapes)
        self.assertEqual(len(clusters), 2)
        result = verify_against_clusters(shapes, clusters[1:])
        self.assertEqual(result.matched, 1)
        self.assertEqual(result.unmatched, ["a.nif"])

    def test_a_separate_cluster_within_the_plugin_threshold_still_matches(self):
        # The flip side, stated so the behaviour is deliberate: two geometries
        # 1.81 apart ship as two sets, and either one would be accepted for
        # either shape. The plugin picks the closest, so shipping both is what
        # makes the right one win.
        shapes = [_shape("a.nif", _grid(seed=1)),
                  _shape("b.nif", _grid(seed=2))]
        clusters = cluster_geometries(shapes)
        self.assertEqual(len(clusters), 2)
        self.assertEqual(
            verify_against_clusters(shapes, clusters[1:]).unmatched, [])

    def test_a_shape_with_no_set_for_its_sex_is_unmatched(self):
        verts = _grid()
        female = [_shape("f.nif", verts, sex="female")]
        clusters = cluster_geometries(female)
        male = [_shape("m.nif", verts.copy(), sex="male")]
        self.assertEqual(verify_against_clusters(male, clusters).unmatched,
                         ["m.nif"])

    def test_the_worst_distance_is_reported_even_when_everything_matched(self):
        verts = _grid(seed=4)
        near = verts.copy()
        near[0] += 4.0
        shapes = [_shape("a.nif", verts), _shape("b.nif", near)]
        clusters = cluster_geometries(shapes)
        result = verify_against_clusters(shapes, clusters)
        self.assertEqual(result.unmatched, [])
        self.assertGreater(result.worst_distance, 0.0)
        self.assertLess(result.worst_distance, SAME_MESH_LIMIT)

    def test_a_member_sitting_beyond_the_plugin_threshold_is_unmatched(self):
        # Two shapes clustered under a limit looser than the plugin's own
        # acceptance: the member then cannot match the set shipped for it.
        verts = _grid(seed=5)
        far = verts.copy()
        far += np.linspace(0, 10, verts.size).reshape(verts.shape)
        shapes = [_shape("a.nif", verts), _shape("b.nif", far)]
        clusters = cluster_geometries(shapes, limit=1000.0)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(verify_against_clusters(shapes, clusters).unmatched,
                         ["b.nif"])

    def test_verification_reports_nothing_unmatched_for_no_shapes(self):
        result = verify_against_clusters([], [])
        self.assertEqual((result.matched, result.unmatched), (0, []))


class HeadReferenceTests(unittest.TestCase):
    # The shipped FACE_FemaleHead.tri really does hold
    # ['OLD_Heavy', 'RLY_Muscular', 'RLY_Thin', 'RLY_Heavy'] — a stale morph
    # first and RLY_Heavy last. Reading by position would write OLD_Heavy's
    # deltas under RLY_Heavy's name, and nothing downstream could tell.
    SHIPPED_ORDER = [("OLD_Heavy", 9.0), ("RLY_Muscular", 2.0),
                     ("RLY_Thin", 3.0), ("RLY_Heavy", 1.0)]

    def test_morphs_are_selected_by_name_not_by_position(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _write_reference(folder, "FACE_FemaleHead.tri",
                                    self.SHIPPED_ORDER)
            ref = load_head_reference(path, "female")
            self.assertAlmostEqual(float(ref.deltas["RLY_Heavy"].max()), 1.0,
                                   places=4)
            self.assertAlmostEqual(float(ref.deltas["RLY_Muscular"].max()), 2.0,
                                   places=4)
            self.assertAlmostEqual(float(ref.deltas["RLY_Thin"].max()), 3.0,
                                   places=4)

    def test_only_the_three_rly_morphs_are_carried(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _write_reference(folder, "ref.tri", self.SHIPPED_ORDER)
            ref = load_head_reference(path, "female")
            self.assertEqual(sorted(ref.deltas), sorted(MORPHS))

    def test_a_reference_missing_a_morph_raises_naming_it(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _write_reference(folder, "ref.tri",
                                    [("RLY_Heavy", 1.0), ("RLY_Thin", 3.0)])
            with self.assertRaisesRegex(ValueError, "RLY_Muscular"):
                load_head_reference(path, "female")

    def test_a_reference_with_no_faces_raises(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _write_reference(folder, "flat.tri", self.SHIPPED_ORDER,
                                    with_faces=False)
            with self.assertRaisesRegex(ValueError, "flat.tri"):
                load_head_reference(path, "female")

    def test_both_candidate_frames_are_offered(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _write_reference(folder, "ref.tri", self.SHIPPED_ORDER)
            ref = load_head_reference(path, "male")
            self.assertEqual(ref.sex, "male")
            np.testing.assert_allclose(ref.surface_head, ref.verts)
            np.testing.assert_allclose(ref.surface_origin,
                                       ref.verts - HEAD_OFFSET)


class PickFrameTests(unittest.TestCase):
    MORPHS_IN = [("RLY_Heavy", 1.0), ("RLY_Muscular", 2.0), ("RLY_Thin", 3.0)]

    def _ref(self, folder):
        return load_head_reference(
            _write_reference(folder, "ref.tri", self.MORPHS_IN), "female")

    def _near_surface(self, n: int = 40, *, offset: float = 0.5):
        rng = np.random.default_rng(7)
        xy = rng.uniform(-4.0, 4.0, size=(n, 2))
        z = np.full(n, HEAD_OFFSET[2] + offset)
        return np.column_stack([xy, z])

    def test_a_head_height_shape_picks_the_head_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            choice = pick_frame(self._ref(folder), self._near_surface())
            self.assertEqual(choice.name, "head")
            self.assertLess(choice.median_distance, 1.0)

    def test_an_origin_centred_shape_picks_the_origin_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            verts = self._near_surface() - HEAD_OFFSET
            choice = pick_frame(self._ref(folder), verts)
            self.assertEqual(choice.name, "origin")
            self.assertLess(choice.median_distance, 1.0)

    def test_the_chosen_surface_is_the_one_named(self):
        with tempfile.TemporaryDirectory() as folder:
            ref = self._ref(folder)
            choice = pick_frame(ref, self._near_surface() - HEAD_OFFSET)
            np.testing.assert_allclose(choice.surface, ref.surface_origin)

    def test_the_sample_cap_does_not_change_the_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            ref = self._ref(folder)
            verts = self._near_surface(500)
            self.assertEqual(pick_frame(ref, verts, sample=4).name,
                             pick_frame(ref, verts, sample=256).name)

    def test_a_shape_with_no_vertices_does_not_raise(self):
        with tempfile.TemporaryDirectory() as folder:
            choice = pick_frame(self._ref(folder), np.zeros((0, 3)))
            self.assertIn(choice.name, ("head", "origin"))

    def test_a_shape_against_the_surface_reports_a_small_contact_distance(self):
        with tempfile.TemporaryDirectory() as folder:
            choice = pick_frame(self._ref(folder),
                                self._near_surface(offset=0.2))
            self.assertLess(choice.contact_distance, 1.0)

    def test_a_shape_that_touches_nothing_reports_a_large_contact_distance(self):
        # The measured case: BG3's orin_misc01 comes no closer than 31 units to
        # the head in either frame, which is what makes its frame choice a coin
        # flip. contact_distance is the number that says the shape is detached.
        with tempfile.TemporaryDirectory() as folder:
            choice = pick_frame(self._ref(folder),
                                self._near_surface(offset=30.0))
            self.assertGreater(choice.contact_distance, 14.0)


class BuildSetTests(unittest.TestCase):
    """The bytes the plugin will read, and the field it will apply."""

    # A reference whose RLY_Heavy moves every vertex exactly 1.0 along z, so a
    # wrapped delta is readable as a weight rather than as a shape.
    MORPHS_IN = [("RLY_Heavy", 1.0), ("RLY_Muscular", 2.0), ("RLY_Thin", 3.0)]

    def _ref(self, folder):
        return load_head_reference(
            _write_reference(folder, "ref.tri", self.MORPHS_IN), "female")

    def _cluster(self, verts, *, part="hair"):
        return cluster_geometries([_shape("a.nif", verts, part=part)])[0]

    def _above(self, offset: float, n: int = 12):
        rng = np.random.default_rng(11)
        xy = rng.uniform(-4.0, 4.0, size=(n, 2))
        return np.column_stack([xy, np.full(n, HEAD_OFFSET[2] + offset)])

    def _build(self, folder, verts):
        ref = self._ref(folder)
        return ref, build_set(ref, self._cluster(verts),
                              preset=DEFAULT_PRESET,
                              frame=pick_frame(ref, verts))

    def test_the_default_preset_carries_the_measured_falloff_numbers(self):
        self.assertEqual(
            (DEFAULT_PRESET.falloff, DEFAULT_PRESET.falloff_inner,
             DEFAULT_PRESET.smooth), (14.0, 4.0, 0))

    def test_a_minimal_tri_round_trips_with_no_faces(self):
        verts = _grid(30)
        back = read_tri(write_tri(minimal_tri(verts)))
        self.assertEqual(back.vertex_count, 30)
        self.assertEqual(len(back.faces), 0)
        np.testing.assert_allclose(back.verts, verts.astype("<f4"))

    def test_declaring_no_faces_is_what_makes_the_round_trip_possible(self):
        # Pins the codec constraint minimal_tri is built around: read_tri
        # always consumes polyTris * 12 bytes of UV faces, so a TriFile written
        # from scratch WITH faces and no UV-face block cannot be read back.
        # Nothing in this pipeline writes one — this test is why.
        from fmp.tri import TriError
        faced = minimal_tri(_grid(30))
        faced.faces = np.asarray([(0, 1, 2)], dtype="<u4")
        with self.assertRaises(TriError):
            read_tri(write_tri(faced))

    def test_a_built_set_carries_exactly_the_three_morphs_in_order(self):
        with tempfile.TemporaryDirectory() as folder:
            _ref, built = self._build(folder, self._above(0.03))
            self.assertEqual(read_tri(built.data).morph_names(), list(MORPHS))

    def test_a_built_set_keeps_the_shape_own_base_vertices(self):
        # The plugin matches on these, so they must be the mesh as authored.
        verts = self._above(0.03)
        with tempfile.TemporaryDirectory() as folder:
            _ref, built = self._build(folder, verts)
            np.testing.assert_allclose(read_tri(built.data).verts,
                                       verts.astype("<f4"))

    def test_a_vertex_lying_on_the_surface_keeps_the_full_delta(self):
        with tempfile.TemporaryDirectory() as folder:
            _ref, built = self._build(folder, self._above(0.03))
            heavy = [m for m in read_tri(built.data).morphs
                     if m.name == "RLY_Heavy"][0]
            magnitudes = np.linalg.norm(heavy.deltas(), axis=1)
            np.testing.assert_allclose(magnitudes, 1.0, atol=1e-3)

    def test_a_vertex_beyond_the_falloff_is_zeroed(self):
        with tempfile.TemporaryDirectory() as folder:
            _ref, built = self._build(folder, self._above(20.0))
            heavy = [m for m in read_tri(built.data).morphs
                     if m.name == "RLY_Heavy"][0]
            self.assertEqual(float(np.abs(heavy.deltas()).max()), 0.0)

    def test_a_preset_with_no_falloff_carries_the_delta_out_to_any_distance(self):
        free = WrapPreset("none", falloff=None, falloff_inner=0.0, smooth=0)
        verts = self._above(20.0)
        with tempfile.TemporaryDirectory() as folder:
            ref = self._ref(folder)
            built = build_set(ref, self._cluster(verts), preset=free,
                              frame=pick_frame(ref, verts))
            heavy = [m for m in read_tri(built.data).morphs
                     if m.name == "RLY_Heavy"][0]
            self.assertGreater(float(np.abs(heavy.deltas()).max()), 0.9)

    def test_the_metrics_report_the_wrap_distance_and_peak_delta(self):
        with tempfile.TemporaryDirectory() as folder:
            _ref, built = self._build(folder, self._above(0.5))
            for key in ("wrap_dist_p50", "wrap_dist_max", "delta_max"):
                self.assertIn(key, built.metrics)
            self.assertAlmostEqual(built.metrics["wrap_dist_p50"], 0.5, places=3)

    def test_a_vertex_count_mismatch_in_the_readback_raises(self):
        # The guard that makes "a file MPF cannot parse is worse than no file"
        # true: the bytes are read back before the run is called a success.
        verts = self._above(0.03)
        wrong = write_tri(minimal_tri(_grid(99)))
        with tempfile.TemporaryDirectory() as folder:
            ref = self._ref(folder)
            with mock.patch("core.headpart_library.write_tri",
                            return_value=wrong):
                with self.assertRaisesRegex(ValueError, "99"):
                    build_set(ref, self._cluster(verts), preset=DEFAULT_PRESET,
                              frame=pick_frame(ref, verts))


class SetFilenameTests(unittest.TestCase):
    def test_a_set_filename_encodes_tag_sex_and_vertex_count(self):
        self.assertEqual(set_filename("BG3", "female", 4014, set()),
                         "RLY_BG3_F_4014.tri")
        self.assertEqual(set_filename("BG3", "male", 4014, set()),
                         "RLY_BG3_M_4014.tri")

    def test_two_geometries_sharing_a_vertex_count_get_distinct_names(self):
        # The plugin stores a vector per (sex, vertex count) and picks the
        # closest, so shipping several is correct — but the manifest dedupes by
        # filename, so they must not collide.
        used: set[str] = set()
        first = set_filename("BG3", "female", 4014, used)
        second = set_filename("BG3", "female", 4014, used)
        third = set_filename("BG3", "female", 4014, used)
        self.assertEqual([first, second, third],
                         ["RLY_BG3_F_4014.tri", "RLY_BG3_F_4014_b.tri",
                          "RLY_BG3_F_4014_c.tri"])

    def test_a_tag_is_reduced_to_filename_safe_characters(self):
        self.assertEqual(sanitize_tag("BG3 Some Hairs v1.1!"),
                         "BG3SomeHairsv11")
        self.assertEqual(sanitize_tag("   "), "Mod")


class ManifestTests(unittest.TestCase):
    def test_the_manifest_carries_one_sets_block_per_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "PhysiquesOfSkyrim_BG3.toml")
            write_manifest(path, [("RLY_BG3_F_10.tri", "female", "hair"),
                                  ("RLY_BG3_M_10.tri", "male", "facialhair")],
                           tag="BG3")
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(text.count("[[sets]]"), 2)
            self.assertIn('file = "RLY_BG3_F_10.tri"', text)
            self.assertIn('sex  = "male"', text)
            self.assertIn('part = "facialhair"', text)
            self.assertIn("BG3", text.splitlines()[0])

    def test_the_manifest_is_valid_toml(self):
        import tomllib
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "m.toml")
            write_manifest(path, [("a.tri", "female", "hair")], tag="X")
            with open(path, "rb") as handle:
                parsed = tomllib.load(handle)
            self.assertEqual(parsed["sets"],
                             [{"file": "a.tri", "sex": "female",
                               "part": "hair"}])


class BuildLibraryTests(unittest.TestCase):
    MORPHS_IN = [("RLY_Heavy", 1.0), ("RLY_Muscular", 2.0), ("RLY_Thin", 3.0)]

    def _refs(self, folder):
        return {
            sex: load_head_reference(
                _write_reference(folder, f"ref_{sex}.tri", self.MORPHS_IN), sex)
            for sex in ("female", "male")}

    def _near(self, n: int = 12, *, offset: float = 0.5, seed: int = 11):
        rng = np.random.default_rng(seed)
        xy = rng.uniform(-4.0, 4.0, size=(n, 2))
        return np.column_stack([xy, np.full(n, HEAD_OFFSET[2] + offset)])

    def _out(self, folder):
        return os.path.join(folder, MORPHS_SUBDIR)

    def test_writes_tris_and_one_manifest_under_the_morphs_subdir(self):
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("a.nif", self._near()),
                      _shape("b.nif", self._near(seed=12), sex="male")]
            report, summary = build_library(
                shapes, self._refs(folder), folder, LibraryOptions(tag="TEST"))
            written = sorted(os.listdir(self._out(folder)))
            self.assertEqual(written, ["PhysiquesOfSkyrim_TEST.toml",
                                       "RLY_TEST_F_12.tri",
                                       "RLY_TEST_M_12.tri"])
            self.assertEqual(summary.sets, 2)
            self.assertEqual(summary.verified, 2)
            self.assertEqual(summary.unmatched, [])
            self.assertEqual([f.status for f in report.files],
                             ["patched", "patched"])

    def test_a_file_result_is_labelled_by_relpath_and_shape(self):
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("a.nif", self._near(), shape="HairA")]
            report, _summary = build_library(
                shapes, self._refs(folder), folder, LibraryOptions(tag="T"))
            self.assertEqual(report.files[0].relpath, "a.nif#HairA")

    def test_members_of_one_cluster_all_point_at_the_same_set(self):
        verts = self._near()
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("a.nif", verts), _shape("b.nif", verts.copy())]
            report, summary = build_library(
                shapes, self._refs(folder), folder, LibraryOptions(tag="T"))
            self.assertEqual(summary.sets, 1)
            outputs = {os.path.basename(f.output) for f in report.files}
            self.assertEqual(outputs, {"RLY_T_F_12.tri"})

    def test_a_dry_run_writes_nothing_but_still_reports_the_set_count(self):
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("a.nif", self._near())]
            _report, summary = build_library(
                shapes, self._refs(folder), folder,
                LibraryOptions(tag="T", dry_run=True))
            self.assertEqual(summary.sets, 1)
            self.assertGreater(summary.bytes_written, 0)
            self.assertFalse(os.path.isdir(self._out(folder)))
            self.assertIsNone(summary.manifest)

    def test_an_unclassified_shape_is_skipped_with_a_warning_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("a.nif", self._near(), sex=None)]
            report, summary = build_library(
                shapes, self._refs(folder), folder, LibraryOptions(tag="T"))
            self.assertEqual(report.files[0].status, "skipped")
            self.assertTrue(report.files[0].warnings)
            self.assertEqual(summary.sets, 0)

    def test_unclassified_both_ships_the_shape_for_both_sexes(self):
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("a.nif", self._near(), sex=None)]
            report, summary = build_library(
                shapes, self._refs(folder), folder,
                LibraryOptions(tag="T", unclassified_both=True))
            self.assertEqual(summary.sets, 2)
            self.assertEqual(report.files[0].status, "patched")
            names = sorted(os.path.basename(p)
                           for p in os.listdir(self._out(folder))
                           if p.endswith(".tri"))
            self.assertEqual(names, ["RLY_T_F_12.tri", "RLY_T_M_12.tri"])

    def test_a_placeholder_shape_is_skipped_with_its_reason(self):
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("stub.nif", _grid(4))]
            report, summary = build_library(
                shapes, self._refs(folder), folder, LibraryOptions(tag="T"))
            self.assertEqual(report.files[0].status, "skipped")
            self.assertIn("placeholder", " ".join(report.files[0].warnings))
            self.assertEqual(summary.sets, 0)

    def test_a_shape_detached_from_the_head_is_skipped_not_shipped_as_zeros(self):
        # The measured case: six of 240 real shapes come no closer than 14.5
        # units to the head in either frame. Past the falloff radius every
        # delta is zero by construction, so a set for them is a file of zeros
        # and leaving it out leaves the game behaving identically.
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("braid.nif", self._near(offset=30.0))]
            report, summary = build_library(
                shapes, self._refs(folder), folder, LibraryOptions(tag="T"))
            self.assertEqual(report.files[0].status, "skipped")
            self.assertIn("detached", " ".join(report.files[0].warnings))
            self.assertEqual(summary.sets, 0)

    def test_a_detached_shape_is_shipped_when_the_falloff_is_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("braid.nif", self._near(offset=30.0))]
            _report, summary = build_library(
                shapes, self._refs(folder), folder,
                LibraryOptions(tag="T", falloff=None))
            self.assertEqual(summary.sets, 1)

    def test_a_sex_with_no_reference_head_fails_rather_than_silently_skips(self):
        with tempfile.TemporaryDirectory() as folder:
            refs = self._refs(folder)
            del refs["male"]
            shapes = [_shape("m.nif", self._near(), sex="male")]
            report, summary = build_library(
                shapes, refs, folder, LibraryOptions(tag="T"))
            self.assertEqual(report.files[0].status, "failed")
            self.assertIn("male", report.files[0].error)
            self.assertEqual(summary.sets, 0)

    def test_a_run_whose_verification_leaves_a_shape_unmatched_fails_it(self):
        # Clustering far looser than the plugin's acceptance ships one set for
        # two geometries the plugin would not both match.
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("a.nif", _grid(30, scale=4.0, seed=1)),
                      _shape("b.nif", _grid(30, scale=4.0, seed=2))]
            report, summary = build_library(
                shapes, self._refs(folder), folder,
                LibraryOptions(tag="T", cluster_limit=1000.0, falloff=None))
            self.assertEqual(summary.sets, 1)
            self.assertTrue(summary.unmatched)
            self.assertIn("failed", [f.status for f in report.files])

    def test_the_summary_reports_the_frame_split_and_wrap_distances(self):
        with tempfile.TemporaryDirectory() as folder:
            shapes = [_shape("head.nif", self._near()),
                      _shape("origin.nif", self._near(seed=13) - HEAD_OFFSET)]
            _report, summary = build_library(
                shapes, self._refs(folder), folder, LibraryOptions(tag="T"))
            self.assertEqual(summary.frames, {"head": 1, "origin": 1})
            self.assertEqual(summary.shapes, 2)
            self.assertAlmostEqual(summary.median_wrap_p50, 0.5, places=3)

    def test_no_shapes_at_all_produces_an_empty_report_not_an_error(self):
        with tempfile.TemporaryDirectory() as folder:
            report, summary = build_library(
                [], self._refs(folder), folder, LibraryOptions(tag="T"))
            self.assertEqual(report.files, [])
            self.assertEqual((summary.sets, summary.shapes), (0, 0))


class RejectTests(unittest.TestCase):
    def test_a_shape_with_a_nan_vertex_is_rejected_with_a_reason(self):
        verts = _grid()
        verts[3, 1] = np.nan
        self.assertEqual(reject_reason(_shape("a.nif", verts)),
                         "non-finite vertices")

    def test_an_infinite_vertex_is_rejected_too(self):
        verts = _grid()
        verts[0, 0] = np.inf
        self.assertEqual(reject_reason(_shape("a.nif", verts)),
                         "non-finite vertices")

    def test_a_shape_under_the_vertex_floor_is_rejected_as_a_placeholder(self):
        self.assertEqual(reject_reason(_shape("a.nif", _grid(MIN_VERTS - 1))),
                         "placeholder")

    def test_a_usable_shape_is_not_rejected(self):
        self.assertIsNone(reject_reason(_shape("a.nif", _grid(MIN_VERTS))))


if __name__ == "__main__":
    unittest.main()
