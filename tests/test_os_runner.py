"""Regression tests for chaining CLI runs through one OsRunner."""
import os
import sys
import time
import unittest

from PySide6.QtWidgets import QApplication

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core.os_runner import OsRunner


class OsRunnerChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _pump(self, done, timeout=30.0):
        deadline = time.time() + timeout
        while not done() and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)

    def test_chained_run_is_not_destroyed_by_the_previous_cleanup(self):
        """A patch spanning several projects queues one CLI run per project.

        The finished signal is delivered synchronously, so the handler that
        starts the next run does so *inside* _on_finished. Cleaning up after
        emitting therefore tore down the run that had just started, which
        killed every project after the first.
        """
        runner = OsRunner()
        exits, errors = [], []
        runner.log_line.connect(
            lambda line: errors.append(line) if "QProcess error" in line else None)

        pending = [["-c", "pass"]]

        def on_finished(code):
            exits.append(code)
            if pending:
                runner.run(sys.executable, pending.pop(0))

        runner.finished.connect(on_finished)
        runner.run(sys.executable, ["-c", "pass"])
        self._pump(lambda: len(exits) >= 2)

        self.assertEqual(exits, [0, 0], f"chained run did not survive; errors={errors}")
        self.assertEqual(errors, [])

    def test_output_of_the_chained_run_still_reaches_the_log(self):
        runner = OsRunner()
        lines, exits = [], []
        runner.log_line.connect(lines.append)

        pending = [["-c", "print('second')"]]

        def on_finished(code):
            exits.append(code)
            if pending:
                runner.run(sys.executable, pending.pop(0))

        runner.finished.connect(on_finished)
        runner.run(sys.executable, ["-c", "print('first')"])
        self._pump(lambda: len(exits) >= 2)

        self.assertEqual(exits, [0, 0])
        self.assertIn("first", lines)
        self.assertIn("second", lines)


if __name__ == "__main__":
    unittest.main()
