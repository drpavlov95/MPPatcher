"""Focused regression tests for the model-backed MPPatcher UI state."""
import os
import sys
import unittest
from pathlib import Path

from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.outfit_scanner import (OutfitInfo, detect_gender_label,
                                 classify_metadata, classify_metadata_details)
from core.headpart_patcher import detect_headpart_sex
from core.slider_group_reader import SliderGroup, SliderGroupDefinition
from ui.main_window import MainWindow
from ui.main_window_ui import COL_REVEAL, COL_SMART, COL_OUTPUT


class StateModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow()
        self.window._all_groups = [
            SliderGroup(name="Group A", members=["Shared", "One"]),
            SliderGroup(name="Group B", members=["Shared", "Two"]),
        ]
        self.window._scan_data = {
            "Shared": OutfitInfo(name="Shared", gender="3BA", source_project="Project A.osp"),
            "One": OutfitInfo(name="One", gender="3BA", source_project="Project A.osp"),
            "Two": OutfitInfo(name="Two", gender="3BA", source_project="Project B.osp"),
        }
        self.window._refresh_body_group_filter()
        self.window._selected_outfits.clear()
        self.window._rebuild_tree()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_selection_survives_filter_and_duplicate_group_membership(self):
        first_project = self.window.group_tree.topLevelItem(0)
        shared = next(
            first_project.child(i) for i in range(first_project.childCount())
            if first_project.child(i).text(0) == "Shared"
        )
        self.assertEqual(shared.text(0), "Shared")
        shared.setCheckState(0, Qt.Checked)
        self.app.processEvents()

        self.assertEqual(self.window._selected_outfits, {("__outfits__", "Shared")})
        self.assertEqual(
            {m for group in self.window._checked_groups() for m in group.members},
            {"Shared"},
        )

        self.window._rebuild_tree("shared")
        self.assertEqual(self.window.group_tree.topLevelItemCount(), 1)
        self.assertEqual(
            self.window.group_tree.topLevelItem(0).child(0).checkState(0),
            Qt.Checked,
        )

    def test_body_group_filter_controls_membership_without_reorganising_by_metadata(self):
        index = self.window.body_group_filter_combo.findData("Group B")
        self.assertGreaterEqual(index, 0)
        self.window.body_group_filter_combo.setCurrentIndex(index)
        self.app.processEvents()
        self.assertEqual(self.window.group_tree.topLevelItemCount(), 2)
        names = {
            self.window.group_tree.topLevelItem(i).child(j).text(0)
            for i in range(self.window.group_tree.topLevelItemCount())
            for j in range(self.window.group_tree.topLevelItem(i).childCount())
        }
        self.assertEqual(names, {"Shared", "Two"})

    def test_missing_osp_uses_slidergroups_source_as_project_fallback(self):
        self.window._all_groups = [
            SliderGroup(
                name="Example",
                members=["Missing Outfit"],
                definitions=[SliderGroupDefinition(
                    name="Example",
                    source_xml="C:/Mods/Example/SliderGroups/Example Mod.xml",
                    members=["Missing Outfit"],
                )],
            )
        ]
        self.window._scan_data = {}
        self.window._reload_groups = lambda: None  # keep this focused on tree rendering
        self.window._member_group_sources = {"Missing Outfit": "Example Mod.xml"}
        self.window._body_group_filter = ""
        self.window._rebuild_tree()
        self.assertEqual(self.window.group_tree.topLevelItemCount(), 1)
        self.assertEqual(self.window.group_tree.topLevelItem(0).text(0),
                         "Unmatched · Example Mod.xml  (1)")

    def test_overlapping_group_membership_is_deduped_for_patch_input(self):
        self.window._selected_outfits = {
            ("__outfits__", "Shared")
        }
        groups = self.window._checked_groups()
        patch_members = list(dict.fromkeys(
            member for group in groups for member in group.members
        ))
        self.assertEqual(patch_members, ["Shared"])

    def test_current_naming_uses_3ba_and_keeps_legacy_input_alias(self):
        self.assertEqual(detect_gender_label("MPP 3BA Body"), "3BA")
        self.assertEqual(detect_gender_label("CBBE 3BBB Body"), "3BA")
        bhunp = classify_metadata(["BHUNP Armor"])
        uunp = classify_metadata(["UUNP Armor"])
        self.assertEqual(bhunp[1], "BHUNP")
        self.assertEqual(bhunp[0], "Unknown")
        self.assertEqual(uunp[1], "UUNP")
        self.assertEqual(uunp[0], "Unknown")
        self.assertEqual(classify_metadata_details(["BHUNP Armor"]).gender, "Female")
        self.assertEqual(classify_metadata_details(["UUNP Armor"]).gender, "Female")
        self.assertEqual(OutfitInfo(name="BHUNP armor", body_family="BHUNP",
                                    gender="Female").eff_sex, "Female")
        self.assertEqual(OutfitInfo(name="manual female", gender="Unknown",
                                    gender_override="3BA").eff_sex, "Female")
        self.assertEqual(OutfitInfo(name="manual male", gender="Unknown",
                                    gender_override="HIMBO").eff_sex, "Male")
        self.assertTrue(self.window._paths_overlap("C:/Data/BodySlide", "C:/Data/BodySlide/Out"))
        self.assertFalse(self.window._paths_overlap("C:/Data/BodySlide", "C:/Data/MPP Output"))
        self.assertFalse(self.window._paths_overlap("C:/Games/Skyrim/Data", "D:/MPPatcher Output"))
        self.assertTrue(self.window._paths_overlap("D:/MPPatcher Output", "D:/MPPatcher Output/SliderSets"))

    def test_missing_project_keeps_group_context_but_needs_attention(self):
        info = OutfitInfo(name="Dragon Priest Bronze", gender="3BA",
                          body_family="3BA", confidence="High",
                          project_found=False)
        self.assertEqual(info.status_label, "Missing project")
        self.assertTrue(info.needs_attention)

    def test_selection_stage_hides_technical_columns_and_reports_patch_state(self):
        self.assertEqual(self.window.group_tree.headerItem().text(3), "Patch Status")
        self.assertTrue(self.window.group_tree.isColumnHidden(1))
        self.assertTrue(self.window.group_tree.isColumnHidden(2))
        self.assertTrue(self.window.group_tree.isColumnHidden(4))
        self.assertEqual(OutfitInfo(name="Partial", partially_patched=True).patch_status,
                         "Partially patched")

    def test_selected_outfits_are_rendered_for_final_technical_review(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._refresh_execute_review()
        self.assertEqual(self.window.execute_review_tree.topLevelItemCount(), 1)
        row = self.window.execute_review_tree.topLevelItem(0)
        self.assertEqual(row.text(0), "Shared")
        self.assertEqual(row.text(1), "3BA (Female)")
        self.assertEqual(row.text(3), "Leave as Original")

    def test_execute_review_revealing_checkbox_toggles_model_and_row(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._refresh_execute_review()
        row = self.window.execute_review_tree.topLevelItem(0)

        self.assertFalse(self.window._scan_data["Shared"].revealing)
        self.assertEqual(row.checkState(4), Qt.Unchecked)
        self.window._toggle_execute_revealing(row)
        self.assertTrue(self.window._scan_data["Shared"].revealing)
        self.assertEqual(row.checkState(4), Qt.Checked)
        self.window._toggle_execute_revealing(row)
        self.assertFalse(self.window._scan_data["Shared"].revealing)
        self.assertEqual(row.checkState(4), Qt.Unchecked)

    def test_execute_review_revealing_accepts_real_mouse_and_keyboard_input(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._refresh_execute_review()
        tree = self.window.execute_review_tree
        row = tree.topLevelItem(0)
        row_rect = tree.visualItemRect(row)
        checkbox_center = QPoint(
            tree.columnViewportPosition(4) + tree.columnWidth(4) // 2,
            row_rect.center().y(),
        )

        QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier,
                         checkbox_center)
        self.app.processEvents()
        self.assertTrue(self.window._scan_data["Shared"].revealing)
        self.assertEqual(row.checkState(4), Qt.Checked)

        tree.setCurrentItem(row, 4)
        QTest.keyClick(tree, Qt.Key_Space)
        self.app.processEvents()
        self.assertFalse(self.window._scan_data["Shared"].revealing)
        self.assertEqual(row.checkState(4), Qt.Unchecked)

    def test_execute_review_revealing_creates_non_patchable_provisional_state(self):
        self.window._scan_data = {}
        self.window._selected_outfits = {("__outfits__", "Missing OSP")}
        self.window._refresh_execute_review()
        tree = self.window.execute_review_tree
        row = tree.topLevelItem(0)
        self.assertIsNone(self.window._scan_data.get("Missing OSP"))

        delegate = tree.itemDelegateForColumn(4)
        self.assertTrue(hasattr(delegate, "toggle_index"))
        self.assertTrue(delegate.toggle_index(tree, tree.indexFromItem(row, 4)))
        info = self.window._scan_data["Missing OSP"]
        self.assertTrue(info.revealing)
        self.assertFalse(info.project_found)
        self.assertEqual(row.checkState(4), Qt.Checked)

    def test_revealing_file_contains_the_names_sent_to_cli(self):
        self.window._scan_data["Shared"].revealing = True
        path = self.window._write_revealing_file(["Shared", "One"])
        self.assertTrue(path)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "Shared")
        self.assertIn(path, self.window._temp_files)

    def test_smart_refit_defaults_off_and_is_per_outfit(self):
        self.assertFalse(OutfitInfo(name="x").smart_refit)
        # Smart refit is no longer a run-wide switch; the column is the only
        # source of truth, so a stale global checkbox must not exist.
        self.assertFalse(hasattr(self.window, "smart_refit_check"))

    def test_execute_review_smart_refit_checkbox_toggles_model_and_row(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._refresh_execute_review()
        row = self.window.execute_review_tree.topLevelItem(0)

        self.assertFalse(self.window._scan_data["Shared"].smart_refit)
        self.assertEqual(row.checkState(COL_SMART), Qt.Unchecked)
        self.window._toggle_execute_smart_refit(row)
        self.assertTrue(self.window._scan_data["Shared"].smart_refit)
        self.assertEqual(row.checkState(COL_SMART), Qt.Checked)
        self.window._toggle_execute_smart_refit(row)
        self.assertFalse(self.window._scan_data["Shared"].smart_refit)
        self.assertEqual(row.checkState(COL_SMART), Qt.Unchecked)

    def test_smart_refit_is_independent_of_revealing(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._refresh_execute_review()
        row = self.window.execute_review_tree.topLevelItem(0)
        info = self.window._scan_data["Shared"]

        self.window._toggle_execute_smart_refit(row)
        self.assertTrue(info.smart_refit)
        self.assertFalse(info.revealing)
        self.window._toggle_execute_revealing(row)
        self.assertTrue(info.smart_refit)
        self.assertTrue(info.revealing)

    def test_execute_review_smart_refit_accepts_real_mouse_and_keyboard_input(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._refresh_execute_review()
        tree = self.window.execute_review_tree
        row = tree.topLevelItem(0)
        # Six columns do not fit the unshown test window, though they do fit
        # the real 1200px minimum. Bring the column into view the way the user
        # would, so the click exercises the delegate and not the geometry.
        tree.scrollTo(tree.indexFromItem(row, COL_SMART))
        self.app.processEvents()
        row_rect = tree.visualItemRect(row)
        checkbox_center = QPoint(
            tree.columnViewportPosition(COL_SMART) + tree.columnWidth(COL_SMART) // 2,
            row_rect.center().y(),
        )
        self.assertGreater(tree.viewport().width(), checkbox_center.x())

        QTest.mouseClick(tree.viewport(), Qt.LeftButton, Qt.NoModifier,
                         checkbox_center)
        self.app.processEvents()
        self.assertTrue(self.window._scan_data["Shared"].smart_refit)
        self.assertEqual(row.checkState(COL_SMART), Qt.Checked)

        tree.setCurrentItem(row, COL_SMART)
        QTest.keyClick(tree, Qt.Key_Space)
        self.app.processEvents()
        self.assertFalse(self.window._scan_data["Shared"].smart_refit)
        self.assertEqual(row.checkState(COL_SMART), Qt.Unchecked)

    def test_smart_refit_file_contains_the_names_sent_to_cli(self):
        self.window._scan_data["Shared"].smart_refit = True
        path = self.window._write_smart_refit_file(["Shared", "One"])
        self.assertTrue(path)
        self.assertEqual(Path(path).read_text(encoding="utf-8"), "Shared")
        self.assertIn(path, self.window._temp_files)

    def test_smart_refit_file_is_empty_when_nothing_is_marked(self):
        # No marked outfit means no --smart-refit-sets argument at all, rather
        # than an empty file the CLI would still have to open.
        self.assertEqual(self.window._write_smart_refit_file(["Shared", "One"]), "")

    def test_smart_refit_group_operation_ignores_active_filter(self):
        self.window._rebuild_tree("shared")
        group = self.window.group_tree.topLevelItem(0)
        self.window._set_smart_refit(group, True)
        self.assertTrue(self.window._scan_data["Shared"].smart_refit)
        self.assertTrue(self.window._scan_data["One"].smart_refit)
        self.assertFalse(self.window._scan_data["Two"].smart_refit)

    def _review_row(self, name="Shared"):
        self.window._selected_outfits = {("__outfits__", name)}
        self.window._refresh_execute_review()
        return self.window.execute_review_tree.topLevelItem(0)

    def test_body_part_without_an_embedded_reference_asks_for_attention(self):
        """A full-body outfit whose project carries no body is worth flagging.

        The patch then generates morphs for the garment only - the body does
        not follow - which looks like "the RM sliders do nothing".
        """
        info = self.window._scan_data["Shared"]
        info.mesh_override = "Body"
        info.has_reference = False
        row = self._review_row()
        self.assertTrue(row.data(COL_OUTPUT, Qt.UserRole + 12))
        self.assertIn("Force Reference", row.toolTip(COL_OUTPUT))

    def test_no_attention_when_the_project_already_carries_a_body(self):
        info = self.window._scan_data["Shared"]
        info.mesh_override = "Body"
        info.has_reference = True
        self.assertFalse(self._review_row().data(COL_OUTPUT, Qt.UserRole + 12))

    def test_no_attention_for_a_reference_less_non_body_part(self):
        info = self.window._scan_data["Shared"]
        info.mesh_override = "Hands"
        info.has_reference = False
        self.assertFalse(self._review_row().data(COL_OUTPUT, Qt.UserRole + 12))

    def test_attention_clears_once_force_reference_is_chosen(self):
        info = self.window._scan_data["Shared"]
        info.mesh_override = "Body"
        info.has_reference = False
        self.assertTrue(self._review_row().data(COL_OUTPUT, Qt.UserRole + 12))
        self.window._apply_review_override("Shared", "output", "force")
        self.assertFalse(self._review_row().data(COL_OUTPUT, Qt.UserRole + 12))

    def test_output_reference_policy_is_conditional_and_persistent(self):
        info = self.window._scan_data["Shared"]
        info.has_reference = True
        self.window._apply_review_override("Shared", "output", "remove")
        self.assertEqual(info.output_reference_policy, "remove")
        self.window._apply_review_override("Shared", "output", "force")
        self.assertEqual(info.output_reference_policy, "remove")
        info.has_reference = False
        self.window._apply_review_override("Shared", "output", "force")
        self.assertEqual(info.output_reference_policy, "force")

    def test_review_and_patch_uses_scrollable_content_and_dynamic_table_height(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._refresh_execute_review()
        single_height = self.window.execute_review_tree.maximumHeight()
        self.assertIs(self.window.execute_scroll.widget(),
                      self.window.execute_review_tree.parentWidget().parentWidget())
        self.assertTrue(self.window.progress_card.isHidden())

        names = [f"Batch {index:02d}" for index in range(20)]
        self.window._scan_data.update({
            name: OutfitInfo(name=name, gender="3BA", source_project="Batch.osp")
            for name in names
        })
        self.window._selected_outfits = {("__outfits__", name) for name in names}
        self.window._refresh_execute_review()
        self.assertGreater(self.window.execute_review_tree.maximumHeight(), single_height)
        self.assertLessEqual(self.window.execute_review_tree.maximumHeight(), 440)

    def test_review_marks_unresolved_mesh_with_an_explicit_action(self):
        self.window._scan_data["Shared"].mesh_type = "Unknown"
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._refresh_execute_review()
        row = self.window.execute_review_tree.topLevelItem(0)
        self.assertEqual(row.text(2), "Select part...")
        self.assertTrue(row.data(2, Qt.UserRole + 12))

    def test_review_controls_keep_internal_reference_keys_and_manual_overrides(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._apply_review_override("Shared", "reference", "HIMBO")
        self.window._apply_review_override("Shared", "mesh", "Hands")
        info = self.window._scan_data["Shared"]
        self.assertEqual(info.gender_override, "HIMBO")
        self.assertEqual(info.mesh_override, "Hands")
        self.window._refresh_execute_review()
        row = self.window.execute_review_tree.topLevelItem(0)
        self.assertEqual(row.text(1), "HIMBO (Male)")
        self.assertEqual(row.text(2), "Hands")
        self.assertTrue(row.data(1, Qt.UserRole + 11))
        self.assertTrue(row.data(2, Qt.UserRole + 11))
        self.assertEqual(self.window.execute_review_tree.headerItem().text(1),
                         "Body Reference")
        # Every configurable column carries its own help button; assert the
        # columns rather than a bare count, so adding one says which is missing.
        self.assertEqual(
            [column for column, _title, _button
             in self.window._review_header_help_buttons],
            [1, 2, 3, COL_REVEAL, COL_SMART])

    def test_headpart_sex_detection_does_not_confuse_female_with_male(self):
        self.assertEqual(detect_headpart_sex("meshes/heads/FemaleHead.tri"), "female")
        self.assertEqual(detect_headpart_sex("meshes/heads/MaleHead.tri"), "male")
        self.assertEqual(detect_headpart_sex("meshes/heads/brows.tri"), "unknown")

    def test_revealing_group_operation_ignores_active_filter(self):
        self.window._rebuild_tree("shared")
        group = self.window.group_tree.topLevelItem(0)
        self.window._set_revealing(group, True)
        self.assertTrue(self.window._scan_data["Shared"].revealing)
        self.assertTrue(self.window._scan_data["One"].revealing)
        self.assertFalse(self.window._scan_data["Two"].revealing)

    def test_progress_ignores_the_prefixed_duplicate_done_line(self):
        """The CLI reports each set twice, and one line carries the MPP_ prefix.

            [DONE ] MPP_One → 3 new slider entries → One_MPP.osd
            [DONE ] One

        The prefixed line used to match no selected set, so it was filed under
        its own key and every outfit counted twice - the bar reached 100% at
        the halfway point and kept running.
        """
        self.window._patch_members = {"One", "Two"}
        self.window._patch_total = 2
        self.window._patch_done = 0
        self.window._terminal_status = {}

        self.window._on_log_line("[DONE ] MPP_One → 3 new slider entries → One_MPP.osd")
        self.window._on_log_line("[DONE ] One")
        self.assertEqual(self.window._patch_done, 1)
        self.assertEqual(self.window.lbl_progress_pct.text(), "50%")

        self.window._on_log_line("[DONE ] MPP_Two → 3 new slider entries → Two_MPP.osd")
        self.window._on_log_line("[DONE ] Two")
        self.assertEqual(self.window._patch_done, 2)
        self.assertEqual(self.window.lbl_progress_pct.text(), "100%")
        self.assertEqual(set(self.window._terminal_status), {"One", "Two"})

    def test_progress_counts_terminal_events_once(self):
        self.window._patch_members = {"One", "Two"}
        self.window._patch_total = 2
        self.window._patch_done = 0
        self.window._terminal_status = {}
        self.window._warning_count = 0
        self.window._on_log_line("[DONE ] One")
        self.window._on_log_line("[DONE ] One")
        self.window._on_log_line("[SKIP ] Two - gender unknown")
        self.assertEqual(self.window._terminal_status, {"One": "DONE", "Two": "SKIP"})
        self.assertEqual(self.window._patch_done, 2)

    def test_patched_is_terminal_but_patch_is_only_in_progress(self):
        self.window._patch_members = {"One", "Two"}
        self.window._patch_total = 2
        self.window._patch_done = 0
        self.window._terminal_status = {}
        self.window._on_log_line("[PATCH ] One")
        self.assertEqual(self.window._patch_done, 0)
        self.window._on_log_line("[PATCHED] One")
        self.assertEqual(self.window._terminal_status, {"One": "PATCHED"})
        self.assertEqual(self.window._patch_done, 1)

    def test_select_all_and_none_only_affect_visible_outfits(self):
        self.window._selected_outfits = {("__outfits__", "Two")}
        self.window.search_edit.setText("shared")
        self.window._rebuild_tree("shared")
        self.window._select_all_groups()
        self.assertEqual({name for _, name in self.window._selected_outfits},
                         {"Shared", "Two"})
        self.window._select_no_groups()
        self.assertEqual(self.window._selected_outfits, {("__outfits__", "Two")})

    def test_selection_context_clears_only_for_a_new_source_or_target(self):
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._selection_context = ("outfits", "folder", "c:\\mod-a")
        self.window._set_selection_context("outfits", "folder", "C:/mod-a")
        self.assertEqual(self.window._selected_outfits, {("__outfits__", "Shared")})
        self.window._set_selection_context("headparts", "folder", "C:/mod-a")
        self.assertEqual(self.window._selected_outfits, set())

    def test_source_context_never_carries_metadata_overrides_to_another_source(self):
        self.window._selection_context = ("outfits", "folder", "c:\\mod-a")
        self.window._selected_outfits = {("__outfits__", "Shared")}
        self.window._scan_data["Shared"].gender_override = "HIMBO"
        self.window._all_groups = [SliderGroup(name="Old", members=["Shared"])]

        self.window._set_selection_context("outfits", "folder", "D:/mod-b")

        self.assertEqual(self.window._selected_outfits, set())
        self.assertEqual(self.window._scan_data, {})
        self.assertEqual(self.window._all_groups, [])


if __name__ == "__main__":
    unittest.main()
