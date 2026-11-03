"""Lossless reader/writer for Skyrim FRTRI003 face/headpart TRI files.

Layout verified empirically 2026-07-03 against AHPH, COtR and vanilla-style
chargen TRIs (9/9 files walk to exact EOF) — see the format section of
docs/superpowers/plans/2026-07-03-facemorphs-patcher-A-core.md.
Reference C++ parser: SKEE FaceMorphInterface.cpp (TRIFile::Load).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

MAGIC = b"FRTRI003"


class TriError(Exception):
    """File is not a valid/complete FRTRI003 TRI."""


@dataclass
class Morph:
    name_raw: bytes                # exact on-disk bytes, normally ends with b"\x00"
    multiplier: float              # float32 value; 0.0 for all-zero morphs
    deltas_i16: np.ndarray         # (vertex_count, 3) int16

    @property
    def name(self) -> str:
        return self.name_raw.rstrip(b"\x00").decode("ascii", errors="replace")

    def deltas(self) -> np.ndarray:
        """World-space float64 deltas (vertex_count, 3)."""
        return self.deltas_i16.astype(np.float64) * self.multiplier


@dataclass
class TriFile:
    vertex_count: int
    verts: np.ndarray              # (vc, 3) float32 base vertices
    faces: np.ndarray              # (polytris, 3) uint32
    # header fields preserved verbatim for write-back (numMorphs is recomputed)
    polyquads: int
    unk2: int
    unk3: int
    uv_verts: int
    flags: int
    num_mods: int
    mod_verts: int
    unk7: int
    unk8: int
    unk9: int
    unk10: int
    # sections this tool never interprets — preserved byte-for-byte
    modverts_raw: bytes = b""
    quads_raw: bytes = b""
    uv_raw: bytes = b""
    uvfaces_raw: bytes = b""
    uvquads_raw: bytes = b""
    mods_raw: bytes = b""
    morphs: list[Morph] = field(default_factory=list)

    def morph_names(self) -> list[str]:
        return [m.name for m in self.morphs]


def read_tri(data: bytes, *, source: str = "<memory>") -> TriFile:
    if len(data) < 8 + 14 * 4:
        raise TriError(f"{source}: file too small ({len(data)} bytes)")
    if data[:8] != MAGIC:
        raise TriError(f"{source}: bad magic {data[:8]!r} (only FRTRI003 is supported)")

    (vc, polytris, polyquads, unk2, unk3, uv_verts, flags,
     num_morphs, num_mods, mod_verts,
     unk7, unk8, unk9, unk10) = struct.unpack_from("<14I", data, 8)

    pos = 8 + 14 * 4

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(data):
            raise TriError(f"{source}: truncated at offset {pos} (needed {n} more bytes)")
        chunk = data[pos:pos + n]
        pos += n
        return chunk

    verts = np.frombuffer(take(vc * 12), dtype="<f4").reshape(vc, 3).copy()
    modverts_raw = take(mod_verts * 12)
    faces = np.frombuffer(take(polytris * 12), dtype="<u4").reshape(polytris, 3).copy()
    quads_raw = take(polyquads * 16)
    uv_raw = take(uv_verts * 8)
    uvfaces_raw = take(polytris * 12)
    uvquads_raw = take(polyquads * 16)

    morphs: list[Morph] = []
    for _ in range(num_morphs):
        (str_len,) = struct.unpack("<I", take(4))
        name_raw = take(str_len)
        (mult,) = struct.unpack("<f", take(4))
        deltas = np.frombuffer(take(vc * 6), dtype="<i2").reshape(vc, 3).copy()
        morphs.append(Morph(name_raw=name_raw, multiplier=mult, deltas_i16=deltas))

    mods_start = pos
    for _ in range(num_mods):
        (str_len,) = struct.unpack("<I", take(4))
        take(str_len)
        (count,) = struct.unpack("<I", take(4))
        take(count * 4)
    mods_raw = data[mods_start:pos]

    if pos != len(data):
        raise TriError(f"{source}: {len(data) - pos} unexplained trailing bytes — refusing")

    return TriFile(
        vertex_count=vc, verts=verts, faces=faces,
        polyquads=polyquads, unk2=unk2, unk3=unk3, uv_verts=uv_verts, flags=flags,
        num_mods=num_mods, mod_verts=mod_verts,
        unk7=unk7, unk8=unk8, unk9=unk9, unk10=unk10,
        modverts_raw=modverts_raw, quads_raw=quads_raw, uv_raw=uv_raw,
        uvfaces_raw=uvfaces_raw, uvquads_raw=uvquads_raw, mods_raw=mods_raw,
        morphs=morphs,
    )


def write_tri(tri: TriFile) -> bytes:
    """Serialize a TriFile. Untouched files round-trip byte-identically."""
    out = bytearray(MAGIC)
    out += struct.pack(
        "<14I",
        tri.vertex_count, len(tri.faces), tri.polyquads, tri.unk2, tri.unk3,
        tri.uv_verts, tri.flags, len(tri.morphs), tri.num_mods, tri.mod_verts,
        tri.unk7, tri.unk8, tri.unk9, tri.unk10,
    )
    out += np.ascontiguousarray(tri.verts, dtype="<f4").tobytes()
    out += tri.modverts_raw
    out += np.ascontiguousarray(tri.faces, dtype="<u4").tobytes()
    out += tri.quads_raw
    out += tri.uv_raw
    out += tri.uvfaces_raw
    out += tri.uvquads_raw
    for m in tri.morphs:
        if m.deltas_i16.shape != (tri.vertex_count, 3):
            raise TriError(
                f"morph {m.name!r}: deltas shape {m.deltas_i16.shape} != "
                f"({tri.vertex_count}, 3)")
        out += struct.pack("<I", len(m.name_raw))
        out += m.name_raw
        out += struct.pack("<f", m.multiplier)
        out += np.ascontiguousarray(m.deltas_i16, dtype="<i2").tobytes()
    out += tri.mods_raw
    return bytes(out)
