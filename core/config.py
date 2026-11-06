r"""Config — mirrors BodySlide's own path-detection strategy.

BodySlide sets AppDir = wxStandardPaths::Get().GetDataDir() = directory of the exe.
When launched via MO2, that path is the *virtual* VFS path (e.g.
C:\...\Data\CalienteTools\BodySlide\). MO2 then transparently serves merged mod files
under that virtual directory via usvfs hooks. All wxWidgets file calls (FindFirstFileW,
etc.) work through VFS because usvfs hooks at the kernel level.

Python's os.listdir / os.path.isdir also use FindFirstFileW / GetFileAttributesW, which
are the same hooks, so the VFS path works for us too — we just need to use the same
base path BodySlide uses: os.path.dirname(sys.executable).
"""
import os
import subprocess
import sys
from PySide6.QtCore import QSettings


_BS_MARKERS = ("OutfitStudio x64.exe", "OutfitStudio.exe", "SliderGroups", "SliderSets")


# ── VFS-safe primitives ────────────────────────────────────────────────
# MO2's usvfs hooks directory ENUMERATION (FindFirstFileW/FindNextFileW, used
# by os.listdir / os.scandir) but NOT the stat family (GetFileAttributesW, used
# by os.path.isdir / exists / isfile). So we must never use the stat family on
# VFS paths — only enumeration. Confirmed empirically via diagnostics.
#
# They live in core.vfs, which imports no Qt, so the command-line paths can use
# them without dragging PySide6 in for the QSettings object below. Re-exported
# here because this is where callers have always found them.
from core.vfs import vfs_find, vfs_isdir, vfs_listdir  # noqa: E402,F401


def _looks_like_bodyslide(path: str) -> bool:
    if not path:
        return False
    try:
        entries = {e.lower() for e in os.listdir(path)}
    except OSError:
        return False
    return any(m.lower() in entries for m in _BS_MARKERS)


