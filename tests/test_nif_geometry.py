"""Coverage for the NIF geometry hand-off to `nifdump`.

Headpart geometry has to come from the mod's NIFs: hair mods routinely ship a
stub chargen TRI beside a real mesh — Vanilla Hair Remake has a four-vertex TRI
next to a 4014-vertex NIF — and a morph set built from the stub matches no live
mesh while reporting nothing. So every failure mode of this hand-off is a silent
one, and each is pinned here: a missing binary, a NIF that cannot be loaded, and
a NIF that dumps no shapes at all.
"""
import os
import struct
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import vfs
from core.nif_geometry import (
    DumpRun, NifdumpMissing, dump_tree, find_nifdump, read_geometries,
)

INDEX_HEADER = "relpath\tshape\tverts\ttris\n"


def _dump_bytes(shapes) -> bytes:
    """A NIFD0002 payload, per the format at the top of `fmp/nifdump.py`."""
    out = bytearray(b"NIFD0002")
    out += struct.pack("<I", len(shapes))
    for name, verts, tris in shapes:
        raw = name.encode("utf-8")
        out += struct.pack("<H", len(raw)) + raw
        out += struct.pack("<II", len(verts), len(tris))
        out += np.asarray(verts, dtype="<f4").tobytes()
        out += np.asarray(tris, dtype="<u4").tobytes()
    return bytes(out)


def _write_dump(dump_root: str, relpath: str, shapes) -> None:
    dest = os.path.join(dump_root, relpath.replace("\\", os.sep) + ".bin")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as handle:
        handle.write(_dump_bytes(shapes))


def _touch(path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"MZ")
    return path


