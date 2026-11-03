"""Reader for RaceMenu's body morph files - the TRIP/TRI\\0 pair, not FaceGen's.

BodySlide writes two unrelated things with a .tri extension. The head files are
FaceGen's FRTRI003, which fmp.tri handles. The body files are RaceMenu's own
format, and the one the game actually applies to a morphed body: a shape table,
each shape holding named morphs, each morph a sparse list of displaced vertices.

Both spellings are supported because the header is an MSVC multi-character
literal ('TRIP' / 'TRI\\0') and so lands in the file byte-reversed - "PIRT" for
the packed form, "\\0IRT" for the unpacked one. Packed stores a per-morph float
multiplier with 16-bit indices and deltas; unpacked stores block sizes with
32-bit counts and float deltas.

The layout follows rfm::BodyTriIndex::Load in src/bodytri.cpp, which indexes
these files for the plugin; this reads the deltas it skips.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np


class TripError(Exception):
    pass


@dataclass
class TripMorph:
    name: str
    multiplier: float
    indices: np.ndarray            # uint32, the vertices this morph displaces
    deltas: np.ndarray             # float64 (n, 3), already scaled

    def dense(self, vertex_count: int) -> np.ndarray:
        """The morph as a full per-vertex displacement array."""
        out = np.zeros((vertex_count, 3), dtype=np.float64)
        keep = self.indices < vertex_count
        out[self.indices[keep]] = self.deltas[keep]
        return out


@dataclass
class TripShape:
    name: str
    morphs: dict[str, TripMorph] = field(default_factory=dict)


def read_trip(data: bytes, *, source: str = "<memory>") -> dict[str, TripShape]:
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(data):
            raise TripError(f"{source}: truncated at byte {pos}")
        out = data[pos:pos + n]
        pos += n
        return out

    def u8() -> int:
        return take(1)[0]

    def u16() -> int:
        return struct.unpack("<H", take(2))[0]

    def u32() -> int:
        return struct.unpack("<I", take(4))[0]

    def f32() -> float:
        return struct.unpack("<f", take(4))[0]

    def name() -> str:
        return take(u8()).decode("utf-8", "replace")

    magic = take(4)
    if magic == b"PIRT":
        packed = True
    elif magic == b"\x00IRT":
        packed = False
    else:
        raise TripError(f"{source}: bad magic {magic!r} (expected PIRT or \\0IRT)")

    shape_count = u16() if packed else u32()
    shapes: dict[str, TripShape] = {}
    for _ in range(shape_count):
        shape = TripShape(name())
        if not packed:
            u32()                                  # block size, not needed
        morph_count = u16() if packed else u32()
        for _ in range(morph_count):
            mname = name()
            if not mname:
                raise TripError(f"{source}: shape {shape.name!r} has an empty morph name")
            if packed:
                multiplier = f32()
                count = u16()
                raw = np.frombuffer(take(count * 8), dtype="<i2").reshape(count, 4)
                # the index occupies the first 16-bit slot and is unsigned
                idx = raw[:, 0].astype(np.uint16).astype(np.uint32)
                deltas = raw[:, 1:].astype(np.float64) * multiplier
            else:
                u32()                              # block size
                count = u32()
                multiplier = 1.0
                rec = np.frombuffer(take(count * 16), dtype=np.uint8).reshape(count, 16)
                idx = rec[:, :4].copy().view("<u4").reshape(count)
                deltas = rec[:, 4:].copy().view("<f4").reshape(count, 3).astype(np.float64)
            shape.morphs[mname] = TripMorph(mname, multiplier, idx, deltas)
        shapes[shape.name] = shape

    # BodySlide appends a second shape table after the first, with no magic of
    # its own - the files here carry one for "Belly Button Match". The counted
    # tables are what the morphs live in, so trailing data is reported rather
    # than refused: raising would reject every real body file.
    if pos != len(data):
        read_trip.trailing = len(data) - pos      # type: ignore[attr-defined]
    return shapes


def read_trip_file(path: str) -> dict[str, TripShape]:
    with open(path, "rb") as fh:
        return read_trip(fh.read(), source=path)
