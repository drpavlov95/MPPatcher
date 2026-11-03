"""Input-source helpers shared by the GUI and the public patch workflows.

The BodySlide pipeline consumes a data tree, while a user-facing patcher should
also accept the two layouts people commonly have on disk: a mod folder and a
ZIP archive.  This module deliberately keeps archive extraction outside the C++
CLI so the native pipeline still receives ordinary files and never writes into
the source archive.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


MAX_ZIP_FILES = 50_000
MAX_ZIP_MEMBER_BYTES = 2 * 1024 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 8 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class PhysicalBodySlideProject:
    """Physical provider for one OSP, outside MO2's merged VFS."""

    project_path: Path
    body_root: Path
    mod_root: Path


def _same_or_below(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path.resolve()), str(root.resolve()))) == str(root.resolve())
    except (OSError, ValueError):
        return False


def _mods_root_candidates(*, configured: str = "", output_root: str = "",
                          app_dir: str = "") -> list[Path]:
    """Find likely physical MO2 ``mods`` roots without trusting VFS paths."""
    candidates: list[Path] = []

    def add(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved.is_dir() and resolved.name.casefold() == "mods" and resolved not in candidates:
            candidates.append(resolved)

    if configured:
        add(Path(configured))
    for value in (output_root, app_dir):
        if not value:
            continue
        path = Path(value)
        for parent in (path, *path.parents):
            if parent.name.casefold() == "mods":
                add(parent)
            add(parent / "mods")
    return candidates


def mo2_texture_environment(mods_root: str | os.PathLike[str]) -> dict[str, str]:
    """Return the small environment contract used for MO2 texture lookup.

    The native patcher receives a physical BodySlide provider so generated
    output can never become its next input.  Outfit textures often live in a
    different enabled mod, however.  Passing the profile's mod list lets the
    native resolver find the winning physical texture without flattening the
    complete MO2 VFS or putting hundreds of mod paths in one environment value.
    """
    if not mods_root:
        return {}
    mods = Path(mods_root)
    instance = mods.parent
    ini = instance / "ModOrganizer.ini"
    try:
        text = ini.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    match = re.search(
        r"^selected_profile=(?:@ByteArray\((.*?)\)|(.*?))\s*$",
        text, flags=re.MULTILINE)
    if not match:
        return {}
    profile_name = (match.group(1) or match.group(2) or "").strip().strip('"')
    profile_part = Path(profile_name)
    if (not profile_name or profile_part.is_absolute() or
            profile_part.name != profile_name or profile_name in {"..", "."}):
        return {}
    modlist = instance / "profiles" / profile_name / "modlist.txt"
    if not mods.is_dir() or not modlist.is_file():
        return {}
    return {
        "AUTOZAP_MO2_MODS_ROOT": str(mods.resolve()),
        "AUTOZAP_MO2_MODLIST": str(modlist.resolve()),
    }


def _osp_set_names(path: Path) -> set[str]:
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeError):
        return set()
    return {
        node.get("name", "").strip()
        for node in root.findall("SliderSet")
        if node.get("name", "").strip()
    }


def resolve_physical_bodyslide_project(
        project_filename: str, selected_sets: set[str], *,
        virtual_bodyslide_root: str, output_root: str,
        configured_mods_root: str = "", app_dir: str = "",
) -> PhysicalBodySlideProject:
    """Resolve the physical mod that owns a selected OSP.

    Reading ShapeData from MO2's merged directory is unsafe when the output mod
    is enabled: the output intentionally reuses the source NIF/OSD paths and can
    therefore become the next run's input.  The visible OSP bytes identify its
    physical provider; the output mod is always excluded.
    """
    filename = Path(project_filename).name
    if not filename.lower().endswith(".osp"):
        raise LookupError(f"invalid BodySlide project name: {project_filename}")

    output = Path(output_root).resolve() if output_root else None
    visible_path = Path(virtual_bodyslide_root) / "SliderSets" / filename
    try:
        visible_bytes = visible_path.read_bytes()
    except OSError:
        visible_bytes = None

    matches: list[PhysicalBodySlideProject] = []
    for mods_root in _mods_root_candidates(
            configured=configured_mods_root, output_root=output_root,
            app_dir=app_dir):
        try:
            mod_dirs = sorted((p for p in mods_root.iterdir() if p.is_dir()),
                              key=lambda p: p.name.casefold())
        except OSError:
            continue
        for mod_root in mod_dirs:
            if output is not None and (_same_or_below(output, mod_root)
                                       or _same_or_below(mod_root, output)):
                continue
            body_root = mod_root / "CalienteTools" / "BodySlide"
            project_path = body_root / "SliderSets" / filename
            if not project_path.is_file():
                continue
            names = _osp_set_names(project_path)
            if not selected_sets.issubset(names):
                continue
            matches.append(PhysicalBodySlideProject(project_path, body_root, mod_root))

    if not matches:
        raise LookupError(
            f"physical provider not found for {filename} "
            f"({', '.join(sorted(selected_sets))})")

    if visible_bytes is not None:
        visible_matches = []
        for candidate in matches:
            try:
                if candidate.project_path.read_bytes() == visible_bytes:
                    visible_matches.append(candidate)
            except OSError:
                pass
        if len(visible_matches) == 1:
            return visible_matches[0]
        if visible_matches:
            matches = visible_matches

    if len(matches) == 1:
        return matches[0]
    providers = ", ".join(candidate.mod_root.name for candidate in matches[:6])
    raise LookupError(f"ambiguous physical provider for {filename}: {providers}")


