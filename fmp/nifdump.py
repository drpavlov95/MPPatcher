"""Reader for the flat geometry dumps produced by mppatcher's `nifdump`.

Hair mods routinely ship real geometry in the NIF and a stub chargen TRI beside it,
so the TRI is not a usable source of vertices. `nifdump` walks a mod and writes one
binary per NIF; this reads them back.

Format (see nifdump.cpp for the authoritative definition):

    magic   "NIFD0002"
    u32     shapeCount
    per shape:
      u16   nameLen, then that many bytes of name
      u32   vertexCount
      u32   triangleCount
      f32   vertices[vertexCount * 3]
      u32   triangles[triangleCount * 3]
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

MAGIC = b"NIFD0002"


class NifDumpError(Exception):
    """File is not a valid/complete nifdump binary."""


@dataclass
class DumpedShape:
    name: str
    verts: np.ndarray      # (vc, 3) float32
    tris: np.ndarray       # (tc, 3) uint32


def read_dump(data: bytes, *, source: str = "<memory>") -> list[DumpedShape]:
    if len(data) < 12:
        raise NifDumpError(f"{source}: too small ({len(data)} bytes)")
    if data[:8] != MAGIC:
        raise NifDumpError(f"{source}: bad magic {data[:8]!r}")

    (count,) = struct.unpack_from("<I", data, 8)
    pos = 12
    out: list[DumpedShape] = []

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(data):
            raise NifDumpError(f"{source}: truncated at {pos} (needed {n} more)")
        chunk = data[pos:pos + n]
        pos += n
        return chunk

    for _ in range(count):
        (name_len,) = struct.unpack("<H", take(2))
        name = take(name_len).decode("utf-8", errors="replace")
        vc, tc = struct.unpack("<II", take(8))
        verts = np.frombuffer(take(vc * 12), dtype="<f4").reshape(vc, 3).copy()
        tris = np.frombuffer(take(tc * 12), dtype="<u4").reshape(tc, 3).copy()
        out.append(DumpedShape(name=name, verts=verts, tris=tris))

    if pos != len(data):
        raise NifDumpError(f"{source}: {len(data) - pos} unexplained trailing bytes")
    return out


def read_dump_file(path: str) -> list[DumpedShape]:
    with open(path, "rb") as fh:
        return read_dump(fh.read(), source=path)
