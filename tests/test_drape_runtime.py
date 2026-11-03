"""Coverage for the Drape runtime handoff to the native CLI.

The CLI falls back to the absolute paths of the machine it was built on, so a
release that resolves the bundled runtime incorrectly does not fail loudly: it
fails one outfit at a time with a message about a drive the user does not have.
"""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import drape_runtime
from core.drape_runtime import BRIDGE_EXE, RUNTIME_DIRNAME, drape_environment, find_runtime
import tempfile
import shutil


def _make_runtime(parent: str) -> str:
    """Create a minimal tree shaped like the shipped MPPatcher-Drape folder."""
    root = os.path.join(parent, RUNTIME_DIRNAME)
    os.makedirs(os.path.join(root, "pyrefit"), exist_ok=True)
    os.makedirs(os.path.join(root, "_internal", "numpy"), exist_ok=True)
    for relative in (("pyrefit", "bridge.py"), (BRIDGE_EXE,),
                     ("_internal", "numpy", "libopenblas.dll")):
        with open(os.path.join(root, *relative), "wb") as handle:
            handle.write(b"payload")
    return root


class DrapeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        # Staging must never touch the real profile during tests.
        self.stage = os.path.join(self.tmp, "localappdata")
        patcher = mock.patch.dict(os.environ, {"LOCALAPPDATA": self.stage},
                                  clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("MPP_DRAPE_ROOT", "MPP_DRAPE_PYTHON"):
            os.environ.pop(name, None)

    def test_found_next_to_the_exe(self):
        app_dir = os.path.join(self.tmp, "BodySlide")
        os.makedirs(app_dir)
        root = _make_runtime(app_dir)
        env = drape_environment(app_dir)
        self.assertEqual(env["MPP_DRAPE_ROOT"], root)
        self.assertEqual(env["MPP_DRAPE_PYTHON"], os.path.join(root, BRIDGE_EXE))

    def test_found_one_level_up(self):
        """onedir layout: the exe sits in a subfolder of the BodySlide root."""
        body = os.path.join(self.tmp, "BodySlide")
        app_dir = os.path.join(body, "MPPatcher")
        os.makedirs(app_dir)
        root = _make_runtime(body)
        self.assertEqual(find_runtime(app_dir), root)

    def test_missing_runtime_returns_nothing(self):
        app_dir = os.path.join(self.tmp, "BodySlide")
        os.makedirs(app_dir)
        self.assertEqual(find_runtime(app_dir), "")
        self.assertEqual(drape_environment(app_dir), {})

    def test_incomplete_runtime_is_rejected(self):
        """A folder without the bridge exe is not a runtime."""
        app_dir = os.path.join(self.tmp, "BodySlide")
        os.makedirs(app_dir)
        root = _make_runtime(app_dir)
        os.remove(os.path.join(root, BRIDGE_EXE))
        self.assertEqual(find_runtime(app_dir), "")

    def test_operator_environment_wins(self):
        """An exported checkout keeps priority over the bundled copy."""
        app_dir = os.path.join(self.tmp, "BodySlide")
        os.makedirs(app_dir)
        _make_runtime(app_dir)
        with mock.patch.dict(os.environ, {"MPP_DRAPE_ROOT": r"C:\dev\Drape",
                                          "MPP_DRAPE_PYTHON": r"C:\Python311\python.exe"}):
            self.assertEqual(drape_environment(app_dir), {})

    def test_stat_blind_path_is_staged_physically(self):
        """The VFS case: readable by enumeration, invisible to the stat family.

        MO2 serves a virtual path through directory enumeration only, and the
        CLI tests the runtime with std::filesystem::exists.  Such a path has to
        be copied somewhere the CLI can actually see.
        """
        app_dir = os.path.join(self.tmp, "BodySlide")
        os.makedirs(app_dir)
        root = _make_runtime(app_dir)
        real_is_usable = drape_runtime._is_usable

        def blind_to_the_virtual_copy(path: str) -> bool:
            if os.path.normcase(path) == os.path.normcase(root):
                return False
            return real_is_usable(path)

        with mock.patch.object(drape_runtime, "_is_usable",
                               side_effect=blind_to_the_virtual_copy):
            staged = find_runtime(app_dir)
            self.assertTrue(staged)
            self.assertNotEqual(os.path.normcase(staged), os.path.normcase(root))
            self.assertTrue(staged.startswith(self.stage))
            # The whole tree travels, not just the two files that are checked.
            self.assertTrue(os.path.isfile(
                os.path.join(staged, "_internal", "numpy", "libopenblas.dll")))

            marker = os.path.join(staged, drape_runtime._STAGE_MARKER)
            stamped = os.path.getmtime(marker)
            again = find_runtime(app_dir)

        self.assertEqual(again, staged)
        self.assertEqual(os.path.getmtime(marker), stamped,
                         "a staged runtime must be reused, not copied again")


if __name__ == "__main__":
    unittest.main()
