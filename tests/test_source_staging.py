import tempfile
import unittest
from pathlib import Path

from core.source_staging import (normalize_output_ownership,
                                 resolve_physical_bodyslide_project)


OSP = """<?xml version="1.0"?>
<SliderSetInfo version="1">
  <SliderSet name="{name}">
    <DataFolder>Example</DataFolder>
    <SourceFile>Example.nif</SourceFile>
  </SliderSet>
</SliderSetInfo>
"""


class PhysicalProjectResolutionTests(unittest.TestCase):
    def _write_project(self, mod: Path, text: str) -> Path:
        path = (mod / "CalienteTools" / "BodySlide" / "SliderSets"
                / "Example.osp")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_output_mod_is_never_reused_as_the_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mods = root / "mods"
            original = mods / "Original Outfit"
            output = mods / "MPP Output"
            text = OSP.format(name="Selected Outfit")
            original_project = self._write_project(original, text)
            self._write_project(output, text)

            virtual = root / "virtual" / "BodySlide"
            (virtual / "SliderSets").mkdir(parents=True)
            (virtual / "SliderSets" / "Example.osp").write_text(
                text, encoding="utf-8")
            (root / "tools" / "MPPatcher").mkdir(parents=True)

            resolved = resolve_physical_bodyslide_project(
                "Example.osp", {"Selected Outfit"},
                virtual_bodyslide_root=str(virtual),
                output_root=str(output),
                app_dir=str(root / "tools" / "MPPatcher"),
            )

            self.assertEqual(resolved.project_path, original_project)
            self.assertEqual(resolved.mod_root, original)

    def test_selected_set_must_exist_in_the_physical_project(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            mods = root / "mods"
            self._write_project(mods / "Unrelated", OSP.format(name="Other"))
            output = mods / "MPP Output"
            output.mkdir(parents=True)

            with self.assertRaisesRegex(LookupError, "physical provider not found"):
                resolve_physical_bodyslide_project(
                    "Example.osp", {"Selected Outfit"},
                    virtual_bodyslide_root=str(root / "virtual"),
                    output_root=str(output),
                    app_dir=str(root / "tools" / "MPPatcher"),
                )

    def test_output_ownership_normalizer_accepts_a_generated_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "output"
            child = root / "CalienteTools" / "BodySlide" / "test.txt"
            child.parent.mkdir(parents=True)
            child.write_text("test", encoding="utf-8")

            changed, failures = normalize_output_ownership(root)

            self.assertEqual(failures, [])
            if __import__("os").name == "nt":
                self.assertEqual(changed, 4)
            else:
                self.assertEqual(changed, 0)


if __name__ == "__main__":
    unittest.main()
