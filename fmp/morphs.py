"""Morph construction, quantization and name matching."""
from __future__ import annotations

from fnmatch import fnmatchcase

import numpy as np

from .tri import Morph, TriFile

I16_MAX = 32767


def quantize_deltas(deltas: np.ndarray) -> tuple[float, np.ndarray]:
    """Float world-space deltas -> (float32 multiplier, int16 array).

    Convention observed in shipped TRIs: multiplier = max_abs / 32767 so the
    largest component uses the full int16 range; all-zero morphs store 0.0.
    """
    deltas = np.asarray(deltas, dtype=np.float64)
    max_abs = float(np.abs(deltas).max()) if deltas.size else 0.0
    if max_abs == 0.0:
        return 0.0, np.zeros(deltas.shape, dtype=np.int16)
    mult = float(np.float32(max_abs / I16_MAX))
    q = np.clip(np.rint(deltas / mult), -I16_MAX, I16_MAX).astype(np.int16)
    return mult, q


def make_morph(name: str, deltas: np.ndarray) -> Morph:
    try:
        raw = name.encode("ascii") + b"\x00"
    except UnicodeEncodeError:
        raise ValueError(f"morph name must be ASCII: {name!r}") from None
    mult, q = quantize_deltas(deltas)
    return Morph(name_raw=raw, multiplier=mult, deltas_i16=q)


def find_morph(tri: TriFile, name: str) -> Morph | None:
    low = name.lower()
    for m in tri.morphs:
        if m.name.lower() == low:
            return m
    return None


def match_morph_names(names: list[str], patterns: list[str]) -> list[str]:
    """Case-insensitive fnmatch selection preserving `names` order, deduped."""
    pats = [p.lower() for p in patterns]
    out = []
    for n in names:
        if n in out:
            continue
        if any(fnmatchcase(n.lower(), p) for p in pats):
            out.append(n)
    return out
