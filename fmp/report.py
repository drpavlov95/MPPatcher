"""Per-run reports: JSON for machines, Markdown for humans."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class FileResult:
    path: str
    relpath: str
    status: str = "pending"
    error: str | None = None
    output: str | None = None
    method: str | None = None
    added: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped_same_name: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class RunReport:
    tool: str
    reference: str
    selected_morphs: list[str]
    targets: list[str]
    output_dir: str | None
    mode: str | None = None
    dry_run: bool = False
    started: str = field(
        default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    files: list[FileResult] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return dict(Counter(f.status for f in self.files))

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def to_markdown(self) -> str:
        c = self.counts()
        lines = [
            f"# FaceMorphs Patcher — {self.tool} report",
            "",
            f"- **Started:** {self.started}",
            f"- **Reference:** `{self.reference}`",
            f"- **Selected morphs ({len(self.selected_morphs)}):** "
            + ", ".join(f"`{m}`" for m in self.selected_morphs),
            f"- **Targets:** " + ", ".join(f"`{t}`" for t in self.targets),
            f"- **Output:** `{self.output_dir}`" + ("  (DRY RUN — nothing written)"
                                                    if self.dry_run else ""),
            f"- **Mode:** {self.mode}",
            f"- **Files:** {len(self.files)} total — "
            + ", ".join(f"{v} {k}" for k, v in sorted(c.items())),
            "",
            "| File | Status | Method | Added | Replaced | Removed | Warnings | Metrics |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for f in self.files:
            metrics = "; ".join(f"{k}={v:.3g}" for k, v in f.metrics.items())
            lines.append(
                f"| `{f.relpath}` | {f.status} | {f.method or ''} "
                f"| {', '.join(f.added)} | {', '.join(f.replaced)} "
                f"| {', '.join(f.removed)} | {'; '.join(f.warnings)} | {metrics} |")
        failures = [f for f in self.files if f.status == "failed"]
        if failures:
            lines += ["", "## Failures", ""]
            lines += [f"- `{f.relpath}`: {f.error}" for f in failures]
        return "\n".join(lines) + "\n"

    def write(self, directory: Path, stem: str = "FMP_report") -> tuple[Path, Path]:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        jpath = directory / f"{stem}.json"
        mpath = directory / f"{stem}.md"
        jpath.write_text(self.to_json(), encoding="utf-8")
        mpath.write_text(self.to_markdown(), encoding="utf-8")
        return jpath, mpath
