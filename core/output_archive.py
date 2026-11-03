"""Create a mod-ready ZIP from an isolated MPPatcher output tree."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile


@dataclass(frozen=True)
class ArchiveResult:
    path: Path
    file_count: int


def create_zip_archive(source_root: str | os.PathLike[str],
                       destination: str | os.PathLike[str]) -> ArchiveResult:
    """Write ``source_root`` contents to ``destination`` atomically.

    Archive entries are relative to ``source_root`` so the resulting ZIP can be
    installed directly as a mod.  The destination is only replaced after a
    complete archive has been written successfully.
    """
    source = Path(source_root)
    target = Path(destination)
    if not source.is_dir():
        raise ValueError(f"Output folder does not exist: {source}")
    if target.suffix.lower() != ".zip":
        target = target.with_suffix(".zip")
    if source.resolve() == target.resolve():
        raise ValueError("The ZIP destination cannot be the output folder.")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    file_count = 0
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED,
                     compresslevel=6) as archive:
            for path in sorted(source.rglob("*")):
                relative = path.relative_to(source)
                if path.is_dir():
                    # Preserve intentionally empty directories without adding
                    # redundant entries for directories implied by a file.
                    if not any(path.iterdir()):
                        archive.writestr(relative.as_posix().rstrip("/") + "/", "")
                    continue
                if path.is_file():
                    archive.write(path, relative.as_posix())
                    file_count += 1
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return ArchiveResult(path=target, file_count=file_count)
