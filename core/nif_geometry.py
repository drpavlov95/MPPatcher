r"""Headpart geometry, read out of the mod's NIFs through `nifdump`.

Geometry for a headpart morph set must come from the NIF, never from a `.tri`
the mod ships beside it. Hair mods routinely ship a stub chargen TRI — Vanilla
Hair Remake has a 212-byte, four-vertex `hair01.tri` next to a 4014-vertex
`hair01.nif` — and MPF matches a morph set against the *live* mesh, so a set
built from the stub matches nothing while reporting success.

`nifdump` is a small nifly-backed binary from the same CMake project that builds
`mppatcher-cli.exe`. It is used rather than a Python reader because nifly handles
`NiTriShape` (Skyrim LE format, still shipped by plenty of old hair mods) as well
as every `BSTriShape` variant, and because it returns triangles, which the
surface wrap needs for its optional smoothing pass.

It is resolved beside the GUI exactly as :attr:`core.config.Config.mppatcher_cli_exe`
resolves the native CLI, and its absence is an error with the searched paths in
it — never a quiet fallback to the stub TRIs, which is the one failure this whole
module exists to prevent.
"""
from __future__ import annotations

import csv
import os
import re
import subprocess
from dataclasses import dataclass, field

import numpy as np

from core.vfs import app_directory, vfs_find
from fmp.nifdump import NifDumpError, read_dump

NIFDUMP_NAMES = ("nifdump.exe", "nifdump")
ENV_OVERRIDE = "MPP_NIFDUMP"
DUMP_SUBDIR = "dumps"
INDEX_NAME = "nifdump-index.tsv"

# "nifdump: 50 NIFs, 50 shapes, 3 with no usable geometry, 1 FAILED"
_TALLY = re.compile(
    r"nifdump:\s*(\d+)\s+NIFs,\s*(\d+)\s+shapes"
    r"(?:,\s*(\d+)\s+with no usable geometry)?"
    r"(?:,\s*(\d+)\s+FAILED)?")


class NifdumpMissing(RuntimeError):
    """`nifdump` could not be located, so no geometry can be read."""


@dataclass
class DumpedGeometry:
    """One shape out of one NIF, in the shape's own local space."""

    relpath: str                   # as nifdump wrote it, relative to the source
    shape: str
    verts: np.ndarray              # (vc, 3) float32
    tris: np.ndarray               # (tc, 3) uint32


@dataclass
class DumpRun:
    """Where a dump landed, and what nifdump said about it."""

    dump_root: str
    index_path: str
    nifs: int = 0
    shapes: int = 0
    # NIFs that carry no usable geometry. Many headpart NIFs are just a skin
    # partition wrapper, which is a valid answer rather than an error, so these
    # are reported and counted instead of failing the run. nifdump prints only a
    # tally, never the names, so `empty` is filled by read_geometries from the
    # dumps that have no index row and `empty_count` keeps nifdump's own number
    # for cross-checking.
    empty: list[str] = field(default_factory=list)
    empty_count: int = 0
    failures: list[str] = field(default_factory=list)


def find_nifdump(app_dir: str = "") -> str:
    """Return a usable `nifdump` path, or '' when none can be reached.

    Defaults to this program's own directory, then its parent — the same order
    :attr:`core.config.Config.mppatcher_cli_exe` uses for the native CLI, which
    covers both deployment layouts (the exe beside BodySlide, and the exe in its
    own subfolder).

    The operator override is returned as given and is not probed: an exported
    path is a deliberate choice of binary, and silently ignoring a typo in it
    would send the run to a different `nifdump` than the one asked for.
    """
    override = os.environ.get(ENV_OVERRIDE, "").strip()
    if override:
        return override
    app_dir = app_dir or app_directory()
    if not app_dir:
        return ""
    # Enumeration only. MO2's usvfs hooks FindFirstFileW but not the stat
    # family, so os.path.isfile answers "no" for a file that is really there.
    return (vfs_find(app_dir, *NIFDUMP_NAMES)
            or vfs_find(os.path.dirname(app_dir), *NIFDUMP_NAMES))


