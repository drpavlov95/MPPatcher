"""Batch orchestration shared by the CLI (Plan A) and the GUI (Plan C)."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .report import FileResult, RunReport
from .scan import collect_targets
from .tri import TriError, read_tri, write_tri


@dataclass
class BatchConfig:
    targets: list[Path]
    include: list[str] = field(default_factory=lambda: ["*.tri"])
    exclude: list[str] = field(default_factory=list)
    recursive: bool = True
    output_dir: Path | None = None
    in_place: bool = False
    backup: bool = True
    dry_run: bool = False

    def __post_init__(self):
        if not self.in_place and self.output_dir is None and not self.dry_run:
            raise ValueError("output_dir is required unless in_place or dry_run")


def _resolve_roots(cfg: BatchConfig) -> list[tuple[Path, list[Path]]]:
    roots = []
    for t in cfg.targets:
        t = Path(t)
        if t.is_file():
            roots.append((t.parent, [t]))
        elif t.is_dir():
            roots.append((t, collect_targets(t, include=cfg.include,
                                             exclude=cfg.exclude,
                                             recursive=cfg.recursive)))
        else:
            raise FileNotFoundError(f"target does not exist: {t}")
    return roots


def run_batch(cfg: BatchConfig, process, report: RunReport) -> RunReport:
    report.targets = [str(t) for t in cfg.targets]
    report.output_dir = "(in place)" if cfg.in_place else (
        str(cfg.output_dir) if cfg.output_dir else None)
    report.dry_run = cfg.dry_run

    for root, files in _resolve_roots(cfg):
        backup_zip: zipfile.ZipFile | None = None
        try:
            for path in files:
                rel = path.relative_to(root)
                result = FileResult(path=str(path), relpath=str(rel))
                report.files.append(result)

                raw = path.read_bytes()
                try:
                    tri = read_tri(raw, source=str(path))
                except TriError as exc:
                    result.status = "failed"
                    result.error = str(exc)
                    continue

                new_tri = process(tri, result)
                if new_tri is None:
                    if result.status == "pending":
                        result.status = "skipped"
                    continue

                data = write_tri(new_tri)
                if cfg.in_place:
                    out_path = path
                elif cfg.output_dir is not None:
                    out_path = Path(cfg.output_dir) / rel
                else:                       # dry-run preview with no destination
                    out_path = None
                result.output = str(out_path) if out_path else None
                result.status = "patched"
                if cfg.dry_run:
                    continue

                if cfg.in_place and cfg.backup:
                    if backup_zip is None:
                        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                        backup_zip = zipfile.ZipFile(
                            root / f"FMP_backup_{stamp}.zip", "w",
                            zipfile.ZIP_DEFLATED)
                    backup_zip.writestr(rel.as_posix(), raw)

                out_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = out_path.with_name(out_path.name + ".fmp_tmp")
                tmp.write_bytes(data)
                tmp.replace(out_path)
        finally:
            if backup_zip is not None:
                backup_zip.close()
    return report
