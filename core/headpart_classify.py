r"""Which sex and which part type a headpart mesh is for.

A wrong sex is the expensive mistake. MPF indexes its morph library by
``(sex, vertex count)``, so a set filed under the wrong sex is filed under a key
no NPC will ever look up — and nothing reports it. The hair simply never moves.
Everything here therefore prefers "unresolved" to a guess, and says which rule
answered so the caller can show the user where a decision came from.

The plugin is the authority. ``HDPT`` records state each mesh's sex and part
type; KS Hairdos names 3096 meshes ``911.nif`` and ``acerola.nif``, and 3065 of
them carry no hint of sex in the path at all. The path and filename rules below
exist for the meshes no plugin in the source declares.

"female" is tested before "male" everywhere, because it contains it: a substring
search in the other order reads every female asset as male.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

SEX_SOURCES = ("plugin", "path", "filename", "paired", "default", "unresolved")
PART_SOURCES = ("plugin", "default")
DEFAULT_PART = "hair"


@dataclass
class Classification:
    """What was decided about one mesh, and what decided it."""

    sex: str | None
    sex_source: str
    part: str
    part_source: str


def mesh_key(relpath: str) -> str:
    r"""Normalise a path to the form an ``HDPT`` MODL reduces to.

    MODL is stored relative to ``meshes\`` with backslashes and inconsistent
    case; a dumped or extracted tree keeps the prefix and may sit under a
    per-archive folder. Both collapse to the tail after the last "meshes/".
    """
    low = relpath.replace("\\", "/").lower()
    cut = low.rfind("meshes/")
    return low[cut + len("meshes/"):] if cut >= 0 else low


def collect_stems(relpaths) -> frozenset[str]:
    """Lowercased basenames without extension, for the paired-letter rule."""
    return frozenset(
        os.path.splitext(os.path.basename(str(path).replace("\\", "/")))[0].lower()
        for path in relpaths)


def _from_plugin(key: str, hdpt) -> tuple[str | None, str | None]:
    """(sex, part) as the plugin declares them, each None when it does not."""
    records = (hdpt or {}).get(key, [])
    if not records:
        return None, None
    # A mesh declared for both sexes, or by two records that disagree, is a
    # genuine ambiguity. Picking one would file the set under a dead key half
    # the time, so it falls through to the path rules and may end unresolved.
    #
    # A record declared for both sexes counts as a conflict even beside a
    # single-sex one. Dropping it instead — keeping only the records that name
    # one sex — resolved such a mesh at plugin-grade confidence for a decision
    # the plugin never made, and left the other sex with no set at all.
    sexes = {record.sex for record in records}
    sex = sexes.pop() if len(sexes) == 1 and None not in sexes else None

    parts = {record.part_type for record in records
             if record.part_type and record.part_type != "unknown"}
    part = parts.pop() if len(parts) == 1 else None
    return sex, part


def _from_path(relpath: str) -> tuple[str | None, str | None]:
    """(sex, source) from the directory and the filename.

    Vanilla splits hair into ``hair\\female`` and ``hair\\male``, but the
    variants for the non-human races sit in one folder and carry the sex in the
    filename instead — ``femaledarkelfhair01``, and
    ``argonianmalehorns04hairline``, where it is in the middle. Some mods use
    the ``f_``/``m_`` prefix or the ``_f``/``_m`` suffix instead; those count on
    their own, unlike a bare trailing letter, because the separator makes them
    deliberate.

    Every *female* indicator is tested before any male one, across both the
    directory and the filename. Testing the directory first and the filename
    second instead read ``meshes/male/femalehair01.nif`` as male — a dead key —
    where the rule this replaced read it as female. Neither source is
    authoritative when they disagree, so the tie goes to the older answer.
    """
    low = relpath.replace("\\", "/").lower()
    base = os.path.basename(low)
    stem = os.path.splitext(base)[0]

    if "/female/" in low:
        return "female", "path"
    if "female" in base:
        return "female", "filename"
    if stem.startswith("f_") or stem.endswith("_f"):
        return "female", "filename"
    if "/male/" in low:
        return "male", "path"
    if "male" in base:
        return "male", "filename"
    if stem.startswith("m_") or stem.endswith("_m"):
        return "male", "filename"
    return None, None


def _from_pair(relpath: str, stems: frozenset[str]) -> str | None:
    """Vanilla's trailing-letter pair: HairShortHumanF against ...M.

    The letter counts only when the opposite file actually exists. On its own
    the last letter of a name is no evidence at all — plenty of meshes end in f
    or m by accident — and guessing files the set under a dead key.

    One character is stripped rather than a separator matched, so the same rule
    covers ``HairShortHumanF``, ``karlach_hair_f`` and BG3's space-separated
    ``gortash_hair F`` without three spellings of the test.
    """
    stem = os.path.splitext(os.path.basename(relpath.replace("\\", "/")))[0].lower()
    if not stem:
        return None
    if stem.endswith("f") and (stem[:-1] + "m") in stems:
        return "female"
    if stem.endswith("m") and (stem[:-1] + "f") in stems:
        return "male"
    return None


def classify(relpath: str, *, hdpt=None, stems=frozenset(),
             default_sex: str | None = None,
             default_part: str = DEFAULT_PART) -> Classification:
    """Resolve one mesh, preferring the plugin and reporting what decided."""
    key = mesh_key(relpath)
    declared_sex, declared_part = _from_plugin(key, hdpt)

    part = declared_part or default_part
    part_source = "plugin" if declared_part else "default"

    if declared_sex:
        return Classification(declared_sex, "plugin", part, part_source)

    sex, source = _from_path(relpath)
    if sex:
        return Classification(sex, source, part, part_source)

    paired = _from_pair(relpath, stems)
    if paired:
        return Classification(paired, "paired", part, part_source)

    if default_sex:
        return Classification(default_sex, "default", part, part_source)
    return Classification(None, "unresolved", part, part_source)
