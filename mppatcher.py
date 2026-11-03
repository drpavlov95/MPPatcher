import argparse
import sys
from pathlib import Path


def _run_headparts(argv: list[str]) -> int:
    """CLI entry used by the packaged GUI for the TRI headpart backend."""
    from core.headpart_patcher import discover_references, patch_headparts
    from core.source_staging import StagedSource, find_tri_root

    ap = argparse.ArgumentParser(prog="MPPatcher --headparts",
                                 description="Transfer Physiques of Skyrim head morphs to TRI headparts")
    ap.add_argument("--headparts", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--source", required=True, help="folder or .zip containing headpart TRIs")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--female-ref")
    ap.add_argument("--male-ref")
    ap.add_argument("--only-missing", action="store_true")
    ap.add_argument("--filter-file", help="newline-separated relative TRI paths")
    args = ap.parse_args(argv)
    kind = "zip" if Path(args.source).suffix.lower() == ".zip" else "folder"
    with StagedSource(args.source, kind) as staged:
        refs = (args.female_ref, args.male_ref)
        if not all(refs):
            refs = discover_references(str(staged.root))
        if not refs:
            print("[ERROR] Could not find Physiques of Skyrim FACE_FemaleHead.tri and FACE_MaleHead.tri.")
            return 2
        root = find_tri_root(staged.root)
        selected = None
        if args.filter_file:
            selected = {line.strip().replace("\\", "/")
                        for line in Path(args.filter_file).read_text(encoding="utf-8").splitlines()
                        if line.strip()}
        report = patch_headparts(str(root), args.output_dir, refs[0], refs[1],
                                 only_missing=args.only_missing,
                                 selected_paths=selected)
        for item in report.files:
            suffix = f" - {item.error}" if item.error else ""
            print(f"[{item.status.upper():7}] {item.relpath}{suffix}")
        counts = report.counts()
        print("Headparts: " + ", ".join(f"{n} {k}" for k, n in sorted(counts.items())))
        return 1 if counts.get("failed") else 0


if "--headparts" in sys.argv[1:]:
    raise SystemExit(_run_headparts(sys.argv[1:]))

from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("MPPatcher")
    app.setApplicationVersion("0.1.0")
    app.setOrganizationName("ModernPhysiquePicker")
    app.setStyle("Fusion")  # consistent cross-platform baseline for QSS

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
