"""Minimal reader for Bethesda BSA archives, enough to pull named files out.

Written because the vanilla hair meshes only exist inside Skyrim - Meshes0.bsa, and
Beards of Power ships a 911 MB archive with no loose files. Only reading is
supported, and only the parts needed to locate and decompress an entry: this is a
way into the data, not an archive tool.

Versions 103/104 (Skyrim LE, Fallout 3/NV) compress with zlib; 105 (Skyrim SE) uses
LZ4. Both are handled, because the format flag says which and guessing would corrupt
silently.

Layout, per the long-standing community documentation of the format:

    header      36 bytes: "BSA\\0", version, folderRecordOffset, archiveFlags,
                folderCount, fileCount, totalFolderNameLength, totalFileNameLength,
                fileFlags (u16), padding (u16)
    folders     folderCount records. v105 widens the offset field to 64 bits, so a
                record is 24 bytes there against 16 in the older versions.
    per folder  optional length-prefixed name, then its file records
                (hash u64, size u32, offset u32)
    names       one zero-terminated name per file, in file order
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

MAGIC = b"BSA\x00"

FLAG_DIR_NAMES = 0x1
FLAG_FILE_NAMES = 0x2
FLAG_COMPRESSED = 0x4
FLAG_EMBED_NAMES = 0x100

# A file's size field carries this bit to mean "the archive-wide compression flag
# does not apply to me". It is the one place where per-file state overrides the
# header, and ignoring it decompresses stored data (or stores compressed data).
SIZE_COMPRESSION_FLIPPED = 0x40000000
SIZE_MASK = 0x3FFFFFFF


class BsaError(Exception):
    """Archive is not a readable BSA."""


@dataclass
class BsaEntry:
    path: str          # folder/name, as stored (backslashes)
    offset: int
    raw_size: int
    compressed: bool


class BsaArchive:
    """Opens an archive and indexes its entries; reads file data on demand."""

    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "rb")
        self.entries: dict[str, BsaEntry] = {}
        self._parse()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> BsaArchive:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _parse(self) -> None:
        f = self._fh
        head = f.read(36)
        if len(head) < 36 or head[:4] != MAGIC:
            raise BsaError(f"{self.path}: not a BSA")
        (_magic, self.version, folder_offset, self.archive_flags,
         folder_count, file_count, total_folder_name_len, total_file_name_len,
         self.file_flags, _pad) = struct.unpack("<4sIIIIIIIHH", head)

        if self.version not in (103, 104, 105):
            raise BsaError(f"{self.path}: unsupported BSA version {self.version}")

        self._compressed_by_default = bool(self.archive_flags & FLAG_COMPRESSED)
        self._embed_names = bool(self.archive_flags & FLAG_EMBED_NAMES)

        # folder records
        f.seek(folder_offset)
        rec_size = 24 if self.version == 105 else 16
        raw = f.read(folder_offset and rec_size * folder_count)
        folders = []
        for i in range(folder_count):
            base = i * rec_size
            if self.version == 105:
                _hash, count, _pad2, off = struct.unpack_from("<QIIQ", raw, base)[:4]
                _hash, count, _pad2, off = struct.unpack_from("<QIIQ", raw, base)
            else:
                _hash, count, off = struct.unpack_from("<QII", raw, base)
            # The stored offset counts the file-name block as if it preceded the
            # file records, so it must be removed to get a real file position.
            folders.append((count, off - total_file_name_len))

        # file records, folder by folder
        file_records = []
        folder_names = []
        for count, off in folders:
            f.seek(off)
            name = ""
            if self.archive_flags & FLAG_DIR_NAMES:
                (nlen,) = struct.unpack("<B", f.read(1))
                name = f.read(nlen).rstrip(b"\x00").decode("cp1252", "replace")
            folder_names.append(name)
            blob = f.read(count * 16)
            for j in range(count):
                _h, size, foff = struct.unpack_from("<QII", blob, j * 16)
                file_records.append((name, size, foff))

        # the name block, one zero-terminated name per file in the same order
        names: list[str] = []
        if self.archive_flags & FLAG_FILE_NAMES:
            block = f.read(total_file_name_len)
            names = [n.decode("cp1252", "replace")
                     for n in block.split(b"\x00")[:file_count]]
        if len(names) != len(file_records):
            # Without names the archive can still be read by index, but nothing
            # here can ask for a path, so refuse rather than return a useless map.
            raise BsaError(f"{self.path}: {len(names)} names for "
                           f"{len(file_records)} file records")

        for (folder, size, off), name in zip(file_records, names):
            flipped = bool(size & SIZE_COMPRESSION_FLIPPED)
            comp = self._compressed_by_default != flipped
            key = (folder + "\\" + name) if folder else name
            self.entries[key.lower()] = BsaEntry(
                path=key, offset=off, raw_size=size & SIZE_MASK, compressed=comp)

    def read(self, path: str) -> bytes:
        key = path.lower().replace("/", "\\")
        e = self.entries.get(key)
        if e is None:
            raise KeyError(path)
        f = self._fh
        f.seek(e.offset)
        size = e.raw_size
        if self._embed_names:
            (nlen,) = struct.unpack("<B", f.read(1))
            f.read(nlen)
            size -= 1 + nlen
        if not e.compressed:
            return f.read(size)

        (unpacked,) = struct.unpack("<I", f.read(4))
        payload = f.read(size - 4)
        if self.version == 105:
            import lz4.frame
            out = lz4.frame.decompress(payload)
        else:
            import zlib
            out = zlib.decompress(payload)
        if len(out) != unpacked:
            raise BsaError(f"{self.path}: {e.path} decompressed to {len(out)} bytes, "
                           f"header says {unpacked}")
        return out

    def glob(self, *, folder_contains: str = "", suffix: str = "") -> list[str]:
        """Entry paths whose folder contains a substring and which end in suffix."""
        fc = folder_contains.lower().replace("/", "\\")
        sf = suffix.lower()
        return sorted(k for k in self.entries
                      if (not fc or fc in k) and (not sf or k.endswith(sf)))


def extract(archives: list[str], out_root: str, *, folder_contains: str = "",
            suffix: str = "") -> tuple[int, list[str]]:
    """Extract matching entries from several archives. Later archives do not
    overwrite earlier ones, mirroring how the game resolves a first hit."""
    written, failures = 0, []
    seen: set[str] = set()
    for arc in archives:
        with BsaArchive(arc) as a:
            for key in a.glob(folder_contains=folder_contains, suffix=suffix):
                if key in seen:
                    continue
                seen.add(key)
                e = a.entries[key]
                dest = os.path.join(out_root, e.path.replace("\\", os.sep))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                try:
                    data = a.read(e.path)
                except Exception as exc:                      # noqa: BLE001
                    failures.append(f"{e.path}: {exc}")
                    continue
                with open(dest, "wb") as fh:
                    fh.write(data)
                written += 1
    return written, failures
