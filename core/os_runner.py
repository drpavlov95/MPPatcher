from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal, QTimer


class OsRunner(QObject):
    log_line = Signal(str)
    finished = Signal(int)   # exit code

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._finish_emitted = False

    def run(self, exe: str, args: list, environment: dict[str, str] | None = None):
        if self.is_running():
            return
        self._process = QProcess(self)
        self._finish_emitted = False
        self._process.setProcessChannelMode(QProcess.MergedChannels)
        if environment:
            process_environment = QProcessEnvironment.systemEnvironment()
            for name, value in environment.items():
                process_environment.insert(str(name), str(value))
            self._process.setProcessEnvironment(process_environment)
        self._process.readyRead.connect(self._on_data)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.start(exe, args)
        if not self._process.waitForStarted(3000):
            err = self._process.errorString()
            self.log_line.emit(f"[ERROR] Process failed to start: {err} | exe={exe}")
            self._emit_finished_once(-1)

    def _on_error(self, error):
        self.log_line.emit(f"[ERROR] QProcess error: {error} ({self._process.errorString() if self._process else ''})")

    def cancel(self):
        if self._process:
            # Ask the CLI to exit normally first.  A hard kill is only a
            # last-resort fallback for a wedged native process.
            self._process.terminate()
            process = self._process
            QTimer.singleShot(2000, lambda: process.kill()
                              if process.state() != QProcess.NotRunning else None)

    def is_running(self) -> bool:
        return self._process is not None and self._process.state() != QProcess.NotRunning

    def _on_data(self):
        while self._process and self._process.canReadLine():
            line = bytes(self._process.readLine()).decode("utf-8", errors="replace").strip()
            if line:
                self.log_line.emit(line)

    def _on_finished(self, exit_code: int, _exit_status):
        # Retire the finished process BEFORE emitting. `finished` is delivered
        # synchronously, so a listener that queues the next run (one CLI run per
        # project) reassigns self._process inside this call; cleaning up
        # afterwards tore down the run that had just started and killed every
        # project after the first.
        process = self._process
        self._process = None
        if process:
            process.deleteLater()
        self._emit_finished_once(exit_code)

    def _emit_finished_once(self, exit_code: int):
        if self._finish_emitted:
            return
        self._finish_emitted = True
        self.finished.emit(exit_code)