def _default_runner(argv: list[str]) -> tuple[int, str]:
    kwargs = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    done = subprocess.run(argv, capture_output=True, text=True,
                          errors="replace", check=False, **kwargs)
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def dump_tree(exe: str, source_root: str, work_dir: str, *,
              runner=None) -> DumpRun:
    """Walk *source_root* for NIFs and dump every shape under *work_dir*."""
    if not exe:
        raise NifdumpMissing(
            "nifdump was not found next to MPPatcher.exe or in its parent "
            f"folder, and {ENV_OVERRIDE} is not set. Headpart geometry can "
            "only be read from the mod's NIFs, so there is nothing to patch "
            "without it.")

    dump_root = os.path.join(work_dir, DUMP_SUBDIR)
    index_path = os.path.join(work_dir, INDEX_NAME)
    os.makedirs(dump_root, exist_ok=True)

    argv = [exe, "--dir", source_root, "--out", dump_root,
            "--index", index_path, "--quiet"]
    try:
        code, output = (runner or _default_runner)(argv)
    except OSError as exc:
        # find_nifdump returns an operator override unprobed, deliberately, so
        # a typo in MPP_NIFDUMP or --nifdump arrives here. Raised as-is it was a
        # localised Windows FileNotFoundError that never named nifdump or the
        # path tried.
        raise NifdumpMissing(
            f"nifdump could not be started from {exe!r}: {exc}. Check "
            f"--nifdump and {ENV_OVERRIDE}.") from exc

    run = DumpRun(dump_root=dump_root, index_path=index_path)
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("[FAIL] "):
            run.failures.append(line[len("[FAIL] "):])
    match = _TALLY.search(output)
    if match:
        run.nifs = int(match.group(1))
        run.shapes = int(match.group(2))
        run.empty_count = int(match.group(3) or 0)
    elif code != 0:
        # No tally at all means nifdump never got far enough to print one —
        # a missing DLL, a bad path. Without this the run would look like a
        # mod with no NIFs in it.
        run.failures.append(output.strip() or f"nifdump exited {code}")
    return run


def _index_rows(index_path: str) -> list[dict[str, str]]:
    try:
        with open(index_path, encoding="utf-8", errors="replace",
                  newline="") as handle:
            return [row for row in csv.DictReader(handle, delimiter="\t")
                    if row.get("relpath")]
    except OSError:
        return []


def _dumped_relpaths(dump_root: str) -> list[str]:
    out = []
    for current, _dirs, files in os.walk(dump_root):
        for name in files:
            if not name.lower().endswith(".bin"):
                continue
            full = os.path.join(current, name)
            out.append(os.path.relpath(full, dump_root)[: -len(".bin")])
    return out


def read_geometries(run: DumpRun) -> list[DumpedGeometry]:
    """Every dumped shape named by the index, in index order.

    The index carries one row per shape, so the NIFs that dumped nothing are the
    ones with a `.bin` and no row; they are collected into ``run.empty`` on the
    way past. A row whose dump cannot be read is a failure rather than an
    exception: one unreadable mesh must not cost the user the other two hundred.
    """
    rows = _index_rows(run.index_path)
    seen: dict[str, None] = {}
    for row in rows:
        seen.setdefault(row["relpath"], None)

    out: list[DumpedGeometry] = []
    for relpath in seen:
        binpath = os.path.join(run.dump_root,
                               relpath.replace("\\", os.sep) + ".bin")
        try:
            with open(binpath, "rb") as handle:
                shapes = read_dump(handle.read(), source=relpath)
        except (OSError, NifDumpError) as exc:
            run.failures.append(f"{relpath}: {exc}")
            continue
        for shape in shapes:
            if not len(shape.verts):
                continue
            out.append(DumpedGeometry(relpath=relpath, shape=shape.name,
                                      verts=shape.verts, tris=shape.tris))

    indexed = {relpath.replace("\\", "/").lower() for relpath in seen}
    run.empty = sorted(
        relpath for relpath in _dumped_relpaths(run.dump_root)
        if relpath.replace("\\", "/").lower() not in indexed)
    return out
