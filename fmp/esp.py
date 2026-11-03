"""Reader for HDPT (head part) records out of a Skyrim plugin.

Hair mods do not put the sex in their file paths. KS Hairdos ships 3096 meshes
named `911.nif`, `abracadabra.nif`, `acerola.nif` - 3065 of them carry no hint at
all - and guessing files a morph set under a key no NPC will ever look up. The
plugin's own HDPT records are the authoritative answer: each one names a mesh, a
sex, a head-part type and whether it is playable at all.

Only HDPT is parsed. This is a way to read the one fact the meshes do not carry,
not a general ESP library.

Record layout, per the long-documented format:

    GRUP     "GRUP", size u32, label 4, type i32, then 12 more bytes  (24 total)
    record   sig 4, dataSize u32, flags u32, formID u32, then 12 more (24 total)
             followed by dataSize bytes of subrecords. When flags has 0x00040000
             the data is zlib-compressed behind a u32 uncompressed size.
    subrec   sig 4, size u16, then size bytes

HDPT subrecords used here: EDID (editor id), MODL (mesh path), DATA (flag byte),
PNAM (head part type).
"""
from __future__ import annotations

import os
import struct
import zlib
from dataclasses import dataclass

RECORD_COMPRESSED = 0x00040000

# HDPT DATA is a single flag byte.
HDPT_PLAYABLE = 0x01
HDPT_MALE = 0x02
HDPT_FEMALE = 0x04
HDPT_EXTRA_PART = 0x08

PART_TYPES = {
    0: "misc", 1: "face", 2: "eyes", 3: "hair",
    4: "facialhair", 5: "scar", 6: "eyebrows",
}


@dataclass
class HeadPart:
    editor_id: str
    model: str                 # as stored, relative to meshes\, backslashes
    part_type: str             # one of PART_TYPES values, or "unknown"
    playable: bool
    male: bool
    female: bool
    extra_part: bool

    @property
    def sex(self) -> str | None:
        """female / male / None when it is declared for both or for neither."""
        if self.female and not self.male:
            return "female"
        if self.male and not self.female:
            return "male"
        return None


def _subrecords(data: bytes):
    pos = 0
    while pos + 6 <= len(data):
        sig = data[pos:pos + 4]
        (size,) = struct.unpack_from("<H", data, pos + 4)
        pos += 6
        yield sig, data[pos:pos + size]
        pos += size


def _parse_hdpt(data: bytes) -> HeadPart:
    edid = model = ""
    flags = 0
    ptype = "unknown"
    for sig, payload in _subrecords(data):
        if sig == b"EDID":
            edid = payload.rstrip(b"\x00").decode("cp1252", "replace")
        elif sig == b"MODL":
            model = payload.rstrip(b"\x00").decode("cp1252", "replace")
        elif sig == b"DATA" and payload:
            flags = payload[0]
        elif sig == b"PNAM" and len(payload) >= 4:
            (t,) = struct.unpack_from("<I", payload, 0)
            ptype = PART_TYPES.get(t, "unknown")
    return HeadPart(
        editor_id=edid, model=model, part_type=ptype,
        playable=bool(flags & HDPT_PLAYABLE),
        male=bool(flags & HDPT_MALE),
        female=bool(flags & HDPT_FEMALE),
        extra_part=bool(flags & HDPT_EXTRA_PART),
    )


def read_head_parts(path: str) -> list[HeadPart]:
    """Every HDPT record in the plugin, in file order."""
    data = open(path, "rb").read()
    out: list[HeadPart] = []
    pos = 0
    n = len(data)

    while pos + 24 <= n:
        sig = data[pos:pos + 4]
        if sig == b"GRUP":
            # Walk into the group: its records follow the 24-byte header, and a
            # group's size counts the header itself.
            (gsize,) = struct.unpack_from("<I", data, pos + 4)
            if gsize < 24:
                break
            pos += 24
            continue

        (dsize, flags) = struct.unpack_from("<II", data, pos + 4)
        body_at = pos + 24
        body = data[body_at:body_at + dsize]
        if flags & RECORD_COMPRESSED and len(body) >= 4:
            try:
                body = zlib.decompress(body[4:])
            except zlib.error:
                body = b""
        if sig == b"HDPT" and body:
            out.append(_parse_hdpt(body))
        pos = body_at + dsize

    return out


def model_key(model: str) -> str:
    """Normalise a MODL path so it can be matched against an extracted tree.

    MODL is stored relative to meshes\\ with backslashes and inconsistent case;
    extracted paths keep the meshes\\ prefix. Both are reduced to lowercase
    forward-slash form with the prefix removed.
    """
    m = model.replace("\\", "/").lower().lstrip("/")
    for prefix in ("meshes/",):
        if m.startswith(prefix):
            m = m[len(prefix):]
    return m


def head_parts_by_model(paths: list[str]) -> dict[str, list[HeadPart]]:
    """Index every HDPT in several plugins by its normalised model path.

    A mesh can be declared more than once - a male and a female record pointing at
    the same file - so the value is a list and the caller decides what that means.
    """
    index: dict[str, list[HeadPart]] = {}
    for p in paths:
        if not os.path.exists(p):
            continue
        for hp in read_head_parts(p):
            if not hp.model:
                continue
            index.setdefault(model_key(hp.model), []).append(hp)
    return index