class FindNifdumpTests(unittest.TestCase):
    def test_prefers_the_binary_beside_the_exe_over_the_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = os.path.join(folder, "MPPatcher")
            beside = _touch(os.path.join(app_dir, "nifdump.exe"))
            _touch(os.path.join(folder, "nifdump.exe"))
            self.assertEqual(find_nifdump(app_dir), beside)

    def test_falls_back_to_the_parent_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = os.path.join(folder, "MPPatcher")
            os.makedirs(app_dir)
            parent = _touch(os.path.join(folder, "nifdump.exe"))
            self.assertEqual(find_nifdump(app_dir), parent)

    def test_environment_override_wins_over_both(self):
        with tempfile.TemporaryDirectory() as folder:
            app_dir = os.path.join(folder, "MPPatcher")
            _touch(os.path.join(app_dir, "nifdump.exe"))
            override = os.path.join(folder, "elsewhere", "my-nifdump.exe")
            with mock.patch.dict(os.environ, {"MPP_NIFDUMP": override}):
                self.assertEqual(find_nifdump(app_dir), override)

    def test_returns_empty_string_when_absent(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MPP_NIFDUMP", None)
                self.assertEqual(find_nifdump(folder), "")

    def test_a_frozen_build_searches_beside_its_own_executable(self):
        # The bug this pins: inside a PyInstaller bundle __file__ points at the
        # temporary extraction directory, not at the exe. A caller that passed
        # dirname(__file__) searched the wrong tree and reported nifdump missing
        # while it sat right beside MPPatcher.exe.
        with tempfile.TemporaryDirectory() as folder:
            app_dir = os.path.join(folder, "tools", "MPPatcher")
            beside = _touch(os.path.join(app_dir, "nifdump.exe"))
            # Stand in for the PyInstaller extraction directory that
            # dirname(__file__) would have resolved to. nifdump is not in it.
            extraction = os.path.join(folder, "_MEI123456", "core")
            os.makedirs(extraction)
            _touch(os.path.join(folder, "_MEI123456", "decoy.txt"))
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MPP_NIFDUMP", None)
                with mock.patch.object(sys, "frozen", True, create=True), \
                     mock.patch.object(sys, "executable",
                                       os.path.join(app_dir, "MPPatcher.exe")), \
                     mock.patch.object(vfs, "__file__",
                                       os.path.join(extraction, "vfs.py")):
                    self.assertEqual(find_nifdump(), beside)

    def test_an_unfrozen_run_uses_the_repository_root_not_the_package_dir(self):
        self.assertEqual(vfs.app_directory(), ROOT)

    def test_an_unfrozen_run_searches_the_project_root(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MPP_NIFDUMP", None)
            with mock.patch.object(sys, "frozen", False, create=True):
                # The repository root holds no nifdump.exe, so this is '' rather
                # than an exception — and it must not look inside core/.
                self.assertEqual(find_nifdump(), "")


class DumpTreeTests(unittest.TestCase):
    def test_raises_nifdump_missing_for_an_empty_exe_path(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(NifdumpMissing):
                dump_tree("", folder, os.path.join(folder, "work"))

    def test_passes_the_documented_argument_list_to_the_binary(self):
        seen = {}

        def runner(argv):
            seen["argv"] = argv
            return 0, "nifdump: 0 NIFs, 0 shapes\n"

        with tempfile.TemporaryDirectory() as folder:
            work = os.path.join(folder, "work")
            run = dump_tree("nifdump.exe", folder, work, runner=runner)
            self.assertEqual(seen["argv"], [
                "nifdump.exe", "--dir", folder, "--out", run.dump_root,
                "--index", run.index_path, "--quiet",
            ])

    def test_parses_the_tally_into_counts(self):
        def runner(_argv):
            return 0, "nifdump: 50 NIFs, 50 shapes\n"

        with tempfile.TemporaryDirectory() as folder:
            run = dump_tree("nifdump.exe", folder, os.path.join(folder, "w"),
                            runner=runner)
            self.assertEqual((run.nifs, run.shapes), (50, 50))

    def test_reports_a_nonzero_exit_as_failure(self):
        def runner(_argv):
            return 2, ("[FAIL] x.nif: cannot load\n"
                       "nifdump: 1 NIFs, 0 shapes, 1 FAILED\n")

        with tempfile.TemporaryDirectory() as folder:
            run = dump_tree("nifdump.exe", folder, os.path.join(folder, "w"),
                            runner=runner)
            self.assertEqual(run.failures, ["x.nif: cannot load"])
            self.assertEqual((run.nifs, run.shapes), (1, 0))

    def test_a_crash_with_no_tally_is_recorded_rather_than_swallowed(self):
        def runner(_argv):
            return 3221225781, "The code execution cannot proceed\n"

        with tempfile.TemporaryDirectory() as folder:
            run = dump_tree("nifdump.exe", folder, os.path.join(folder, "w"),
                            runner=runner)
            self.assertTrue(run.failures)
            self.assertIn("cannot proceed", run.failures[0])


class ReadGeometriesTests(unittest.TestCase):
    def _run_with(self, folder, rows, dumps):
        work = os.path.join(folder, "work")
        dump_root = os.path.join(work, "dumps")
        index_path = os.path.join(work, "nifdump-index.tsv")
        os.makedirs(dump_root, exist_ok=True)
        with open(index_path, "w", encoding="utf-8") as handle:
            handle.write(INDEX_HEADER)
            for row in rows:
                handle.write("\t".join(str(value) for value in row) + "\n")
        for relpath, shapes in dumps:
            _write_dump(dump_root, relpath, shapes)
        return DumpRun(dump_root=dump_root, index_path=index_path,
                       nifs=len(dumps), shapes=len(rows))

    def test_reads_vertices_and_triangles_for_each_indexed_shape(self):
        verts_a = [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0), (7.0, 8.0, 9.0)]
        verts_b = [(0.5, 0.5, 0.5), (1.5, 1.5, 1.5), (2.5, 2.5, 2.5)]
        with tempfile.TemporaryDirectory() as folder:
            run = self._run_with(
                folder,
                rows=[("meshes\\hair\\a.nif", "HairA", 3, 1),
                      ("meshes\\hair\\b.nif", "HairB", 3, 1)],
                dumps=[("meshes\\hair\\a.nif", [("HairA", verts_a, [(0, 1, 2)])]),
                       ("meshes\\hair\\b.nif", [("HairB", verts_b, [(2, 1, 0)])])])
            out = read_geometries(run)
            self.assertEqual([g.shape for g in out], ["HairA", "HairB"])
            np.testing.assert_allclose(out[0].verts, np.asarray(verts_a))
            np.testing.assert_array_equal(out[1].tris, np.asarray([(2, 1, 0)]))
            self.assertEqual(out[0].relpath, "meshes\\hair\\a.nif")

    def test_several_shapes_in_one_nif_each_become_their_own_geometry(self):
        verts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)]
        with tempfile.TemporaryDirectory() as folder:
            run = self._run_with(
                folder,
                rows=[("meshes\\h.nif", "Hair", 3, 1),
                      ("meshes\\h.nif", "Scalp", 3, 1)],
                dumps=[("meshes\\h.nif", [("Hair", verts, [(0, 1, 2)]),
                                          ("Scalp", verts, [(0, 1, 2)])])])
            self.assertEqual([g.shape for g in read_geometries(run)],
                             ["Hair", "Scalp"])

    def test_an_unreadable_dump_is_recorded_as_a_failure_not_raised(self):
        with tempfile.TemporaryDirectory() as folder:
            run = self._run_with(folder,
                                 rows=[("meshes\\broken.nif", "X", 3, 1)],
                                 dumps=[])
            self.assertEqual(read_geometries(run), [])
            self.assertEqual(len(run.failures), 1)
            self.assertIn("broken.nif", run.failures[0])

    def test_a_dump_with_zero_shapes_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as folder:
            run = self._run_with(folder, rows=[],
                                 dumps=[("meshes\\wrapper.nif", [])])
            self.assertEqual(read_geometries(run), [])
            self.assertEqual(run.empty, [os.path.join("meshes", "wrapper.nif")])
            self.assertEqual(run.failures, [])


if __name__ == "__main__":
    unittest.main()
