"""Regression tests for BodySlide-compatible group aggregation."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.slider_group_reader import read_all, read_all_physical, read_definitions


class SliderGroupReaderTests(unittest.TestCase):
    def test_same_named_groups_merge_members_and_keep_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "a.xml"), "w", encoding="utf-8") as stream:
                stream.write(
                    '<SliderGroups><Group name="3BA">'
                    '<Member name="OutfitA"/><Member name="Shared"/>'
                    '</Group></SliderGroups>'
                )
            with open(os.path.join(folder, "b.xml"), "w", encoding="utf-8") as stream:
                stream.write(
                    '<SliderGroups><Group name="3BA">'
                    '<Member name="Shared"/><Member name="OutfitB"/>'
                    '</Group></SliderGroups>'
                )

            definitions = read_definitions(folder)
            self.assertEqual(len(definitions), 2)
            group = read_all(folder)[0]
            self.assertEqual(group.name, "3BA")
            self.assertEqual(group.members, ["OutfitA", "Shared", "OutfitB"])
            self.assertEqual({os.path.basename(path) for path in group.source_files},
                             {"a.xml", "b.xml"})
            self.assertEqual(len(group.definitions), 2)

    def test_duplicate_physical_folder_is_not_scanned_twice(self):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, "groups.xml"), "w", encoding="utf-8") as stream:
                stream.write(
                    '<SliderGroups><Group name="Test">'
                    '<Member name="Outfit"/></Group></SliderGroups>'
                )
            groups = read_all_physical([folder, folder])
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0].members, ["Outfit"])
            self.assertEqual(len(groups[0].definitions), 1)

    def test_nested_xml_matches_bodyslide_recursive_scan(self):
        with tempfile.TemporaryDirectory() as folder:
            nested = os.path.join(folder, "nested")
            os.makedirs(nested)
            with open(os.path.join(nested, "groups.xml"), "w", encoding="utf-8") as stream:
                stream.write(
                    '<SliderGroups><Group name="Nested">'
                    '<Member name="Outfit"/></Group></SliderGroups>'
                )
            groups = read_all(folder)
            self.assertEqual([(g.name, g.members) for g in groups],
                             [("Nested", ["Outfit"])])


if __name__ == "__main__":
    unittest.main()
