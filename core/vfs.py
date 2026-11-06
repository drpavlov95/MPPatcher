r"""Path primitives that survive MO2's virtual file system.

MO2's usvfs hooks directory **enumeration** — ``FindFirstFileW`` /
``FindNextFileW``, which is what :func:`os.listdir` and :func:`os.scandir` use —
but **not** the stat family, ``GetFileAttributesW``, behind
:func:`os.path.isdir`, :func:`os.path.exists` and :func:`os.path.isfile`. A file
that is really there, served from a mod under a virtual Data path, therefore
answers "no" to every stat question and "yes" to enumeration. Confirmed
empirically through the diagnostics in this repo.

So anything that looks for a file in a possibly-virtual tree must enumerate.
These three helpers are that, and they live here rather than in
:mod:`core.config` so the command-line paths can use them without importing Qt
for a ``QSettings`` object they never touch.
"""
from __future__ import annotations

import os
import sys


def app_directory() -> str:
    """Where this program lives, which is where its native binaries sit.

    Never ``dirname(__file__)``. Inside a PyInstaller bundle that is the
    temporary extraction directory, so a search rooted there looks through
    unpacked Python and finds none of the binaries deployed beside the exe —
    reporting them missing while they sit right next to ``MPPatcher.exe``.
    Frozen builds must ask ``sys.executable``; this is the same base BodySlide
    itself uses, and under MO2 it is the virtual path, which is what we want.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    # Development: the repository root, two levels up from core/.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def vfs_isdir(path: str) -> bool:
    """True if `path` is an enumerable directory (works through usvfs)."""
    if not path:
        return False
    try:
        os.listdir(path)
        return True
    except OSError:
        return False


def vfs_listdir(path: str) -> list[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []


def vfs_find(folder: str, *names: str) -> str:
    """Full path of the first `name` present in `folder`, case-insensitively.

    Located by enumeration. Returns '' when none of the names is there.
    """
    entries = vfs_listdir(folder)
    if not entries:
        return ""
    low = {entry.lower(): entry for entry in entries}
    for name in names:
        actual = low.get(name.lower())
        if actual:
            return os.path.join(folder, actual)
    return ""
