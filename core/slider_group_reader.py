import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class SliderGroupDefinition:
    """One literal ``<Group>`` element from one SliderGroups XML file."""

    name: str
    source_xml: str
    members: list[str] = field(default_factory=list)


@dataclass
class SliderGroup:
    name: str
    members: list[str] = field(default_factory=list)
    # Provenance is retained for diagnostics without changing the logical
    # group structure shown in the UI.
    source_files: list[str] = field(default_factory=list)
    definitions: list[SliderGroupDefinition] = field(default_factory=list)


def read_all_physical(folders: list[str]) -> list[SliderGroup]:
    """Read and merge groups from multiple physical SliderGroups folders."""
    merged: dict[str, SliderGroup] = {}
    seen_files: set[str] = set()
    for folder in folders:
        for g in read_all(folder):
            source_paths = g.source_files or [folder]
            if any(os.path.normcase(os.path.abspath(source)) in seen_files
                   for source in source_paths):
                continue
            seen_files.update(os.path.normcase(os.path.abspath(source))
                              for source in source_paths)
            if g.name in merged:
                # Add members not already present
                existing = set(merged[g.name].members)
                merged[g.name].members.extend(m for m in g.members if m not in existing)
            else:
                merged[g.name] = g
    return sorted(merged.values(), key=lambda g: g.name)


def read_all(slider_groups_folder: str) -> list[SliderGroup]:
    groups: dict[str, SliderGroup] = {}
    for definition in read_definitions(slider_groups_folder):
        _merge_definition(groups, definition)
    return sorted(groups.values(), key=lambda g: g.name)



def read_definitions(slider_groups_folder: str) -> list[SliderGroupDefinition]:
    """Return literal group definitions with their source XML paths.

    Enumeration and direct XML reads are intentional: MO2 USVFS can expose a
    virtual directory to these APIs even when stat-style directory checks fail.
    """
    definitions: list[SliderGroupDefinition] = []
    # BodySlide recursively enumerates XML files below SliderGroups.  Probe
    # each child with listdir instead of os.path.isdir: MO2's USVFS exposes
    # directory enumeration but can reject stat-style directory checks.
    paths = _enumerate_xml_files(slider_groups_folder)
    for path in sorted(paths, key=lambda p: os.path.relpath(p, slider_groups_folder).casefold()):
        definitions.extend(_read_file(path))
    return definitions


def _enumerate_xml_files(root: str) -> list[str]:
    paths: list[str] = []
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(os.listdir(current), key=str.casefold)
        except OSError:
            continue
        for name in entries:
            path = os.path.join(current, name)
            try:
                # A successful enumeration means this child is a directory.
                os.listdir(path)
            except OSError:
                if name.lower().endswith(".xml"):
                    paths.append(path)
            else:
                pending.append(path)
    return paths


def _merge_definition(groups: dict[str, SliderGroup],
                      definition: SliderGroupDefinition):
    group = groups.get(definition.name)
    if group is None:
        group = SliderGroup(name=definition.name)
        groups[definition.name] = group
    if definition.source_xml not in group.source_files:
        group.source_files.append(definition.source_xml)
    group.definitions.append(definition)
    existing = set(group.members)
    group.members.extend(member for member in definition.members if member not in existing)


def _read_file(path: str) -> list[SliderGroupDefinition]:
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except ET.ParseError:
        return []

    results = []
    for group_elem in root.findall("Group"):
        name = group_elem.get("name", "").strip()
        if not name:
            continue
        members = [
            m.get("name", "").strip()
            for m in group_elem.findall("Member")
            if m.get("name", "").strip()
        ]
        results.append(SliderGroupDefinition(name=name, source_xml=path,
                                             members=members))
    return results
