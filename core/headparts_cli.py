r"""The `--headparts` command, in two modes.

**library** (the default) reads the mod's NIFs and writes an MPF morph library —
``SKSE/Plugins/MPF/morphs`` holding one TRI per distinct geometry plus a
``[[sets]]`` manifest. This is what the plugin reads under its default
``Backend=shared``, and the only artifact that can serve a mod shipping no
``.tri`` at all.

**tri** transfers the morphs into the ``.tri`` files the mod already ships. That
is the artifact ``Backend=racemenu`` needs, the documented compatibility
fallback for old asset installations, and it can only reach headparts that
already have a chargen TRI.

Output protocol is one ``[STATUS ] relpath`` line per shape followed by a
``Headparts: ...`` tally, because the GUI's runner parses those lines to drive
its progress bar.
"""
from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

from core.headpart_classify import classify, collect_stems
from core.headpart_library import (
    DEFAULT_CLUSTER_LIMIT, DEFAULT_PRESET, LibraryOptions, ShapeInput,
    build_library, load_head_reference, sanitize_tag,
)
from core.headpart_patcher import discover_references, patch_headparts
from core.headpart_sources import discover, stage_archive_nifs
from core.nif_geometry import NifdumpMissing, dump_tree, find_nifdump, read_geometries
from core.source_staging import StagedSource, find_tri_root

PART_CHOICES = ("misc", "face", "eyes", "hair", "facialhair", "scar",
                "eyebrows")


ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".bsa", ".ba2")


def default_tag(source: str) -> str:
    """A filename-safe tag from the source folder or archive name.

    Every content mod shares one morphs folder, so a tag that collides puts two
    mods' sets in each other's way. The mod's own name is the best default
    available without asking.

    Only a known archive suffix is stripped, never any trailing dot segment:
    mod folders carry version numbers, and ``os.path.splitext`` reads
    "BG3 hairs 1.1" as a file named "BG3 hairs 1" with extension ".1".
    """
    name = os.path.basename(str(source).rstrip("\\/"))
    low = name.lower()
    for suffix in ARCHIVE_SUFFIXES:
        if low.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return sanitize_tag(name)


def build_headparts_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="MPPatcher --headparts",
        description="Generate Physiques of Skyrim morphs for a headpart mod")
    ap.add_argument("--headparts", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--mode", choices=("library", "tri"), default="library",
                    help="library: read the mod's NIFs and write an MPF morph "
                         "library (default). tri: transfer into the .tri files "
                         "the mod already ships, for Backend=racemenu.")
    ap.add_argument("--source", required=True,
                    help="mod folder or .zip holding the headparts")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--female-ref", help="FACE_FemaleHead.tri to sample from")
    ap.add_argument("--male-ref", help="FACE_MaleHead.tri to sample from")
    ap.add_argument("--filter-file",
                    help="newline-separated relative paths to limit the run to")
    ap.add_argument("--dry-run", action="store_true",
                    help="measure and report without writing anything")

    library = ap.add_argument_group("library mode")
    library.add_argument("--tag", help="short mod tag used in filenames; "
                                      "defaults to the source folder's name")
    library.add_argument("--part", choices=PART_CHOICES, default="hair",
                         help="part type for meshes no plugin declares")
    library.add_argument("--default-sex", choices=("female", "male"),
                         help="sex for shapes nothing else resolves. Only for "
                              "a mod that is wholly one sex — a wrong sex "
                              "files the set under a key nothing looks up.")
    library.add_argument("--unclassified-both", action="store_true",
                         help="ship shapes whose sex cannot be determined for "
                              "both sexes instead of skipping them")
    library.add_argument("--cluster-limit", type=float,
                         default=DEFAULT_CLUSTER_LIMIT,
                         help="two shapes are one geometry within this "
                              "CentredMeanDistance (default %(default)s, "
                              "deliberately tighter than the plugin's 3.0)")
    library.add_argument("--falloff", type=float,
                         default=DEFAULT_PRESET.falloff,
                         help="delta reaches zero this far from the head; "
                              "0 disables the falloff (default %(default)s)")
    library.add_argument("--falloff-inner", type=float,
                         default=DEFAULT_PRESET.falloff_inner,
                         help="full delta up to this distance "
                              "(default %(default)s)")
    library.add_argument("--smooth", type=int, default=0,
                         help="Laplacian smoothing iterations over the target")
    library.add_argument("--nifdump",
                         help="path to nifdump.exe; normally found beside "
                              "MPPatcher.exe or via MPP_NIFDUMP")

    tri = ap.add_argument_group("tri mode")
    tri.add_argument("--only-missing", action="store_true",
                     help="skip TRIs that already carry every morph")
    return ap


