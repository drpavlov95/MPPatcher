"""Morph transfer from a reference TRI onto a target TRI."""
from __future__ import annotations

from dataclasses import dataclass, field

from .mapping import build_mapping
from .morphs import find_morph, make_morph
from .tri import TriFile


@dataclass
class TransferOutcome:
    method: str
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    skipped_same_name: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)


def transfer_morphs(ref: TriFile, tgt: TriFile, morph_names: list[str], *,
                    mode: str = "merge", replace_same_name: bool = True,
                    method: str = "auto") -> TransferOutcome:
    if mode not in ("merge", "overwrite"):
        raise ValueError(f"unknown mode: {mode}")

    src_morphs = []
    for name in morph_names:
        m = find_morph(ref, name)
        if m is None:
            raise KeyError(f"reference has no morph named {name!r} "
                           f"(available: {ref.morph_names()})")
        src_morphs.append(m)

    map_method, idx, metrics = build_mapping(ref, tgt)
    if method != "auto":
        if method == "identity" and map_method != "identity":
            raise ValueError("identity mapping requested but topologies differ")
        map_method = method if method != "identity" else map_method
        if method == "nearest" and idx is None:
            from .mapping import nearest_vertex_indices
            idx = nearest_vertex_indices(ref.verts, tgt.verts)
            map_method = "nearest"

    out = TransferOutcome(method=map_method, metrics=metrics)

    if mode == "overwrite":
        out.removed = tgt.morph_names()
        tgt.morphs = []

    for src in src_morphs:
        if map_method == "identity":
            new = make_morph(src.name, src.deltas())
            # exact copy: reuse original quantization so nothing degrades
            new.deltas_i16 = src.deltas_i16.copy()
            new.multiplier = src.multiplier
        else:
            new = make_morph(src.name, src.deltas()[idx])

        existing = find_morph(tgt, src.name)
        if existing is None:
            tgt.morphs.append(new)
            out.added.append(src.name)
        elif replace_same_name or mode == "overwrite":
            tgt.morphs[tgt.morphs.index(existing)] = new
            out.replaced.append(src.name)
        else:
            out.skipped_same_name.append(src.name)
    return out
