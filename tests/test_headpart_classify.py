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

    def test_every_female_signal_is_tested_before_any_male_one(self):
        # Across both sources, not within each. An earlier version tested the
        # directory first and the filename second, which read
        # "meshes/male/femalehair01.nif" as male — a dead key — where the rule
        # it replaced read it as female. When the two disagree neither is
        # authoritative, so the tie goes to the older answer.
        found = classify("meshes\\hair\\male\\femaleish.nif")
        self.assertEqual((found.sex, found.sex_source), ("female", "filename"))

    def test_the_directory_decides_when_the_filename_says_nothing(self):
        found = classify("meshes\\hair\\male\\hair01.nif")
        self.assertEqual((found.sex, found.sex_source), ("male", "path"))

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


class LegacyConventionTests(unittest.TestCase):
    """Conventions the pre-2026-11-06 rule accepted, measured and kept.

    The old TRI-mode rule took `f_`/`m_` prefixes and `_f`/`_m` suffixes with no
    counterpart required. Replacing it wholesale with the pairing rule would
    have skipped a female-only mod naming its files `hair_f.tri` — the same
    class of loss the pairing rule was added to fix, for a different spelling.
    """

    def test_an_underscore_prefix_resolves_on_its_own(self):
        self.assertEqual(classify("hair/f_longbraid.nif").sex, "female")
        self.assertEqual(classify("hair/m_longbraid.nif").sex, "male")

    def test_an_underscore_suffix_resolves_without_a_counterpart(self):
        self.assertEqual(classify("hair/braid_f.nif").sex, "female")
        self.assertEqual(classify("hair/braid_m.nif").sex, "male")

    def test_a_filename_saying_female_beats_a_male_directory(self):
        # The old rule tested the filename first and read this as female.
        # Testing the directory first flipped it to male — a dead key.
        found = classify("meshes/male/femalehair01.nif")
        self.assertEqual(found.sex, "female")

    def test_a_male_filename_in_a_female_directory_is_female(self):
        # The mirror case. Neither source is authoritative, so the rule is the
        # one it has always been: female wins the tie, because a wrong male key
        # is as dead as a wrong female one and this is the older behaviour.
        self.assertEqual(classify("meshes/female/malehair01.nif").sex, "female")

    def test_a_bare_directory_still_decides_when_the_name_is_silent(self):
        self.assertEqual(classify("meshes/male/hair01.nif").sex, "male")


class AmbiguousRecordTests(unittest.TestCase):
    def test_a_both_sex_record_beside_a_single_sex_one_is_unresolved(self):
        # Minor 6 of the review, re-graded: this used to resolve "female" with
        # sex_source "plugin" — plugin-grade confidence for a decision the
        # plugin did not make — and the male side silently got no set.
        hdpt = _index(_record("meshes\\h.nif", female=True),
                      _record("meshes\\h.nif", female=True, male=True))
        found = classify("meshes\\h.nif", hdpt=hdpt)
        self.assertEqual((found.sex, found.sex_source), (None, "unresolved"))

    def test_two_records_agreeing_on_one_sex_still_resolve(self):
        hdpt = _index(_record("meshes\\h.nif", female=True),
                      _record("meshes\\h.nif", female=True))
        self.assertEqual(classify("meshes\\h.nif", hdpt=hdpt).sex, "female")


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
