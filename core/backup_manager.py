"""Validated, manifest-backed backup and restore helpers."""
import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class BackupEntry:
    timestamp: str
    zip_path: str
    file_count: int


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def create_backup(file_paths: list[str], backup_folder: str) -> str | None:
    """Create a manifest-backed backup, failing if any requested file is unreadable."""
    try:
        os.makedirs(backup_folder, exist_ok=True)
    except OSError:
        pass
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_path = os.path.join(backup_folder, f"MPPatcher_backup_{ts}.zip")
    entries = []
    try:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for index, src in enumerate(file_paths):
                try:
                    with open(src, "rb") as fh:
                        data = fh.read()
                except OSError as exc:
                    raise OSError(f"could not read backup source: {src}: {exc}") from exc
                payload = f"payload/{index:08d}_{Path(src).name}"
                zf.writestr(payload, data)
                entries.append({
                    "path": os.path.abspath(src),
                    "payload": payload,
                    "size": len(data),
                    "sha256": _sha256(data),
                })
            zf.writestr("manifest.json", json.dumps({
                "format": 2,
                "created": datetime.now().isoformat(timespec="seconds"),
                "files": entries,
            }, indent=2))
        return zip_path
    except Exception as exc:
        try:
            os.remove(zip_path)
        except OSError:
            pass
        print(f"Backup failed: {exc}")
        return None


def _allowed_destination(path_text: str, allowed_roots: list[str]) -> str | None:
    destination = os.path.abspath(path_text)
    for root in allowed_roots:
        root_abs = os.path.abspath(root)
        try:
            if os.path.commonpath((destination, root_abs)) == root_abs:
                return destination
        except ValueError:
            continue
    return None


def restore_backup(zip_path: str, allowed_roots: list[str] | None = None) -> bool:
    """Restore a backup only inside explicitly approved roots.

    The old format with absolute ZIP member names remains readable, but no
    restore is attempted unless the caller supplies an allowed destination
    root. Every member is validated before the first destination is written.
    """
    if not allowed_roots:
        print("Restore refused: no allowed destination roots were supplied")
        return False
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            if zf.testzip() is not None:
                raise ValueError("backup archive failed CRC validation")
            names = set(zf.namelist())
            manifest = None
            if "manifest.json" in names:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                files = manifest.get("files")
                if not isinstance(files, list):
                    raise ValueError("invalid backup manifest")
                planned = []
                seen = set()
                for entry in files:
                    original = entry.get("path", "")
                    payload = entry.get("payload", "")
                    dest = _allowed_destination(original, allowed_roots)
                    if not dest or payload not in names or dest in seen:
                        raise ValueError(f"backup destination is not allowed: {original}")
                    seen.add(dest)
                    data = zf.read(payload)
                    if len(data) != int(entry.get("size", -1)) or _sha256(data) != entry.get("sha256"):
                        raise ValueError(f"backup integrity check failed: {original}")
                    planned.append((dest, data))
            else:
                # Legacy archives used absolute paths as member names. They
                # are accepted only when every path is within allowed_roots.
                planned = []
                seen = set()
                for member in zf.infolist():
                    if member.is_dir():
                        continue
                    raw = member.filename.replace("/", os.sep)
                    dest = _allowed_destination(raw, allowed_roots)
                    if not dest or dest in seen:
                        raise ValueError(f"legacy backup destination is not allowed: {member.filename}")
                    seen.add(dest)
                    planned.append((dest, zf.read(member)))

            # Stage every file in its destination directory, then atomically
            # replace. If validation fails, the original tree remains untouched.
            staged = []
            try:
                for dest, data in planned:
                    target = Path(dest)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.mpp_restore_",
                                                     dir=str(target.parent))
                    with os.fdopen(fd, "wb") as out:
                        out.write(data)
                    staged.append((temp_name, dest))
                for temp_name, dest in staged:
                    os.replace(temp_name, dest)
            finally:
                for temp_name, _dest in staged:
                    try:
                        os.remove(temp_name)
                    except OSError:
                        pass
        return True
    except Exception as exc:
        print(f"Restore failed: {exc}")
        return False


def list_backups(backup_folder: str) -> list[BackupEntry]:
    try:
        all_files = os.listdir(backup_folder)
    except OSError:
        return []
    entries = []
    pattern = re.compile(r"MPPatcher_backup_(\d{8}_\d{6})\.zip")
    for fname in sorted(all_files, reverse=True):
        match = pattern.fullmatch(fname)
        if not match:
            continue
        zip_path = os.path.join(backup_folder, fname)
        ts_str = match.group(1)
        try:
            dt = datetime.strptime(ts_str, "%Y%m%d_%H%M%S")
            ts_display = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            ts_display = ts_str
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                if "manifest.json" in zf.namelist():
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                    file_count = len(manifest.get("files", []))
                else:
                    file_count = len(zf.namelist())
        except Exception:
            file_count = 0
        entries.append(BackupEntry(timestamp=ts_display, zip_path=zip_path,
                                   file_count=file_count))
    return entries
