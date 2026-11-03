"""Internal CLI. The Plan C GUI calls the same core modules, not this file."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .morphs import find_morph, match_morph_names
from .pipeline import BatchConfig, run_batch
from .report import RunReport
from .transfer import transfer_morphs
from .tri import TriError, read_tri


def _cmd_inspect(args) -> int:
    path = Path(args.file)
    try:
        tri = read_tri(path.read_bytes(), source=str(path))
    except (OSError, TriError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({
            "path": str(path),
            "vertex_count": tri.vertex_count,
            "face_count": len(tri.faces),
            "morphs": [{
                "name": m.name,
                "multiplier": m.multiplier,
                "max_abs_delta": float(abs(m.deltas()).max()) if m.multiplier else 0.0,
            } for m in tri.morphs],
        }, indent=2))
    else:
        print(f"{path}")
        print(f"  vertices: {tri.vertex_count}   faces: {len(tri.faces)}   "
              f"morphs: {len(tri.morphs)}")
        for m in tri.morphs:
            mx = float(abs(m.deltas()).max()) if m.multiplier else 0.0
            print(f"    {m.name:<40} mult={m.multiplier:.6g}  max_delta={mx:.4f}")
    return 0


def _cmd_transfer(args) -> int:
    ref_path = Path(args.ref)
    try:
        ref = read_tri(ref_path.read_bytes(), source=str(ref_path))
    except (OSError, TriError) as exc:
        print(f"error: cannot read reference: {exc}", file=sys.stderr)
        return 2

    patterns = [p.strip() for p in args.morphs.split(",") if p.strip()]
    selected = match_morph_names(ref.morph_names(), patterns)
    if not selected:
        print(f"error: no reference morphs match {patterns!r} "
              f"(available: {ref.morph_names()})", file=sys.stderr)
        return 2
    print(f"Selected {len(selected)} morph(s): {', '.join(selected)}")
    if args.mode == "overwrite":
        print("WARNING: overwrite mode deletes ALL existing morphs in each target.")

    def process(tri, result):
        if args.only_missing and all(find_morph(tri, n) for n in selected):
            result.warnings.append("already has all selected morphs")
            return None
        outcome = transfer_morphs(
            ref, tri, selected, mode=args.mode,
            replace_same_name=not args.no_replace_same_name,
            method=args.method)
        result.method = outcome.method
        result.added = outcome.added
        result.replaced = outcome.replaced
        result.removed = outcome.removed
        result.skipped_same_name = outcome.skipped_same_name
        result.metrics.update(outcome.metrics)
        p95 = outcome.metrics.get("map_dist_p95", 0.0)
        if p95 > args.far_warn:
            result.warnings.append(
                f"target far from reference surface (p95={p95:.2f})")
        return tri

    cfg = BatchConfig(
        targets=[Path(t) for t in args.target],
        include=args.include or ["*.tri"],
        exclude=args.exclude or [],
        recursive=not args.no_recursive,
        output_dir=Path(args.out) if args.out else None,
        in_place=args.in_place,
        backup=not args.no_backup,
        dry_run=args.dry_run,
    )
    report = RunReport(tool="transfer", reference=str(ref_path),
                       selected_morphs=selected, targets=[], output_dir=None,
                       mode=args.mode)
    try:
        run_batch(cfg, process, report)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for f in report.files:
        extra = f" (+{len(f.added)} added)" if f.added else ""
        extra += f" ({len(f.replaced)} replaced)" if f.replaced else ""
        extra += f" — {f.error}" if f.error else ""
        print(f"[{f.status}] {f.relpath}{extra}")
    print("Summary:", ", ".join(f"{v} {k}" for k, v in sorted(report.counts().items())))

    report_dir = args.report_dir or (args.out if args.out and not args.dry_run else None)
    if report_dir:
        jpath, mpath = report.write(Path(report_dir))
        print(f"Report: {jpath}")
    elif args.dry_run:
        print(report.to_markdown())
    return 1 if report.counts().get("failed") else 0


def _cmd_bake(args) -> int:
    from .bake import bake_morph

    def process(tri, result):
        try:
            outcome = bake_morph(tri, args.morph, amount=args.amount,
                                 after=args.after, rename_to=args.rename_to)
        except KeyError:
            result.warnings.append(f"morph {args.morph!r} not present — skipped")
            return None
        result.method = f"bake:{outcome.after}"
        result.removed = [outcome.baked] if args.after == "delete" else []
        result.warnings.extend(outcome.warnings)
        result.metrics["bake_amount"] = args.amount
        return tri

    cfg = BatchConfig(
        targets=[Path(t) for t in args.target],
        include=args.include or ["*.tri"],
        exclude=args.exclude or [],
        recursive=not args.no_recursive,
        output_dir=Path(args.out) if args.out else None,
        in_place=args.in_place,
        backup=not args.no_backup,
        dry_run=args.dry_run,
    )
    report = RunReport(tool="bake", reference="(morph lives in targets)",
                       selected_morphs=[args.morph], targets=[], output_dir=None,
                       mode=args.after)
    try:
        run_batch(cfg, process, report)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for f in report.files:
        print(f"[{f.status}] {f.relpath}" + (f" — {f.error}" if f.error else ""))
    print("Summary:", ", ".join(f"{v} {k}" for k, v in sorted(report.counts().items())))
    report_dir = args.report_dir or (args.out if args.out and not args.dry_run else None)
    if report_dir:
        report.write(Path(report_dir))
    return 1 if report.counts().get("failed") else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="fmp", description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="list morphs inside a TRI file")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=_cmd_inspect)

    p = sub.add_parser("transfer", help="batch-transfer reference morphs to targets")
    p.add_argument("--ref", required=True)
    p.add_argument("--target", action="append", required=True)
    p.add_argument("--morphs", default="*",
                   help="comma-separated case-insensitive glob patterns")
    p.add_argument("--mode", choices=["merge", "overwrite"], default="merge")
    p.add_argument("--no-replace-same-name", action="store_true")
    p.add_argument("--only-missing", action="store_true")
    p.add_argument("--method", choices=["auto", "nearest", "identity"],
                   default="auto")
    p.add_argument("--include", action="append")
    p.add_argument("--exclude", action="append")
    p.add_argument("--no-recursive", action="store_true")
    p.add_argument("--out")
    p.add_argument("--in-place", action="store_true")
    p.add_argument("--no-backup", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report-dir")
    p.add_argument("--far-warn", type=float, default=8.0)
    p.set_defaults(func=_cmd_transfer)

    p = sub.add_parser("bake", help="bake a morph into target base geometry")
    p.add_argument("--morph", required=True)
    p.add_argument("--amount", type=float, default=1.0)
    p.add_argument("--after", choices=["delete", "keep", "negate", "rename"],
                   default="delete")
    p.add_argument("--rename-to")
    p.add_argument("--target", action="append", required=True)
    p.add_argument("--include", action="append")
    p.add_argument("--exclude", action="append")
    p.add_argument("--no-recursive", action="store_true")
    p.add_argument("--out")
    p.add_argument("--in-place", action="store_true")
    p.add_argument("--no-backup", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--report-dir")
    p.set_defaults(func=_cmd_bake)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
