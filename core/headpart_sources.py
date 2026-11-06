r"""What a headpart source tree holds: NIFs, archives and plugins.

Three layouts all have to work. A mod with loose NIFs under ``meshes\``; a mod
that ships nothing loose at all, which is common enough that Beards of Power's
entire 177 shapes live inside one 911 MB BSA; and a ZIP, which the caller stages
through :class:`core.source_staging.StagedSource` before getting here.

Plugins are discovered rather than asked for, because the facts this pipeline
needs about a headpart — its sex and its part type — are ``HDPT`` record fields,
not filename conventions. KS Hairdos names 3096 meshes ``911.nif`` and
``acerola.nif``; 3065 of them carry no hint of sex at all.

Everything here walks directories rather than stat'ing paths: MO2's usvfs hooks
``FindFirstFileW`` but not ``GetFileAttributesW``, so a file that is really there
answers "no" to :func:`os.path.isfile` on a virtual path.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

NIF_SUFFIX = ".nif"
ARCHIVE_SUFFIX = ".bsa"
PLUGIN_SUFFIXES = (".esp", ".esm", ".esl")

# A previous run's output, and the mod's own backups, are not inputs. The output
# of library mode lands in SKSE/Plugins/MPF/morphs, so including it would let a
# generated tree become the next run's source.
_EXCLUDED_FRAGMENTS = ("/backup", "/mpf/morphs")


@dataclass
class HeadpartSource:
    """Everything discovered under one staged source root."""

    root: str
    loose_nifs: list[str] = field(default_factory=list)   # relative to root
    archives: list[str] = field(default_factory=list)     # absolute
    plugins: list[str] = field(default_factory=list)      # absolute


def _is_excluded(relpath: str) -> bool:
    low = "/" + relpath.replace("\\", "/").lower()
    return any(fragment in low for fragment in _EXCLUDED_FRAGMENTS)


def discover(root: str) -> HeadpartSource:
    """Collect the NIFs, BSAs and plugins under *root*.

    A root that cannot be walked is an empty answer rather than an error: the
    caller has a staged path that may not exist yet, and reporting "nothing
    here" lets it say so in its own words.
    """
    found = HeadpartSource(root=str(root))
    for current, _dirs, files in os.walk(root):
        for name in sorted(files):
            full = os.path.join(current, name)
            relative = os.path.relpath(full, root)
            if _is_excluded(relative):
                continue
            low = name.lower()
            if low.endswith(NIF_SUFFIX):
                found.loose_nifs.append(relative)
            elif low.endswith(ARCHIVE_SUFFIX):
                found.archives.append(full)
            elif low.endswith(PLUGIN_SUFFIXES):
                found.plugins.append(full)
    return found


CONFIDENCE = {
    "plugin": "High",       # the game's own record said so
    "path": "Medium",
    "filename": "Medium",
    "paired": "Medium",
    "default": "Low",       # the operator stated it for the whole mod
    "unresolved": "Unknown",
}


@dataclass
class HeadpartRow:
    """One NIF as the GUI shows it, before any geometry is read."""

    relpath: str
    sex: str | None
    sex_source: str
    part: str
    confidence: str
    warnings: list[str] = field(default_factory=list)


def scan_rows(root: str, *, default_part: str = "hair",
              default_sex: str | None = None, hdpt=None) -> list[HeadpartRow]:
    """What the GUI lists for a headpart source: one row per NIF.

    Deliberately file-based. A vertex count would mean dumping every mesh in
    the mod, which is the expensive half of a run — Beards of Power spends
    minutes pulling 182 meshes out of a 911 MB archive — and the scan would pay
    it again on every rescan. Shape-level detail belongs in the run report.

    Each row says which rule resolved its sex, so a row the patcher will skip
    is visible before the run instead of appearing as a skip in the log.
    """
    from core.headpart_classify import classify, collect_stems

    found = discover(root)
    if hdpt is None and found.plugins:
        from fmp.esp import head_parts_by_model
        hdpt = head_parts_by_model(found.plugins)

    stems = collect_stems(found.loose_nifs)
    rows = []
    for relpath in found.loose_nifs:
        decided = classify(relpath, hdpt=hdpt, stems=stems,
                           default_sex=default_sex, default_part=default_part)
        warnings = []
        if decided.sex is None:
            warnings.append(
                "Sex could not be determined from the plugin, the path or the "
                "filename; this headpart will be skipped unless it is shipped "
                "for both sexes.")
        rows.append(HeadpartRow(
            relpath=relpath, sex=decided.sex, sex_source=decided.sex_source,
            part=decided.part,
            confidence=CONFIDENCE.get(decided.sex_source, "Unknown"),
            warnings=warnings))
    return rows


def _staging_name(archive: str, used: set[str]) -> str:
    """A folder name per archive, kept distinct when two share a stem."""
    stem = os.path.splitext(os.path.basename(archive))[0] or "archive"
    candidate, n = stem, 1
    while candidate.lower() in used:
        n += 1
        candidate = f"{stem}_{n}"
    used.add(candidate.lower())
    return candidate


def stage_archive_nifs(archives: list[str], work_dir: str, *,
                       extract=None) -> tuple[list[str], list[str]]:
    """Extract the NIFs out of each archive into its own folder under *work_dir*.

    One folder per archive, not one shared tree. If the same hair exists in two
    archives with different geometry, covering both beats guessing which one the
    load order picks — the geometry clustering downstream merges them when they
    are in fact identical.

    An archive that cannot be opened costs that archive, not the run: a mod that
    ships one unreadable BSA beside three good ones should still get its three.
    """
    if extract is None:
        from fmp.bsa import extract as extract      # noqa: PLW0127

    staged: list[str] = []
    failures: list[str] = []
    used: set[str] = set()
    for archive in archives:
        out_root = os.path.join(work_dir, _staging_name(archive, used))
        try:
            written, entry_failures = extract([archive], out_root,
                                              suffix=NIF_SUFFIX)
        except Exception as exc:                            # noqa: BLE001
            failures.append(f"{os.path.basename(archive)}: {exc}")
            continue
        failures.extend(entry_failures)
        if written:
            staged.append(out_root)
    return staged, failures
