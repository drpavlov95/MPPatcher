r"""Hand the native CLI the Drape bridge that ships beside the exe.

mppatcher-cli does not solve the Drape refit itself: it writes ``.drcap``
captures to a work directory and spawns ``<python> -m pyrefit.bridge <dir>``.
Both halves of that command come from the environment -- ``MPP_DRAPE_ROOT`` for
the checkout and ``MPP_DRAPE_PYTHON`` for the interpreter -- and when they are
missing the binary falls back to the absolute paths of the machine it was
compiled on.  On a user's install those paths do not exist, so Smart refit
fails the whole set with a message naming a drive they have never seen.

The release therefore carries a frozen bridge in ``MPPatcher-Drape`` next to
the exe, and this module points the CLI at it.

Two details drive the shape of the code:

* The CLI tests the checkout with ``std::filesystem::exists``, i.e. the stat
  family, which MO2's usvfs does not hook (see :mod:`core.config`).  So the
  runtime is verified here with ``os.path.isfile`` -- the same syscall family --
  and a path that only answers to enumeration is treated as unusable.
* A path that fails that test is still reachable for *reading*, so the runtime
  is copied once to a physical folder under LOCALAPPDATA and the CLI is sent
  there instead.
"""
import os
import shutil

from core.config import vfs_isdir, vfs_listdir

RUNTIME_DIRNAME = "MPPatcher-Drape"
BRIDGE_EXE = "drape-bridge.exe"
# The CLI refuses to start unless this file exists under MPP_DRAPE_ROOT.
BRIDGE_MARKER = ("pyrefit", "bridge.py")
# Bumping this invalidates an already staged copy.
RUNTIME_VERSION = "0.2.0"
_STAGE_MARKER = ".mppatcher-drape-version"


def _is_usable(root: str) -> bool:
    """True when the CLI's own existence checks will also succeed on `root`."""
    return (os.path.isfile(os.path.join(root, *BRIDGE_MARKER))
            and os.path.isfile(os.path.join(root, BRIDGE_EXE)))


def _is_present(root: str) -> bool:
    """True when `root` holds a runtime, judged by enumeration only.

    Used for the VFS case, where the files are readable but invisible to stat.
    """
    entries = {name.lower() for name in vfs_listdir(root)}
    return BRIDGE_EXE.lower() in entries and BRIDGE_MARKER[0].lower() in entries


def _candidates(app_dir: str) -> list[str]:
    seen, out = set(), []
    for base in (app_dir, os.path.dirname(app_dir or "")):
        if not base:
            continue
        path = os.path.join(base, RUNTIME_DIRNAME)
        if path.lower() not in seen:
            seen.add(path.lower())
            out.append(path)
    return out


def _stage_root() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "MPPatcher", "drape-runtime")


def _copy_tree_via_enumeration(source: str, target: str) -> None:
    """Copy `source` into `target` without stat'ing anything in `source`.

    ``shutil.copytree`` decides what is a directory from stat results, which is
    exactly the information MO2 does not provide for a virtual path.  Listing a
    folder is the test that does work.
    """
    os.makedirs(target, exist_ok=True)
    for name in vfs_listdir(source):
        src = os.path.join(source, name)
        dst = os.path.join(target, name)
        if vfs_isdir(src):
            _copy_tree_via_enumeration(src, dst)
            continue
        with open(src, "rb") as reader, open(dst, "wb") as writer:
            shutil.copyfileobj(reader, writer, length=1024 * 1024)


def _stage(source: str) -> str:
    """Return a physical copy of the runtime, copying it on first use."""
    target = _stage_root()
    marker = os.path.join(target, _STAGE_MARKER)
    try:
        with open(marker, "r", encoding="utf-8") as handle:
            if handle.read().strip() == RUNTIME_VERSION and _is_usable(target):
                return target
    except OSError:
        pass
    shutil.rmtree(target, ignore_errors=True)
    _copy_tree_via_enumeration(source, target)
    if not _is_usable(target):
        return ""
    with open(marker, "w", encoding="utf-8") as handle:
        handle.write(RUNTIME_VERSION)
    return target


def find_runtime(app_dir: str) -> str:
    """Return a usable Drape runtime root, or '' when none can be reached."""
    for candidate in _candidates(app_dir):
        if _is_usable(candidate):
            return candidate
    for candidate in _candidates(app_dir):
        if _is_present(candidate):
            staged = _stage(candidate)
            if staged:
                return staged
    return ""


def drape_environment(app_dir: str) -> dict[str, str]:
    """Return the MPP_DRAPE_* pair for a Smart refit run.

    An operator who already exports the variables keeps their own checkout: the
    development Drape tree is the reference implementation and must stay
    selectable without touching the install.
    """
    if os.environ.get("MPP_DRAPE_ROOT") and os.environ.get("MPP_DRAPE_PYTHON"):
        return {}
    root = find_runtime(app_dir)
    if not root:
        return {}
    return {"MPP_DRAPE_ROOT": root,
            "MPP_DRAPE_PYTHON": os.path.join(root, BRIDGE_EXE)}
