import sys

# The headparts backend runs in this same executable, before the GUI stack is
# imported: the packaged exe is the only Python the user has, and a PySide6
# import costs seconds the CLI path has no use for.
if "--headparts" in sys.argv[1:]:
    from core.headparts_cli import run_headparts

    raise SystemExit(run_headparts(sys.argv[1:]))

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
