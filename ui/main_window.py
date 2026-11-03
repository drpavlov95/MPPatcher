"""Compatibility entry point for the active MPPatcher UI module.

The UI implementation lives in :mod:`ui.main_window_ui`; keeping this module
preserves the stable import used by the packaged launcher and test tooling.
"""

from .main_window_ui import MainWindow

__all__ = ["MainWindow"]
