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
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.headpart_library import (
    DEFAULT_CLUSTER_LIMIT, MIN_VERTS, MORPHS, SAME_MESH_LIMIT, ShapeInput,
    centred_mean_distance, cluster_geometries, reject_reason,
    verify_against_clusters,
)


def _grid(n: int = 20, *, scale: float = 1.0, seed: int = 0) -> np.ndarray:
    """A deterministic scatter of `n` vertices."""
    rng = np.random.default_rng(seed)
    return rng.normal(scale=scale, size=(n, 3))


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
