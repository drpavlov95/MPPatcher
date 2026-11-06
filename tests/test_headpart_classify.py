"""Coverage for resolving a headpart's sex and part type.

A wrong sex is the expensive failure: it files a morph set under a key no NPC
will ever look up, and nothing reports it — the hair simply never moves. So the
plugin's own `HDPT` record is the authority, the path and filename rules only
answer where no plugin declares the mesh, and anything still undecided is
reported rather than guessed.

The measured case this exists for: BG3 Some Hairs names its meshes
`gortash_hair F.nif`, with a space. The old `_f`/`f_` test read 28 of its 38
files as unknown and skipped them.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.headpart_classify import (
    Classification, classify, collect_stems, mesh_key,
)
from fmp.esp import HeadPart


def _record(model: str, *, female=False, male=False, part="hair",
            playable=True) -> HeadPart:
    return HeadPart(editor_id="X", model=model, part_type=part,
                    playable=playable, male=male, female=female,
                    extra_part=False)


def _index(*records: HeadPart) -> dict[str, list[HeadPart]]:
    out: dict[str, list[HeadPart]] = {}
    for record in records:
        out.setdefault(mesh_key(record.model), []).append(record)
    return out


class MeshKeyTests(unittest.TestCase):
    def test_reduces_both_spellings_to_the_tail_after_meshes(self):
        self.assertEqual(mesh_key("meshes\\actors\\hair\\A.NIF"),
                         "actors/hair/a.nif")
        self.assertEqual(mesh_key("Hair\\Beards.bsa\\meshes/actors/b.nif"),
                         "actors/b.nif")

    def test_a_path_with_no_meshes_segment_is_kept_whole(self):
        self.assertEqual(mesh_key("actors/hair/a.nif"), "actors/hair/a.nif")


class CollectStemsTests(unittest.TestCase):
    def test_stems_are_lowercased_basenames_without_the_extension(self):
        self.assertEqual(
            collect_stems(["meshes\\hair\\Gortash_Hair F.nif", "x/Y.NIF"]),
            frozenset({"gortash_hair f", "y"}))


class PluginAuthorityTests(unittest.TestCase):
    def test_a_single_sex_hdpt_record_decides_and_is_credited(self):
        hdpt = _index(_record("meshes\\hair\\911.nif", female=True))
        found = classify("meshes\\hair\\911.nif", hdpt=hdpt)
        self.assertEqual((found.sex, found.sex_source), ("female", "plugin"))

    def test_a_record_declared_for_both_sexes_is_unresolved_not_guessed(self):
        hdpt = _index(_record("meshes\\hair\\911.nif", female=True, male=True))
        found = classify("meshes\\hair\\911.nif", hdpt=hdpt)
        self.assertEqual((found.sex, found.sex_source), (None, "unresolved"))

    def test_two_records_disagreeing_on_sex_are_unresolved(self):
        hdpt = _index(_record("meshes\\hair\\a.nif", female=True),
                      _record("meshes\\hair\\a.nif", male=True))
        found = classify("meshes\\hair\\a.nif", hdpt=hdpt)
        self.assertEqual((found.sex, found.sex_source), (None, "unresolved"))

    def test_the_plugin_record_beats_a_contradicting_filename(self):
        # The mesh is named male but the plugin declares it female. KS Hairdos
        # reuses meshes across records; the record is what the game reads.
        hdpt = _index(_record("meshes\\hair\\malehair01.nif", female=True))
        found = classify("meshes\\hair\\malehair01.nif", hdpt=hdpt)
        self.assertEqual((found.sex, found.sex_source), ("female", "plugin"))

    def test_the_part_type_comes_from_the_record(self):
        hdpt = _index(_record("meshes\\brows\\b.nif", female=True,
                              part="eyebrows"))
        found = classify("meshes\\brows\\b.nif", hdpt=hdpt)
        self.assertEqual((found.part, found.part_source), ("eyebrows", "plugin"))

    def test_records_disagreeing_on_the_part_type_fall_back_to_the_default(self):
        hdpt = _index(_record("meshes\\h.nif", female=True, part="hair"),
                      _record("meshes\\h.nif", female=True, part="misc"))
        found = classify("meshes\\h.nif", hdpt=hdpt, default_part="hair")
        self.assertEqual((found.part, found.part_source), ("hair", "default"))

    def test_a_mesh_no_record_mentions_takes_the_default_part(self):
        hdpt = _index(_record("meshes\\other.nif", female=True))
        found = classify("meshes\\hair\\female\\x.nif", hdpt=hdpt,
                         default_part="facialhair")
        self.assertEqual((found.part, found.part_source),
                         ("facialhair", "default"))


class PathAndFilenameTests(unittest.TestCase):
    def test_female_in_the_path_beats_the_male_substring_it_contains(self):
        self.assertEqual(
            classify("meshes\\hair\\female\\x.nif").sex_source, "path")
        self.assertEqual(classify("meshes\\hair\\female\\x.nif").sex, "female")

    def test_female_in_the_filename_is_not_read_as_male(self):
        found = classify("meshes\\hair\\femaledarkelfhair01.nif")
        self.assertEqual((found.sex, found.sex_source), ("female", "filename"))

    def test_male_in_the_middle_of_a_filename_is_found(self):
        found = classify("meshes\\hair\\argonianmalehorns04hairline.nif")
        self.assertEqual((found.sex, found.sex_source), ("male", "filename"))

    def test_the_path_is_tested_before_the_filename(self):
        found = classify("meshes\\hair\\male\\femaleish.nif")
        self.assertEqual(found.sex_source, "path")

    def test_a_space_separated_pair_resolves_when_the_opposite_file_exists(self):
        stems = collect_stems(["gortash_hair F.nif", "gortash_hair M.nif"])
        found = classify("meshes\\x\\gortash_hair F.nif", stems=stems)
        self.assertEqual((found.sex, found.sex_source), ("female", "paired"))

    def test_the_vanilla_underscore_pair_also_resolves(self):
        stems = collect_stems(["HairShortHumanF.nif", "HairShortHumanM.nif"])
        found = classify("meshes\\HairShortHumanM.nif", stems=stems)
        self.assertEqual((found.sex, found.sex_source), ("male", "paired"))

    def test_a_lone_trailing_letter_is_not_evidence(self):
        stems = collect_stems(["gortash_hair F.nif"])
        found = classify("meshes\\x\\gortash_hair F.nif", stems=stems)
        self.assertEqual((found.sex, found.sex_source), (None, "unresolved"))

    def test_default_sex_applies_only_when_nothing_else_answered(self):
        found = classify("meshes\\beards\\beard11.nif", default_sex="male")
        self.assertEqual((found.sex, found.sex_source), ("male", "default"))
        kept = classify("meshes\\hair\\female\\x.nif", default_sex="male")
        self.assertEqual((kept.sex, kept.sex_source), ("female", "path"))

    def test_nothing_at_all_is_unresolved(self):
        found = classify("meshes\\hair\\911.nif")
        self.assertEqual((found.sex, found.sex_source), (None, "unresolved"))

    def test_classification_works_with_no_hdpt_index(self):
        found = classify("meshes\\hair\\female\\acerola.nif", hdpt=None)
        self.assertEqual((found.sex, found.sex_source), ("female", "path"))
        self.assertNotEqual(found.sex_source, "plugin")


class RealModShapeTests(unittest.TestCase):
    """The mod that measured 28 of 38 as unknown under the old rule."""

    BG3 = ["gortash_hair F.nif", "gortash_hair M.nif",
           "gortash_hairHL F.nif", "gortash_hairHL M.nif",
           "scalp F.nif", "scalp M.nif",
           "orin_hair_base F.nif", "orin_hair_base M.nif",
           "karlach_hair_f.nif", "karlach_hair_m.nif"]

    def test_every_bg3_name_resolves_from_its_pair_without_a_plugin(self):
        stems = collect_stems(self.BG3)
        unresolved = [name for name in self.BG3
                      if classify(name, stems=stems).sex is None]
        self.assertEqual(unresolved, [])

    def test_the_resolved_sexes_split_evenly(self):
        stems = collect_stems(self.BG3)
        sexes = [classify(name, stems=stems).sex for name in self.BG3]
        self.assertEqual((sexes.count("female"), sexes.count("male")), (5, 5))


class ClassificationShapeTests(unittest.TestCase):
    def test_the_dataclass_carries_all_four_fields(self):
        found = classify("meshes\\hair\\female\\x.nif")
        self.assertIsInstance(found, Classification)
        self.assertEqual(
            (found.sex, found.sex_source, found.part, found.part_source),
            ("female", "path", "hair", "default"))


if __name__ == "__main__":
    unittest.main()