def normalize_output_ownership(root: str | os.PathLike[str]) -> tuple[int, list[str]]:
    """Give generated Windows files back to the interactive account.

    MO2 is sometimes launched elevated.  Windows then chooses the local
    Administrators group as the default owner of files created by its child CLI,
    which makes normal Explorer sessions show a misleading administrator prompt.
    The token user is still the real account, so explicitly grant that SID full
    control and make it the owner after a job finishes.  The explicit grant is
    important for trees whose inherited ACL contains an OWNER RIGHTS entry: an
    owner-only change can otherwise remove the user's effective access.
    """
    if os.name != "nt":
        return 0, []

    import ctypes
    from ctypes import wintypes

    TOKEN_QUERY = 0x0008
    TOKEN_USER_CLASS = 1
    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 0x00000001
    ERROR_INSUFFICIENT_BUFFER = 122

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD)]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    advapi32.SetNamedSecurityInfoW.restype = wintypes.DWORD
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
        return 0, [f"OpenProcessToken failed ({ctypes.get_last_error()})"]
    try:
        needed = wintypes.DWORD()
        advapi32.GetTokenInformation(
            token, TOKEN_USER_CLASS, None, 0, ctypes.byref(needed))
        error = ctypes.get_last_error()
        if error != ERROR_INSUFFICIENT_BUFFER or not needed.value:
            return 0, [f"GetTokenInformation size failed ({error})"]
        buffer = ctypes.create_string_buffer(needed.value)
        if not advapi32.GetTokenInformation(
                token, TOKEN_USER_CLASS, buffer, needed,
                ctypes.byref(needed)):
            return 0, [f"GetTokenInformation failed ({ctypes.get_last_error()})"]
        # TOKEN_USER begins with SID_AND_ATTRIBUTES; its first field is PSID.
        sid = ctypes.c_void_p.from_buffer(buffer).value
        if not sid:
            return 0, ["current token has no user SID"]

        sid_string = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(
                ctypes.c_void_p(sid), ctypes.byref(sid_string)):
            return 0, [f"ConvertSidToStringSid failed ({ctypes.get_last_error()})"]
        try:
            sid_text = sid_string.value
        finally:
            kernel32.LocalFree(ctypes.cast(sid_string, ctypes.c_void_p))
        if not sid_text:
            return 0, ["current token has an empty user SID"]

        base = Path(root)
        if not base.exists():
            return 0, []
        targets = [base]
        for current, dirs, files in os.walk(base, followlinks=False):
            folder = Path(current)
            targets.extend(folder / name for name in dirs)
            targets.extend(folder / name for name in files)

        # Do this before changing the owner.  Some inherited ACLs explicitly
        # suppress the normal implicit owner rights.
        grant = subprocess.run(
            ["icacls.exe", str(base), "/grant:r",
             f"*{sid_text}:(OI)(CI)F", "/T", "/C", "/Q"],
            capture_output=True, text=True, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
        if grant.returncode:
            detail = (grant.stderr or grant.stdout).strip()
            return 0, [f"icacls grant failed ({grant.returncode}): {detail}"]

        changed = 0
        failures: list[str] = []
        for target in targets:
            status = advapi32.SetNamedSecurityInfoW(
                str(target), SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION,
                ctypes.c_void_p(sid), None, None, None)
            if status:
                failures.append(f"{target}: Windows error {status}")
            else:
                changed += 1
        return changed, failures
    finally:
        kernel32.CloseHandle(token)


def _safe_member(root: Path, name: str) -> Path:
    # ZIP names are always slash-separated, but normalising through Path also
    # rejects drive letters and .. traversal on Windows.
    rel = Path(name.replace("\\", "/"))
    if rel.is_absolute() or rel.drive or any(part == ".." for part in rel.parts):
        raise ValueError(f"unsafe ZIP member: {name}")
    dest = (root / rel).resolve()
    if os.path.commonpath((str(root.resolve()), str(dest))) != str(root.resolve()):
        raise ValueError(f"unsafe ZIP member: {name}")
    return dest


def extract_zip(archive: str | os.PathLike[str], destination: str | os.PathLike[str]) -> Path:
    """Extract *archive* safely and return the extraction root."""
    root = Path(destination).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        members = zf.infolist()
        if len(members) > MAX_ZIP_FILES:
            raise ValueError(f"ZIP contains too many entries ({len(members)} > {MAX_ZIP_FILES})")
        total_size = sum(max(0, member.file_size) for member in members if not member.is_dir())
        if total_size > MAX_ZIP_TOTAL_BYTES:
            raise ValueError("ZIP uncompressed size exceeds the safety limit")
        seen = set()
        for member in members:
            if member.file_size > MAX_ZIP_MEMBER_BYTES:
                raise ValueError(f"ZIP member is too large: {member.filename}")
            dest = _safe_member(root, member.filename)
            if dest in seen:
                raise ValueError(f"duplicate ZIP member: {member.filename}")
            seen.add(dest)
            # Do not extract Unix symlinks as regular files.
            unix_mode = (member.external_attr >> 16) & 0o170000
            if unix_mode == 0o120000:
                raise ValueError(f"symlink ZIP member is not allowed: {member.filename}")
            if member.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, dest.open("wb") as out:
                copied = 0
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > MAX_ZIP_MEMBER_BYTES:
                        raise ValueError(f"ZIP member expanded beyond the safety limit: {member.filename}")
                    out.write(chunk)
                if copied != member.file_size:
                    raise ValueError(f"ZIP member size mismatch: {member.filename}")
    return root


def _walk_dirs(root: Path):
    yield root
    for current, dirs, _files in os.walk(root):
        for name in dirs:
            yield Path(current) / name


def find_bodyslide_root(root: str | os.PathLike[str]) -> Path:
    """Find the BodySlide data root inside a folder/archive extraction.

    Accepts either ``.../CalienteTools/BodySlide`` or a directory containing
    ``SliderSets`` directly.  The shortest matching path wins, which avoids
    accidentally selecting a nested test output when a mod archive contains
    several unrelated folders.
    """
    base = Path(root)
    candidates = []
    for folder in _walk_dirs(base):
        try:
            names = {p.name.lower() for p in folder.iterdir()}
        except OSError:
            continue
        if "slidersets" in names and "shapedata" in names:
            candidates.append(folder)
        elif folder.name.lower() == "bodyslide" and (folder / "SliderSets").exists():
            candidates.append(folder)
    return min(candidates, key=lambda p: len(p.parts)) if candidates else base


def find_tri_root(root: str | os.PathLike[str]) -> Path:
    """Return the best root for recursive headpart TRI scanning."""
    base = Path(root)
    meshes = []
    for p in _walk_dirs(base):
        if p.name.lower() != "meshes":
            continue
        try:
            has_tri = any(name.lower().endswith(".tri")
                          for _current, _dirs, files in os.walk(p)
                          for name in files)
        except OSError:
            has_tri = False
        if has_tri:
            meshes.append(p)
    if meshes:
        return min(meshes, key=lambda p: len(p.parts))
    return base


class StagedSource:
    """A source path plus an optional temporary extraction directory."""

    def __init__(self, path: str, kind: str):
        self.original = str(path)
        self.kind = kind
        self._temp: Path | None = None
        if kind == "zip":
            self._temp = Path(tempfile.mkdtemp(prefix="mppatcher_source_"))
            try:
                self.root = extract_zip(path, self._temp)
            except Exception:
                shutil.rmtree(self._temp, ignore_errors=True)
                self._temp = None
                raise
        else:
            self.root = Path(path)

    def cleanup(self) -> None:
        if self._temp is not None:
            shutil.rmtree(self._temp, ignore_errors=True)
            self._temp = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.cleanup()
