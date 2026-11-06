"""Headpart TRI morph transfer used by MPPatcher's Headparts target.

The reference files are the three-morph ``FACE_FemaleHead.tri`` and
``FACE_MaleHead.tri`` shipped by Physiques of Skyrim.  This path intentionally
operates on TRI files and preserves the input tree; generating new geometry from
NIFs is a separate FaceMorphsPatcher authoring workflow.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.headpart_classify import classify, collect_stems
from fmp.morphs import find_morph
from fmp.transfer import transfer_morphs
from fmp.pipeline import BatchConfig, run_batch
from fmp.report import RunReport
from fmp.tri import read_tri


MORPHS = ("RLY_Heavy", "RLY_Muscular", "RLY_Thin")


def discover_references(data_root: str | os.PathLike[str],
                        mods_root: str | os.PathLike[str] | None = None) -> tuple[str, str] | None:
    """Find the current Physiques of Skyrim head references in a data tree."""
    base = Path(data_root)
    candidates = [base / "SKSE" / "Plugins" / "MPF" / "morphs"]
    # Development/MO2 layouts may pass BodySlide's directory instead of Data.
    candidates += [base.parent.parent / "SKSE" / "Plugins" / "MPF" / "morphs"]
    if mods_root:
        mods = Path(mods_root)
        candidates.append(mods / "Physiques of Skyrim" / "SKSE" / "Plugins" / "MPF" / "morphs")
    for folder in candidates:
        female = folder / "FACE_FemaleHead.tri"
        male = folder / "FACE_MaleHead.tri"
        try:
            # Enumeration works through MO2's USVFS, unlike Path.is_file on
            # some virtual paths.
            names = {name.lower() for name in os.listdir(folder)}
        except OSError:
            continue
        if female.name.lower() in names and male.name.lower() in names:
            return str(female), str(male)
    return None


def scan_headparts(root: str | os.PathLike[str]) -> list[Path]:
    """Return TRI targets, excluding MPF reference/backup folders."""
    base = Path(root)
    out = []
    # Match the suffix explicitly so .TRI files are found consistently on
    # case-sensitive source folders as well as through MO2's VFS.
    for current, _dirs, files in os.walk(base):
        for name in files:
            if not name.lower().endswith(".tri"):
                continue
            p = Path(current) / name
            low = str(p).lower().replace("\\", "/")
            if "/backup" in low or "/backup_morphs" in low or "/mpf/morphs" in low:
                continue
            out.append(p)
    return sorted(out)


def detect_headpart_sex(path: Path | str, *,
                        stems: frozenset[str] = frozenset()) -> str:
    """Return ``female``, ``male`` or ``unknown`` without substring traps.

    Delegates to :func:`core.headpart_classify.classify`, which strips one
    trailing character rather than matching a separator — so BG3's
    ``gortash_hair F.tri`` pairs with ``gortash_hair M.tri`` the same way
    vanilla's ``HairShortHumanF`` pairs with ``...M``. The old rule tested for
    ``_f``/``f_`` and read 28 of that mod's 38 files as unknown.

    *stems* is every other name in the same scan, which the pairing rule needs:
    a trailing letter is only evidence when the opposite file exists. Called
    without it, the function stays as conservative as it always was.
    """
    text = str(path).lower().replace("\\", "/")
    # Beards carry no sex in the name but are male by nature. classify has no
    # such rule — it is specific to this legacy TRI path — so it is checked
    # here, after the explicit female tests inside classify cannot be fooled.
    found = classify(str(path), stems=stems)
    if found.sex:
        return found.sex
    if "beard" in Path(text).stem or "/beards/" in text:
        return "male"
    return "unknown"


def _is_male(path: Path) -> bool:
    """Compatibility wrapper for callers that only need a boolean."""
    return detect_headpart_sex(path) == "male"


def patch_headparts(source_root: str, output_dir: str, female_ref: str,
                    male_ref: str, *, dry_run: bool = False,
                    only_missing: bool = False,
                    selected_paths: set[str] | None = None) -> RunReport:
    """Transfer Physiques morphs to every headpart TRI under *source_root*."""
    source = Path(source_root)
    targets = scan_headparts(source)
    if selected_paths is not None:
        targets = [p for p in targets
                   if str(p.relative_to(source)).replace("\\", "/") in selected_paths]
    refs = {"female": read_tri(Path(female_ref).read_bytes(), source=female_ref),
            "male": read_tri(Path(male_ref).read_bytes(), source=male_ref)}
    selected = list(MORPHS)
    for sex, tri in refs.items():
        absent = [name for name in selected if find_morph(tri, name) is None]
        if absent:
            raise ValueError(f"{sex} reference is missing {', '.join(absent)}")

    report = RunReport(tool="headparts", reference=f"{female_ref}; {male_ref}",
                       selected_morphs=selected, targets=[str(source)],
                       output_dir=str(output_dir), mode="merge")
    cfg = BatchConfig(targets=[source], output_dir=Path(output_dir),
                      dry_run=dry_run, include=["*.tri"], recursive=True)

    # The trailing-letter pairing rule needs to know what else the scan holds:
    # "gortash_hair F.tri" is only female because "gortash_hair M.tri" is there
    # too. Collected from every target, not just the selected ones, so filtering
    # the run down to one file does not change how its name reads.
    stems = collect_stems(scan_headparts(source))

    def process(tri, result):
            sex = detect_headpart_sex(Path(result.path), stems=stems)
            if sex == "unknown":
                result.warnings.append(
                    "Could not determine headpart sex from the path; skipped for safety.")
                return None
            ref = refs[sex]
            if only_missing and all(find_morph(tri, n) is not None for n in selected):
                result.warnings.append("already has all Physiques morphs")
                return None
            outcome = transfer_morphs(ref, tri, selected, mode="merge",
                                     replace_same_name=True, method="auto")
            result.method = outcome.method
            result.added = outcome.added
            result.replaced = outcome.replaced
            result.metrics.update(outcome.metrics)
            return tri
    run_batch(cfg, process, report)
    return report