def _exe_dir() -> str:
    """Return the BodySlide data directory (virtual VFS path when under MO2).

    Mirrors BodySlide's GetDataDir() / AppDir = directory of the exe.

    Handles two deployment layouts:
      - onefile:  .../CalienteTools/BodySlide/MPPatcher.exe        -> exe_dir is BodySlide
      - onedir:   .../CalienteTools/BodySlide/MPPatcher/MPPatcher.exe -> parent is BodySlide
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
    else:
        # Dev mode: project root (two levels up from core/)
        exe_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # If the exe dir itself looks like BodySlide, use it (onefile layout)
    if _looks_like_bodyslide(exe_dir):
        return exe_dir
    # Otherwise check the parent (onedir layout: exe is in a subfolder)
    parent = os.path.dirname(exe_dir)
    if _looks_like_bodyslide(parent):
        return parent
    # Fallback: exe dir as-is
    return exe_dir


# Keep find_all_slider_groups_folders as a fallback for the user-set mods folder
def find_all_slider_groups_folders(mods_root: str) -> list[str]:
    """Return all physical SliderGroups folders across installed mods."""
    folders = []
    for mod in vfs_listdir(mods_root):
        sg = os.path.join(mods_root, mod, "CalienteTools", "BodySlide", "SliderGroups")
        if vfs_isdir(sg):
            folders.append(sg)
    return folders


class Config:
    def __init__(self):
        self._s = QSettings("ModernPhysiquePicker", "MPPatcher")
        self._app_dir = _exe_dir()

        # Auto-detect BodySlide path on first run
        if not self._get("bodyslide_path") and self._app_dir:
            self._set("bodyslide_path", self._app_dir)

    # ── Read-only computed ─────────────────────────────────────────────

    @property
    def app_dir(self) -> str:
        """Exe directory — same as BodySlide's AppDir. Virtual VFS path under MO2."""
        return self._app_dir

    @property
    def slider_groups_folder(self) -> str:
        """Virtual VFS SliderGroups path — mirrors AppDir + '/SliderGroups'."""
        root = self.bodyslide_root
        return os.path.join(root, "SliderGroups") if root else ""

    @property
    def bodyslide_root(self) -> str:
        """Return the usable BodySlide root without assuming exe location."""
        configured = str(self.bodyslide_path or "").strip()
        if _looks_like_bodyslide(configured):
            return configured
        if _looks_like_bodyslide(self._app_dir):
            return self._app_dir
        return configured or self._app_dir

    @property
    def outfit_studio_exe(self) -> str:
        return vfs_find(self.bodyslide_root, "OutfitStudio x64.exe", "OutfitStudio.exe")

    @property
    def mppatcher_cli_exe(self) -> str:
        # Prefer the isolated public pipeline. The internal binary remains available
        # as an explicit fallback for the migration workflow, but the normal GUI path
        # must never silently select the legacy/body-rebase development executable.
        names = ("mppatcher-cli-public.exe", "mppatcher-cli.exe",
                 "mppatcher-cli-internal.exe")
        return (vfs_find(self._app_dir, *names)
                or vfs_find(self.bodyslide_root, *names))

    @property
    def cli_is_internal(self) -> bool:
        """True when the CLI we found advertises the destructive internal tools.

        Asking the binary is the only reliable test — the public build is compiled
        without that code, so there is nothing to detect from the filename alone.
        Result is cached per exe path; --help is cheap but this is read on redraw.
        """
        exe = self.mppatcher_cli_exe
        if not exe:
            return False
        cached = getattr(self, "_cli_internal_cache", None)
        if cached and cached[0] == exe:
            return cached[1]
        flag = False
        try:
            kw = {}
            if hasattr(subprocess, "CREATE_NO_WINDOW"):
                kw["creationflags"] = subprocess.CREATE_NO_WINDOW
            out = subprocess.run([exe, "--help"], capture_output=True, text=True,
                                 timeout=10, **kw).stdout or ""
            flag = "INTERNAL TOOLS" in out
        except Exception:
            flag = False
        self._cli_internal_cache = (exe, flag)
        return flag

    @property
    def automations_folder(self) -> str:
        return os.path.join(self._app_dir, "Automations") if self._app_dir else ""

    # ── Persistent settings ────────────────────────────────────────────

    def _get(self, key, default=""):
        return self._s.value(key, default)

    def _set(self, key, value):
        self._s.setValue(key, value)

    @property
    def bodyslide_path(self): return self._get("bodyslide_path")
    @bodyslide_path.setter
    def bodyslide_path(self, v): self._set("bodyslide_path", v)

    @property
    def mods_folder(self) -> str:
        """Optional user-set physical MO2 mods folder for physical-path fallbacks."""
        return self._get("mods_folder")

    @mods_folder.setter
    def mods_folder(self, v: str):
        self._set("mods_folder", v)

    @property
    def output_folder(self): return self._get("output_folder")
    @output_folder.setter
    def output_folder(self, v): self._set("output_folder", v)

    @property
    def output_mode(self) -> str:
        value = str(self._get("output_mode", "folder") or "folder").lower()
        return value if value in {"folder", "zip"} else "folder"

    @output_mode.setter
    def output_mode(self, value: str):
        self._set("output_mode", "zip" if value == "zip" else "folder")

    @property
    def output_archive(self): return self._get("output_archive")
    @output_archive.setter
    def output_archive(self, v): self._set("output_archive", v)

    @property
    def backup_folder(self): return self._get("backup_folder")
    @backup_folder.setter
    def backup_folder(self, v): self._set("backup_folder", v)

    @property
    def new_project_mode(self): return self._get("new_project_mode", True)
    @new_project_mode.setter
    def new_project_mode(self, v): self._set("new_project_mode", v)

    @property
    def backup_on_overwrite(self): return self._get("backup_on_overwrite", True)
    @backup_on_overwrite.setter
    def backup_on_overwrite(self, v): self._set("backup_on_overwrite", v)

    @property
    def auto_zap(self): return self._get("auto_zap", True)
    @auto_zap.setter
    def auto_zap(self, v): self._set("auto_zap", v)

    # No global smart_refit setting: the flag is per outfit, carried by
    # OutfitInfo.smart_refit and persisted with the rest of the scan state.

    @property
    def smart_refit_warning_suppressed(self) -> bool:
        value = self._get("smart_refit_warning_suppressed", False)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    @smart_refit_warning_suppressed.setter
    def smart_refit_warning_suppressed(self, value: bool):
        self._set("smart_refit_warning_suppressed", bool(value))

    @property
    def reduce_clipping(self): return self._get("reduce_clipping", False)
    @reduce_clipping.setter
    def reduce_clipping(self, v): self._set("reduce_clipping", v)

    @property
    def fix_clipping_strength(self): return self._get("fix_clipping_strength", 0.5)
    @fix_clipping_strength.setter
    def fix_clipping_strength(self, v): self._set("fix_clipping_strength", v)

    @property
    def theme(self): return self._get("theme", "dark")
    @theme.setter
    def theme(self, v): self._set("theme", v)