def _selected_paths(filter_file: str | None) -> set[str] | None:
    if not filter_file:
        return None
    text = Path(filter_file).read_text(encoding="utf-8")
    return {line.strip().replace("\\", "/")
            for line in text.splitlines() if line.strip()}


def _is_selected(selected: set[str] | None, relpath: str, shape: str) -> bool:
    """Whether this shape was asked for.

    The GUI checks rows, and in library mode a row is one *shape* — a NIF can
    hold several — so its filter carries ``relpath#shape``. TRI mode's filter
    carries bare paths. Both spellings select, so one filter file works for
    either mode and selecting a NIF selects every shape in it.
    """
    if selected is None:
        return True
    key = relpath.replace("\\", "/")
    return key in selected or f"{key}#{shape}" in selected


def _report_lines(report) -> None:
    for item in report.files:
        detail = ""
        if item.error:
            detail = f" - {item.error}"
        elif item.warnings:
            detail = f" - {'; '.join(item.warnings)}"
        print(f"[{item.status.upper():7}] {item.relpath}{detail}")


def _tally(report) -> dict[str, int]:
    counts = report.counts()
    print("Headparts: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
    return counts


def _run_library(args) -> int:
    exe = args.nifdump or find_nifdump(os.path.dirname(os.path.abspath(__file__)))
    if not exe:
        print("[ERROR] nifdump was not found. Headpart geometry can only be "
              "read from the mod's NIFs, and reading the .tri files a mod "
              "ships instead would build morph sets that match no live mesh "
              "and report success.")
        print("        Put nifdump.exe beside MPPatcher.exe, pass --nifdump, "
              "or set MPP_NIFDUMP.")
        return 2

    kind = "zip" if Path(args.source).suffix.lower() == ".zip" else "folder"
    with StagedSource(args.source, kind) as staged:
        refs = (args.female_ref, args.male_ref)
        if not all(refs):
            refs = discover_references(str(staged.root)) or discover_references(
                os.path.dirname(os.path.abspath(__file__)))
        if not refs:
            print("[ERROR] Could not find Physiques of Skyrim "
                  "FACE_FemaleHead.tri and FACE_MaleHead.tri in the active "
                  "Data/VFS tree. Pass --female-ref and --male-ref to name "
                  "them explicitly.")
            return 2

        try:
            heads = {"female": load_head_reference(refs[0], "female"),
                     "male": load_head_reference(refs[1], "male")}
        except (OSError, ValueError) as exc:
            print(f"[ERROR] {exc}")
            return 2

        with tempfile.TemporaryDirectory(prefix="mppatcher_headparts_") as work:
            source = discover(str(staged.root))
            roots = [str(staged.root)]
            if source.archives:
                staged_roots, failures = stage_archive_nifs(
                    source.archives, os.path.join(work, "bsa"))
                roots.extend(staged_roots)
                for failure in failures:
                    print(f"[WARN   ] {failure}")
                print(f"Archives: {len(source.archives)} opened, "
                      f"{len(staged_roots)} yielded NIFs")

            geometries, dump_failures, empty = [], [], 0
            for index, root in enumerate(roots):
                try:
                    run = dump_tree(exe, root, os.path.join(work, f"d{index}"))
                except NifdumpMissing as exc:
                    print(f"[ERROR] {exc}")
                    return 2
                geometries.extend(read_geometries(run))
                dump_failures.extend(run.failures)
                empty += len(run.empty)
            for failure in dump_failures:
                print(f"[WARN   ] {failure}")

            if not geometries:
                print("[ERROR] No NIF geometry was found in the source. "
                      "Headparts are patched from their meshes, so there is "
                      "nothing to do.")
                return 2
            print(f"Geometry: {len(geometries)} shapes from "
                  f"{len(source.loose_nifs) + len(source.archives)} inputs"
                  + (f", {empty} NIFs with no usable geometry" if empty else ""))

            plugins = source.plugins
            hdpt = None
            if plugins:
                from fmp.esp import head_parts_by_model
                hdpt = head_parts_by_model(plugins)
                print(f"Plugins:  {len(plugins)} read, "
                      f"{sum(len(v) for v in hdpt.values())} HDPT records "
                      f"covering {len(hdpt)} meshes")
            else:
                print("Plugins:  none found; sex falls back to the path and "
                      "filename rules")

            stems = collect_stems(g.relpath for g in geometries)
            selected = _selected_paths(args.filter_file)
            shapes = []
            for geometry in geometries:
                if not _is_selected(selected, geometry.relpath, geometry.shape):
                    continue
                found = classify(geometry.relpath, hdpt=hdpt, stems=stems,
                                 default_sex=args.default_sex,
                                 default_part=args.part)
                shapes.append(ShapeInput(
                    relpath=geometry.relpath, shape=geometry.shape,
                    sex=found.sex, sex_source=found.sex_source,
                    part=found.part, verts=geometry.verts,
                    tris=geometry.tris))

            options = LibraryOptions(
                tag=args.tag or default_tag(args.source),
                default_part=args.part, default_sex=args.default_sex,
                unclassified_both=args.unclassified_both,
                cluster_limit=args.cluster_limit,
                falloff=(args.falloff if args.falloff > 0 else None),
                falloff_inner=args.falloff_inner, smooth=args.smooth,
                dry_run=args.dry_run)
            report, summary = build_library(shapes, heads, args.output_dir,
                                            options)

    _report_lines(report)
    print(f"Sets:     {summary.sets} written"
          + ("  (dry run, nothing saved)" if args.dry_run else "")
          + f", {summary.bytes_written / 1e6:.1f} MB")
    print(f"Verified: {summary.verified} of "
          f"{summary.verified + len(summary.unmatched)} shipped shapes match "
          "a set the plugin would find")
    if summary.unmatched:
        print("[ERROR] these shapes match no set in the library, so the "
              "plugin would apply nothing to them: "
              + ", ".join(summary.unmatched[:10]))
    print(f"Frames:   {summary.frames or '{}'}")
    print(f"Distance: headpart-to-head p50, median {summary.median_wrap_p50:.2f}, "
          f"worst {summary.worst_wrap_p50:.2f}")
    if summary.median_wrap_p50 > 15.0:
        print("[WARN   ] that is far too large — the reference head is almost "
              "certainly in the wrong frame and the morphs are nonsense")
    for reason, count in sorted(summary.skipped.items()):
        print(f"Skipped:  {count} x {reason}")
    if summary.manifest:
        print(f"Manifest: {summary.manifest}")

    counts = _tally(report)
    return 1 if (counts.get("failed") or summary.unmatched) else 0


def _run_tri(args) -> int:
    kind = "zip" if Path(args.source).suffix.lower() == ".zip" else "folder"
    with StagedSource(args.source, kind) as staged:
        refs = (args.female_ref, args.male_ref)
        if not all(refs):
            refs = discover_references(str(staged.root))
        if not refs:
            print("[ERROR] Could not find Physiques of Skyrim "
                  "FACE_FemaleHead.tri and FACE_MaleHead.tri.")
            return 2
        root = find_tri_root(staged.root)
        report = patch_headparts(str(root), args.output_dir, refs[0], refs[1],
                                 only_missing=args.only_missing,
                                 selected_paths=_selected_paths(args.filter_file))
    _report_lines(report)
    counts = _tally(report)
    return 1 if counts.get("failed") else 0


def run_headparts(argv: list[str]) -> int:
    args = build_headparts_parser().parse_args(argv)
    return _run_tri(args) if args.mode == "tri" else _run_library(args)
