import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from drive_app.gui import DataTable, Window, byte_text, eta_text, official_icon, progress_percent


class FakeManager:
    def __init__(self):
        self.calls = []
        self.data = {"jobs": [], "files": [], "history": []}

    def snapshot(self):
        return self.data

    def add(self, url, destination):
        self.calls.append(("add", url, destination))
        self.data["jobs"].append({"id": "job1", "name": "Vídeos do cliente", "destination": destination,
                                  "state": "queued", "size": 1024, "done": 0})
        self.data["files"].append({"id": "file1", "job_id": "job1", "relative_path": "Cena 01.mp4",
                                   "state": "queued", "size": 1024, "done": 0})
        return "job1"

    def pause(self, job_id):
        self.calls.append(("pause", job_id)); self.data["jobs"][0]["state"] = "paused"

    def resume(self, job_id):
        self.calls.append(("resume", job_id)); self.data["jobs"][0]["state"] = "running"

    def cancel(self, job_id):
        self.calls.append(("cancel", job_id)); self.data["jobs"][0]["state"] = "cancelled"

    def set_limits(self, concurrency, bandwidth):
        self.calls.append(("limits", concurrency, bandwidth))

    def shutdown(self):
        self.calls.append(("shutdown",))


def app():
    return QApplication.instance() or QApplication([])


def close_window(window):
    window.close()
    deadline = time.monotonic() + 3
    while not window.can_close and time.monotonic() < deadline:
        app().processEvents()
        time.sleep(0.005)
    assert window.can_close
    app().processEvents()


def test_download_navigation_pause_resume_cancel(tmp_path):
    application = app()
    manager = FakeManager()
    window = Window(tmp_path, manager)
    window.show()
    application.processEvents()
    assert window.windowTitle() == "Driver Downloader"
    assert not official_icon().isNull()
    assert not window.windowIcon().isNull()
    assert not official_icon().pixmap(32, 32).isNull()
    assert any(label.text() == "Driver Downloader" for label in window.findChildren(QLabel))
    assert not window.grab().isNull()
    window.url.setText("https://drive.google.com/drive/folders/abcde")
    window.destination.setText(str(tmp_path / "downloads"))
    window.start_download()
    assert manager.calls[0][0] == "add"
    assert window.stack.currentIndex() == 1
    assert window.files_model.rowCount() == 1
    window.pause_button.click()
    assert manager.calls[-1] == ("pause", "job1")
    assert window.resume_button.isEnabled()
    window.resume_button.click()
    assert manager.calls[-1] == ("resume", "job1")
    assert window.pause_button.isEnabled()
    window.cancel_button.click()
    assert manager.calls[-1] == ("cancel", "job1")
    close_window(window)
    assert manager.calls[-1] == ("shutdown",)


def test_settings_persist_and_limits_are_applied(tmp_path):
    application = app()
    manager = FakeManager()
    window = Window(tmp_path, manager)
    window.concurrency.setValue(2)
    window.bandwidth.setValue(3.5)
    window.default_destination.setText(str(tmp_path))
    window.theme.setCurrentIndex(1)
    window.save_settings()
    assert ("limits", 2, int(3.5 * 1024 * 1024)) in manager.calls
    close_window(window)
    reopened = Window(tmp_path, FakeManager())
    assert reopened.concurrency.value() == 2
    assert reopened.bandwidth.value() == 3.5
    assert reopened.theme.currentData() == "light"
    assert reopened.destination.text() == str(tmp_path)
    close_window(reopened)


def test_invalid_empty_fields_do_not_enqueue(tmp_path):
    application = app()
    manager = FakeManager()
    window = Window(tmp_path, manager)
    window.start_download()
    assert not manager.calls
    assert "Informe o link" in window.home_notice.text()
    close_window(window)


def test_unknown_size_and_completed_progress():
    assert progress_percent({"state": "running", "size": None, "done": 500}) is None
    assert progress_percent({"state": "running", "size": 100, "done": 100}) == 99
    assert progress_percent({"state": "checking", "size": 100, "done": 100}) == 99
    assert progress_percent({"state": "completed", "size": None}) == 100
    assert byte_text(None) == "—"
    assert eta_text(float("inf")) == "—"


def test_selected_export_note_is_visible_and_preserved_during_refresh(tmp_path):
    application = app()
    manager = FakeManager()
    manager.add("https://drive.google.com/file/d/abcde/view", str(tmp_path))
    manager.data["files"][0].update(name="Documento.docx", note="Exportação Google: reinicia do início ao continuar.", resumable=False)
    window = Window(tmp_path, manager)
    window.navigate(1)
    window.files_table.setCurrentIndex(window.files_model.index(0, 0))
    assert "Exportação Google" in window.file_detail.text()
    assert not window.file_detail.isHidden()
    manager.data["files"][0]["done"] = 512
    window.refresh()
    assert window.files_table.currentIndex().row() == 0
    assert "reinicia" in window.file_detail.text()
    tooltip = window.files_model.data(window.files_model.index(0, 0), 3)
    assert "Exportação Google" in tooltip
    close_window(window)


def test_source_smoke_test_runs_real_gui():
    project = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(project / "main.py"), "--smoke-test"],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
