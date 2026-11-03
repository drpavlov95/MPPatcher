"""Recursive target discovery with include/exclude glob filters."""
from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path


def _matches(name: str, rel: str, patterns) -> bool:
    return any(fnmatchcase(name, p.lower()) or fnmatchcase(rel, p.lower())
               for p in patterns)


def collect_targets(root: Path, include=("*.tri",), exclude=(),
                    recursive: bool = True) -> list[Path]:
    root = Path(root)
    it = root.rglob("*") if recursive else root.glob("*")
    out = []
    for p in sorted(it):
        if not p.is_file():
            continue
        name = p.name.lower()
        rel = p.relative_to(root).as_posix().lower()
        if not _matches(name, rel, include):
            continue
        if exclude and _matches(name, rel, exclude):
            continue
        out.append(p)
    return out
