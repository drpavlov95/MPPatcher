"""Coverage for the headparts CLI surface.

Two modes share one entry point, and which one is the default matters: library
mode is the only one that can serve a mod shipping no `.tri` at all, and it is
what the plugin reads by default. TRI mode remains for `Backend=racemenu`.

The absent-nifdump path is tested here because it is the one place where a quiet
fallback would be catastrophic: reading the mod's stub TRIs instead would build
morph sets that match no live mesh and report success.
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.headparts_cli import build_headparts_parser, run_headparts


def _parse(argv):
    return build_headparts_parser().parse_args(argv)


BASE = ["--headparts", "--source", "s", "--output-dir", "o"]


class ParserTests(unittest.TestCase):
    def test_library_is_the_default_mode(self):
        self.assertEqual(_parse(BASE).mode, "library")

    def test_tri_mode_is_selectable(self):
        self.assertEqual(_parse(BASE + ["--mode", "tri"]).mode, "tri")

    def test_an_unknown_mode_is_rejected(self):
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                _parse(BASE + ["--mode", "racemenu"])

    def test_tri_mode_still_accepts_only_missing_and_filter_file(self):
        args = _parse(BASE + ["--mode", "tri", "--only-missing",
                              "--filter-file", "f.txt"])
        self.assertTrue(args.only_missing)
        self.assertEqual(args.filter_file, "f.txt")

    def test_library_mode_accepts_tag_part_default_sex_and_cluster_limit(self):
        args = _parse(BASE + ["--tag", "BOP", "--part", "facialhair",
                              "--default-sex", "male", "--cluster-limit", "2.5",
                              "--unclassified-both"])
        self.assertEqual(args.tag, "BOP")
        self.assertEqual(args.part, "facialhair")
        self.assertEqual(args.default_sex, "male")
        self.assertEqual(args.cluster_limit, 2.5)
        self.assertTrue(args.unclassified_both)

    def test_the_falloff_numbers_default_to_the_measured_ones(self):
        args = _parse(BASE)
        self.assertEqual((args.falloff, args.falloff_inner), (14.0, 4.0))

    def test_the_falloff_can_be_disabled(self):
        self.assertEqual(_parse(BASE + ["--falloff", "0"]).falloff, 0.0)

    def test_a_default_sex_outside_the_two_is_rejected(self):
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                _parse(BASE + ["--default-sex", "both"])

    def test_source_and_output_dir_are_required(self):
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                _parse(["--headparts"])

    def test_an_explicit_nifdump_path_is_accepted(self):
        self.assertEqual(_parse(BASE + ["--nifdump", "x.exe"]).nifdump, "x.exe")


class TagDefaultTests(unittest.TestCase):
    def test_the_tag_defaults_to_the_sanitised_source_basename(self):
        from core.headparts_cli import default_tag
        self.assertEqual(default_tag(r"E:\mods\BG3 hairs 1.1"), "BG3hairs11")
        self.assertEqual(default_tag(r"E:\mods\Beards of Power.zip"),
                         "BeardsofPower")

    def test_a_trailing_separator_does_not_produce_an_empty_tag(self):
        from core.headparts_cli import default_tag
        self.assertEqual(default_tag(r"E:\mods\Apachii Sky Hair SSE" + os.sep),
                         "ApachiiSkyHairSSE")


class FilterSelectionTests(unittest.TestCase):
    """One filter file has to work for both modes."""

    def setUp(self):
        from core.headparts_cli import _is_selected
        self.selected = _is_selected

    def test_no_filter_selects_everything(self):
        self.assertTrue(self.selected(None, "meshes\\a.nif", "Hair"))

    def test_a_bare_path_selects_every_shape_in_that_nif(self):
        chosen = {"meshes/a.nif"}
        self.assertTrue(self.selected(chosen, "meshes\\a.nif", "Hair"))
        self.assertTrue(self.selected(chosen, "meshes\\a.nif", "Scalp"))

    def test_a_shape_qualified_entry_selects_only_that_shape(self):
        chosen = {"meshes/a.nif#Hair"}
        self.assertTrue(self.selected(chosen, "meshes\\a.nif", "Hair"))
        self.assertFalse(self.selected(chosen, "meshes\\a.nif", "Scalp"))

    def test_an_unlisted_path_is_not_selected(self):
        self.assertFalse(self.selected({"meshes/b.nif"}, "meshes\\a.nif", "H"))


class NifdumpMissingTests(unittest.TestCase):
    def test_library_mode_stops_with_an_actionable_error(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "mod")
            os.makedirs(source)
            out = os.path.join(folder, "out")
            with mock.patch("core.headparts_cli.find_nifdump", return_value=""):
                with mock.patch("sys.stdout"):
                    code = run_headparts(
                        ["--headparts", "--source", source,
                         "--output-dir", out, "--tag", "T"])
        self.assertEqual(code, 2)

    def test_the_error_names_nifdump_and_the_override(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "mod")
            os.makedirs(source)
            printed = []
            with mock.patch("core.headparts_cli.find_nifdump", return_value=""):
                with mock.patch("builtins.print",
                                side_effect=lambda *a, **k: printed.append(
                                    " ".join(str(x) for x in a))):
                    run_headparts(["--headparts", "--source", source,
                                   "--output-dir", os.path.join(folder, "o"),
                                   "--tag", "T"])
            text = "\n".join(printed)
            self.assertIn("nifdump", text)
            self.assertIn("MPP_NIFDUMP", text)


class SilentSuccessTests(unittest.TestCase):
    """Paths where a run under-covered a mod and still exited 0."""

    def _source(self, folder):
        source = os.path.join(folder, "mod")
        os.makedirs(source, exist_ok=True)
        return source

    def test_a_filter_matching_nothing_is_an_error_not_an_empty_success(self):
        # Finding 7. A --filter-file whose spelling does not match nifdump's
        # relpaths (a case difference is enough) left no shapes at all, and the
        # CLI printed "Sets: 0 written / Verified: 0 of 0" and exited 0. The GUI
        # then reported "Patching complete — 0 meshes processed".
        from core.headparts_cli import _fail_on_empty_selection
        self.assertIsNotNone(_fail_on_empty_selection(
            shapes=[], geometries=3, selected={"meshes/a.nif"}))
        self.assertIsNone(_fail_on_empty_selection(
            shapes=[object()], geometries=3, selected={"meshes/a.nif"}))

    def test_an_empty_selection_error_names_what_was_offered(self):
        from core.headparts_cli import _fail_on_empty_selection
        message = _fail_on_empty_selection(
            shapes=[], geometries=2, selected={"MESHES/A.NIF"},
            offered=["meshes\\a.nif"])
        self.assertIn("meshes\\a.nif", message)
        self.assertIn("MESHES/A.NIF", message)

    def test_a_shortfall_against_nifdumps_own_count_is_reported(self):
        # Finding 3. nifdump exits 2 and emits [FAIL] per unreadable NIF, but
        # those were printed as warnings and dropped: a run that read 10 of 50
        # meshes reported "Verified 10 of 10" and exited 0.
        from core.headparts_cli import _geometry_shortfall
        self.assertIsNotNone(_geometry_shortfall(expected=50, read=10,
                                                 failures=["a.nif: cannot load"]))
        self.assertIsNone(_geometry_shortfall(expected=50, read=50,
                                              failures=[]))

    def test_a_shortfall_with_no_failures_is_still_reported(self):
        from core.headparts_cli import _geometry_shortfall
        message = _geometry_shortfall(expected=50, read=49, failures=[])
        self.assertIsNotNone(message)
        self.assertIn("50", message)
        self.assertIn("49", message)

    def test_dump_failures_alone_are_a_shortfall(self):
        from core.headparts_cli import _geometry_shortfall
        self.assertIsNotNone(_geometry_shortfall(
            expected=0, read=0, failures=["a.nif: cannot load"]))


class ArchiveSelectionTests(unittest.TestCase):
    """Finding 6: a filter built from loose rows silenced archive content."""

    def test_archives_all_is_the_default_so_nothing_is_silently_dropped(self):
        self.assertEqual(_parse(BASE).archives, "all")

    def test_archive_geometry_ignores_a_filter_built_from_loose_rows(self):
        from core.headparts_cli import _is_selected
        chosen = {"meshes/loose.nif"}
        # A mod with 3 loose NIFs beside a BSA holding 180: the user checks the
        # 3 rows, and the filter excluded all 180 extracted shapes while the run
        # reported success over 3 of 183 meshes.
        self.assertTrue(_is_selected(chosen, "meshes\\fromBsa.nif", "Beard",
                                     from_archive=True, archives="all"))
        self.assertFalse(_is_selected(chosen, "meshes\\fromBsa.nif", "Beard",
                                      from_archive=True, archives="none"))

    def test_loose_geometry_is_always_subject_to_the_filter(self):
        from core.headparts_cli import _is_selected
        chosen = {"meshes/loose.nif"}
        self.assertTrue(_is_selected(chosen, "meshes\\loose.nif", "Hair",
                                     from_archive=False, archives="all"))
        self.assertFalse(_is_selected(chosen, "meshes\\other.nif", "Hair",
                                      from_archive=False, archives="all"))


class ReferenceDiscoveryTests(unittest.TestCase):
    def test_library_mode_stops_when_no_reference_heads_can_be_found(self):
        with tempfile.TemporaryDirectory() as folder:
            source = os.path.join(folder, "mod")
            os.makedirs(source)
            printed = []
            with mock.patch("core.headparts_cli.find_nifdump",
                            return_value="nifdump.exe"):
                with mock.patch("core.headparts_cli.discover_references",
                                return_value=None):
                    with mock.patch("builtins.print",
                                    side_effect=lambda *a, **k: printed.append(
                                        " ".join(str(x) for x in a))):
                        code = run_headparts(
                            ["--headparts", "--source", source,
                             "--output-dir", os.path.join(folder, "o"),
                             "--tag", "T"])
            self.assertEqual(code, 2)
            self.assertIn("FACE_FemaleHead.tri", "\n".join(printed))


if __name__ == "__main__":
    unittest.main()
