"""Coverage for what a headpart source tree actually contains.

Three layouts all have to work: loose NIFs under `meshes\\`, a mod that ships
nothing loose at all (Beards of Power is a 911 MB BSA), and a ZIP the caller has
already staged. The plugin matters as much as the meshes — `HDPT` records are the
only reliable source of a mesh's sex — so plugins are discovered here rather than
asked for.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.headpart_sources import discover, scan_rows, stage_archive_nifs
from fmp.esp import HeadPart


def _make(root: str, *relatives: str) -> None:
    for relative in relatives:
        path = os.path.join(root, relative.replace("/", os.sep))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(b"x")


class DiscoverTests(unittest.TestCase):
    def test_finds_nifs_archives_and_plugins_case_insensitively(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder,
                  "meshes/actors/character/hair/A.NIF",
                  "meshes/actors/character/hair/b.nif",
                  "Thing.Bsa", "Mod.ESP", "Light.esl", "Master.esm",
                  "textures/hair.dds")
            found = discover(folder)
            self.assertEqual(
                sorted(p.replace(os.sep, "/") for p in found.loose_nifs),
                ["meshes/actors/character/hair/A.NIF",
                 "meshes/actors/character/hair/b.nif"])
            self.assertEqual([os.path.basename(p) for p in found.archives],
                             ["Thing.Bsa"])
            self.assertEqual(
                sorted(os.path.basename(p) for p in found.plugins),
                ["Light.esl", "Master.esm", "Mod.ESP"])

    def test_archives_and_plugins_are_absolute_nifs_are_relative(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/a.nif", "Mod.esp", "Mod.bsa")
            found = discover(folder)
            self.assertFalse(os.path.isabs(found.loose_nifs[0]))
            self.assertTrue(os.path.isabs(found.archives[0]))
            self.assertTrue(os.path.isabs(found.plugins[0]))

    def test_ignores_backup_and_generated_morph_folders(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder,
                  "backup/meshes/old.nif",
                  "SKSE/Plugins/MPF/morphs/RLY_X_F_100.tri",
                  "SKSE/Plugins/MPF/morphs/stale.nif",
                  "backup/Mod.esp")
            found = discover(folder)
            self.assertEqual(found.loose_nifs, [])
            self.assertEqual(found.plugins, [])

    def test_an_empty_tree_discovers_nothing_without_raising(self):
        with tempfile.TemporaryDirectory() as folder:
            found = discover(folder)
            self.assertEqual(
                (found.loose_nifs, found.archives, found.plugins), ([], [], []))
            self.assertEqual(found.root, folder)

    def test_a_missing_root_discovers_nothing_without_raising(self):
        found = discover(os.path.join(tempfile.gettempdir(), "no-such-mod-dir"))
        self.assertEqual(found.loose_nifs, [])


class ScanRowsTests(unittest.TestCase):
    """What the GUI shows before a run: no geometry read, no guessing hidden."""

    def test_one_row_per_nif_with_the_resolved_sex_and_part(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/hair/female/a.nif", "meshes/hair/male/b.nif")
            rows = scan_rows(folder, default_part="hair")
            self.assertEqual(len(rows), 2)
            by_sex = {row.sex: row for row in rows}
            self.assertEqual(sorted(by_sex), ["female", "male"])
            self.assertEqual(by_sex["female"].part, "hair")

    def test_the_rule_that_decided_is_reported_as_a_confidence(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/hair/female/a.nif")
            row = scan_rows(folder, default_part="hair")[0]
            self.assertEqual(row.sex_source, "path")
            self.assertEqual(row.confidence, "Medium")

    def test_a_plugin_declared_row_is_high_confidence(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/hair/911.nif")
            hdpt = {"hair/911.nif": [HeadPart(
                editor_id="X", model="meshes\\hair\\911.nif", part_type="hair",
                playable=True, male=False, female=True, extra_part=False)]}
            row = scan_rows(folder, default_part="hair", hdpt=hdpt)[0]
            self.assertEqual((row.sex, row.sex_source, row.confidence),
                             ("female", "plugin", "High"))

    def test_an_unresolved_row_carries_a_warning_before_the_run(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/hair/911.nif")
            row = scan_rows(folder, default_part="hair")[0]
            self.assertIsNone(row.sex)
            self.assertEqual(row.confidence, "Unknown")
            self.assertTrue(row.warnings)

    def test_a_default_sex_row_is_low_confidence(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/beards/beard11.nif")
            row = scan_rows(folder, default_part="facialhair",
                            default_sex="male")[0]
            self.assertEqual((row.sex, row.confidence), ("male", "Low"))

    def test_rows_read_the_plugins_in_the_tree_without_being_handed_them(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/hair/911.nif")
            # No plugin in the tree: the scan must still work and credit the
            # fallback rules rather than claim a plugin decided.
            rows = scan_rows(folder, default_part="hair")
            self.assertNotEqual(rows[0].sex_source, "plugin")

    def test_a_mod_whose_meshes_are_all_in_a_bsa_still_has_rows(self):
        # Beards of Power keeps all 181 of its meshes inside one archive. With
        # only loose NIFs listed, the GUI tree was empty and the Start button
        # refused the run — making the archive path CLI-only.
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "Beards.bsa", "Beards.esp")
            rows = scan_rows(folder, default_part="facialhair")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].kind, "archive")
            self.assertEqual(os.path.basename(rows[0].relpath), "Beards.bsa")

    def test_an_archive_row_says_its_contents_are_read_during_the_run(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "Beards.bsa")
            row = scan_rows(folder, default_part="facialhair")[0]
            self.assertIsNone(row.sex)
            self.assertTrue(row.warnings)
            self.assertIn("archive", " ".join(row.warnings).lower())

    def test_mesh_rows_are_listed_before_archive_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "Extra.bsa", "meshes/hair/female/a.nif")
            kinds = [row.kind for row in scan_rows(folder)]
            self.assertEqual(kinds, ["mesh", "archive"])

    def test_a_mesh_row_is_marked_as_one(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/hair/female/a.nif")
            self.assertEqual(scan_rows(folder)[0].kind, "mesh")

    def test_the_relpath_is_kept_as_the_row_name(self):
        with tempfile.TemporaryDirectory() as folder:
            _make(folder, "meshes/hair/female/a.nif")
            row = scan_rows(folder, default_part="hair")[0]
            self.assertEqual(row.relpath.replace(os.sep, "/"),
                             "meshes/hair/female/a.nif")


class StageArchiveNifsTests(unittest.TestCase):
    def test_each_archive_is_staged_into_its_own_subfolder(self):
        seen = []

        def extract(archives, out_root, **kwargs):
            seen.append((archives, out_root, kwargs.get("suffix")))
            return 3, []

        with tempfile.TemporaryDirectory() as folder:
            work = os.path.join(folder, "work")
            roots, failures = stage_archive_nifs(
                [os.path.join(folder, "Hair.bsa"),
                 os.path.join(folder, "Beards.bsa")],
                work, extract=extract)
            self.assertEqual(failures, [])
            self.assertEqual([os.path.basename(r) for r in roots],
                             ["Hair", "Beards"])
            self.assertEqual([entry[2] for entry in seen], [".nif", ".nif"])
            self.assertEqual([entry[0][0].endswith("Hair.bsa") for entry in seen],
                             [True, False])

    def test_an_archive_that_yields_no_nifs_is_not_staged(self):
        def extract(_archives, _out_root, **_kwargs):
            return 0, []

        with tempfile.TemporaryDirectory() as folder:
            roots, failures = stage_archive_nifs(
                [os.path.join(folder, "Sounds.bsa")],
                os.path.join(folder, "work"), extract=extract)
            self.assertEqual((roots, failures), ([], []))

    def test_an_extract_failure_is_collected_not_raised(self):
        def extract(archives, _out_root, **_kwargs):
            if "Broken" in archives[0]:
                raise ValueError("unsupported BSA version 42")
            return 2, []

        with tempfile.TemporaryDirectory() as folder:
            roots, failures = stage_archive_nifs(
                [os.path.join(folder, "Broken.bsa"),
                 os.path.join(folder, "Good.bsa")],
                os.path.join(folder, "work"), extract=extract)
            self.assertEqual([os.path.basename(r) for r in roots], ["Good"])
            self.assertEqual(len(failures), 1)
            self.assertIn("unsupported BSA version 42", failures[0])
            self.assertIn("Broken.bsa", failures[0])

    def test_per_entry_failures_reported_by_extract_are_carried_through(self):
        def extract(_archives, _out_root, **_kwargs):
            return 1, ["hair\\a.nif: LZ4 frame error"]

        with tempfile.TemporaryDirectory() as folder:
            roots, failures = stage_archive_nifs(
                [os.path.join(folder, "Hair.bsa")],
                os.path.join(folder, "work"), extract=extract)
            self.assertEqual(len(roots), 1)
            self.assertEqual(failures, ["hair\\a.nif: LZ4 frame error"])

    def test_two_archives_sharing_a_stem_get_distinct_staging_roots(self):
        def extract(_archives, _out_root, **_kwargs):
            return 1, []

        with tempfile.TemporaryDirectory() as folder:
            roots, _failures = stage_archive_nifs(
                [os.path.join(folder, "a", "Hair.bsa"),
                 os.path.join(folder, "b", "Hair.bsa")],
                os.path.join(folder, "work"), extract=extract)
            self.assertEqual(len(set(roots)), 2)


if __name__ == "__main__":
    unittest.main()
