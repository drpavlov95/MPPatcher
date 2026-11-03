"""Bake a morph into a TRI's base geometry (scope Tab 2)."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .morphs import find_morph
from .tri import TriFile

AFTER_CHOICES = ("delete", "keep", "negate", "rename")


@dataclass
class BakeOutcome:
    baked: str
    amount: float
    after: str
    renamed_to: str | None = None
    warnings: list[str] = field(default_factory=list)


def bake_morph(tri: TriFile, morph_name: str, *, amount: float = 1.0,
               after: str = "delete", rename_to: str | None = None) -> BakeOutcome:
    if after not in AFTER_CHOICES:
        raise ValueError(f"after must be one of {AFTER_CHOICES}")
    if after == "rename" and not rename_to:
        raise ValueError("rename requires rename_to")

    morph = find_morph(tri, morph_name)
    if morph is None:
        raise KeyError(f"target has no morph named {morph_name!r} "
                       f"(available: {tri.morph_names()})")

    out = BakeOutcome(baked=morph.name, amount=amount, after=after)
    if tri.num_mods > 0:
        out.warnings.append(
            "TRI has modifier records; baked base may desync modifiers")

    new_base = tri.verts.astype(np.float64) + morph.deltas() * amount
    tri.verts = new_base.astype(np.float32)

    if after == "delete":
        tri.morphs.remove(morph)
    elif after == "keep":
        out.warnings.append(
            "kept positive slider after bake — double morphing if applied")
    elif after == "negate":
        neg = np.clip(-morph.deltas_i16.astype(np.int32), -32767, 32767)
        morph.deltas_i16 = neg.astype(np.int16)
    elif after == "rename":
        morph.name_raw = rename_to.encode("ascii") + b"\x00"
        out.renamed_to = rename_to
    return out
