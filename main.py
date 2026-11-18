from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--interrupt":
        from drive_app.process import interrupt_helper
        return interrupt_helper(int(sys.argv[2]))
    smoke = "--smoke-test" in sys.argv
    if smoke and os.name != "nt" and not os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pathlib import Path
    import tempfile
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox
    from drive_app.gui import Window, official_icon

    app = QApplication(sys.argv)
    app.setApplicationName("Driver Downloader")
    app.setOrganizationName("DriverDownloader")
    app.setWindowIcon(official_icon())
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    temporary = None
    lock = None
    if smoke:
        class SmokeManager:
            def snapshot(self): return {"jobs": [], "files": [], "history": []}
            def shutdown(self): pass
            def set_limits(self, concurrency, bandwidth_limit): pass
        temporary = tempfile.TemporaryDirectory(prefix="driver-downloader-smoke-")
        root, manager, auth = Path(temporary.name), SmokeManager(), None
    else:
        from drive_app.runtime import app_directory, create_runtime, prepare_root
        try:
            root = app_directory()
            prepare_root(root)
            lock = QLockFile(str(root / "app.lock"))
            lock.setStaleLockTime(0)
            if not lock.tryLock(0):
                QMessageBox.information(None, "Driver Downloader", "O aplicativo já está aberto nesta conta do Windows.")
                return 0
            manager, auth = create_runtime(root)
        except Exception:
            QMessageBox.critical(None, "Driver Downloader", "Não foi possível preparar o aplicativo. Confira a pasta local e os registros de instalação.")
            if lock: lock.unlock()
            return 1
    window = Window(root, manager, auth)
    window.show()
    if smoke:
        QTimer.singleShot(350, window.close)
        QTimer.singleShot(5000, lambda: app.exit(2))
    result = app.exec()
    if lock: lock.unlock()
    if temporary: temporary.cleanup()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
