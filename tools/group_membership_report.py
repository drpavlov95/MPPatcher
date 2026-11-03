"""Development-only BodySlide group membership diagnostics.

The normal UI reads one effective ``SliderGroups`` directory.  This helper
can do the same directly, or reconstruct an MO2 profile's visible files from
its enabled mod list.  It never infers groups from OSP names or scanner
metadata.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.slider_group_reader import SliderGroupDefinition, read_definitions


def _enabled_mods(modlist: str) -> list[str]:
    with open(modlist, encoding="utf-8", errors="replace") as handle:
        return [line[1:].rstrip("\r\n") for line in handle if line.startswith("+")]


def _effective_files(mods_root: str, modlist: str, subfolder: str,
                     suffixes: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return one physical source per visible relative filename.

    MO2's later enabled priority wins when two mods provide the same relative
    file.  The report keeps that selected source instead of unioning hidden
    lower-priority files into the virtual directory.
    """
    selected: dict[str, tuple[str, str]] = {}
    for mod in _enabled_mods(modlist):
        folder = os.path.join(mods_root, mod, subfolder)
        try:
            for current, _dirs, names in os.walk(folder):
                for name in names:
                    if not name.lower().endswith(suffixes):
                        continue
                    path = os.path.join(current, name)
                    relative = os.path.relpath(path, folder)
                    # MO2's priority collision key is the virtual relative
                    # path, not merely the basename.
                    selected[relative.casefold()] = (relative, path)
        except OSError:
            continue
    return [selected[key] for key in sorted(selected)]


def _definitions_from_profile(mods_root: str, modlist: str):
    definitions: list[SliderGroupDefinition] = []
    group_dir = os.path.join("CalienteTools", "BodySlide", "SliderGroups")
    for _name, path in _effective_files(mods_root, modlist, group_dir, (".xml",)):
        try:
            root = ET.parse(path).getroot()
        except (ET.ParseError, OSError, UnicodeError):
            continue
        for group_elem in root.findall("Group"):
            name = (group_elem.get("name") or "").strip()
            if not name:
                continue
            members = [
                (member.get("name") or "").strip()
                for member in group_elem.findall("Member")
                if (member.get("name") or "").strip()
            ]
            definitions.append(SliderGroupDefinition(name, path, members))
    return definitions


def _parse_slider_names(path: str) -> set[str]:
    names: set[str] = set()
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeError):
        return names
    for slider_set in root.findall(".//SliderSet"):
        name = (slider_set.get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _source_label(path: str) -> str:
    # Keep the complete physical source in the CSV so two mods with the same
    # XML filename cannot become indistinguishable.  Console summaries use a
    # shorter basename below.
    return os.path.normpath(path)


def _source_display(path: str) -> str:
    return os.path.basename(path)


def _osp_matches_from_profile(mods_root: str, modlist: str):
    matches: dict[str, set[str]] = defaultdict(set)
    for relative, path in _effective_files(
            mods_root, modlist,
            os.path.join("CalienteTools", "BodySlide", "SliderSets"),
            (".osp", ".xml")):
        for name in _parse_slider_names(path):
            matches[name].add(relative)
    return matches


def _osp_matches_from_folder(folder: str):
    matches: dict[str, set[str]] = defaultdict(set)
    try:
        paths = []
        for current, _dirs, files in os.walk(folder):
            paths.extend(os.path.join(current, name) for name in files
                         if name.lower().endswith((".osp", ".xml")))
    except OSError:
        return matches
    for path in paths:
        for name in _parse_slider_names(path):
            matches[name].add(os.path.basename(path))
    return matches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--slider-groups", help="effective SliderGroups directory")
    source.add_argument("--mods-root", help="MO2 mods directory")
    parser.add_argument("--modlist", help="MO2 profile modlist.txt (with --mods-root)")
    parser.add_argument("--slider-sets", help="SliderSets directory for matching OSP names")
    parser.add_argument("--output", default="group_membership_report.csv")
    parser.add_argument("--group", action="append", dest="groups",
                        help="only report this group; may be repeated")
    args = parser.parse_args()

    if args.mods_root:
        if not args.modlist:
            parser.error("--modlist is required with --mods-root")
        definitions = _definitions_from_profile(args.mods_root, args.modlist)
        osp_matches = _osp_matches_from_profile(args.mods_root, args.modlist)
    else:
        definitions = read_definitions(args.slider_groups)
        osp_matches = (_osp_matches_from_folder(args.slider_sets)
                       if args.slider_sets else defaultdict(set))

    allowed = set(args.groups or [])
    definitions = [d for d in definitions if not allowed or d.name in allowed]
    logical = defaultdict(list)
    for definition in definitions:
        logical[definition.name].append(definition)

    rows = []
    for definition in definitions:
        for member in definition.members:
            rows.append({
                "Group": definition.name,
                "Source XML": _source_label(definition.source_xml),
                "Member": member,
                "Matching OSP": "; ".join(sorted(osp_matches.get(member, set()))),
                "Found/Missing": "Found" if member in osp_matches else "Missing",
            })
    with open(args.output, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "Group", "Source XML", "Member", "Matching OSP", "Found/Missing"
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Raw group definitions: {len(definitions)}")
    print(f"Distinct logical groups: {len(logical)}")
    for name in sorted(logical):
        defs = logical[name]
        members = {member for definition in defs for member in definition.members}
        sources = sorted({_source_display(d.source_xml) for d in defs})
        print(f"{name}: {len(defs)} definitions, {len(members)} unique members, "
              f"sources: {', '.join(sources)}")
    print(f"Wrote {len(rows)} membership rows to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
