"""Coverage for the surviving TRI-transfer mode's sex detection.

The old rule tested for `_f` / `f_` / `female`. BG3 Some Hairs names its meshes
`gortash_hair F.tri`, with a space, so 28 of its 38 TRIs came back `unknown` and
`patch_headparts` skipped them "for safety". The rule now strips one character
rather than matching a separator, and only reads the letter as the sex when the
opposite file is actually there.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.headpart_classify import collect_stems
from core.headpart_patcher import detect_headpart_sex

BG3 = ["gortash_hair F.tri", "gortash_hair M.tri", "gortash_hairHL F.tri",
       "gortash_hairHL M.tri", "scalp F.tri", "scalp M.tri",
       "orin_hair_base F.tri", "orin_hair_base M.tri"]


class DetectHeadpartSexTests(unittest.TestCase):
    def test_a_space_separated_pair_is_no_longer_unknown(self):
        stems = collect_stems(BG3)
        self.assertEqual(
            detect_headpart_sex("meshes\\x\\gortash_hair F.tri", stems=stems),
            "female")
        self.assertEqual(
            detect_headpart_sex("meshes\\x\\gortash_hair M.tri", stems=stems),
            "male")

    def test_every_bg3_name_resolves_once_its_pair_is_known(self):
        stems = collect_stems(BG3)
        unknown = [name for name in BG3
                   if detect_headpart_sex(name, stems=stems) == "unknown"]
        self.assertEqual(unknown, [])

    def test_a_lone_space_separated_letter_stays_unknown(self):
        stems = collect_stems(["gortash_hair F.tri"])
        self.assertEqual(
            detect_headpart_sex("meshes\\x\\gortash_hair F.tri", stems=stems),
            "unknown")

    def test_without_stems_the_old_conservative_answer_is_kept(self):
        # The pairing rule needs to know what else is in the scan. Called with
        # nothing, it must not start guessing from a trailing letter.
        self.assertEqual(detect_headpart_sex("gortash_hair F.tri"), "unknown")

    def test_beard_still_reads_as_male(self):
        self.assertEqual(detect_headpart_sex("meshes\\beard11.tri"), "male")
        self.assertEqual(detect_headpart_sex("meshes\\beards\\x.tri"), "male")

    def test_female_is_not_read_as_male(self):
        self.assertEqual(detect_headpart_sex("femaledarkelfhair01.tri"),
                         "female")
        self.assertEqual(detect_headpart_sex("meshes\\hair\\female\\x.tri"),
                         "female")

    def test_the_vanilla_underscore_pair_resolves(self):
        stems = collect_stems(["HairShortHumanF.tri", "HairShortHumanM.tri"])
        self.assertEqual(
            detect_headpart_sex("HairShortHumanF.tri", stems=stems), "female")

    def test_something_with_no_evidence_at_all_is_unknown(self):
        self.assertEqual(detect_headpart_sex("meshes\\hair\\911.tri"),
                         "unknown")


if __name__ == "__main__":
    unittest.main()
