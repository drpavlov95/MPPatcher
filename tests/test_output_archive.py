import unittest
from pathlib import Path
from zipfile import ZipFile

from core.output_archive import create_zip_archive


class OutputArchiveTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / "_test_output_archive_workspace"
        self.root.mkdir(exist_ok=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_archive_contains_output_tree_without_a_wrapper_folder(self):
        root = self.root / "output"
        (root / "CalienteTools" / "BodySlide").mkdir(parents=True)
        (root / "CalienteTools" / "BodySlide" / "Example.osp").write_text(
            "project", encoding="utf-8")
        (root / "empty").mkdir()

        result = create_zip_archive(root, self.root / "patched")

        self.assertEqual(result.path.name, "patched.zip")
        self.assertEqual(result.file_count, 1)
        with ZipFile(result.path) as archive:
            self.assertEqual(
                archive.namelist(),
                ["CalienteTools/BodySlide/Example.osp", "empty/"],
            )

    def test_existing_archive_is_replaced_with_complete_new_archive(self):
        root = self.root / "output"
        root.mkdir()
        (root / "good.txt").write_text("good", encoding="utf-8")
        target = self.root / "patched.zip"
        target.write_text("not a zip yet", encoding="utf-8")

        create_zip_archive(root, target)

        with ZipFile(target) as archive:
            self.assertEqual(archive.read("good.txt"), b"good")
