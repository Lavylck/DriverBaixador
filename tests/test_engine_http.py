from __future__ import annotations

import hashlib
import socket
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests

from drive_app.manager import Manager
from drive_app.models import Control, DownloadError, Plan, RemoteFile
from drive_app.state import Store
from drive_app.transfer import Bandwidth, part_path, transfer_file


class ServerState:
    def __init__(self):
        self.data = {}
        self.requests = []
        self.delay = 0
        self.ignore_range = False
        self.bad_range_once = False
        self.drop_once = False
        self.chunk_size = 4096
        self.active = self.peak = 0
        self.lock = threading.Lock()


@pytest.fixture
def http_source():
    state = ServerState()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            key = self.path.lstrip("/")
            body = state.data[key]
            requested = self.headers.get("Range", "")
            with state.lock:
                state.requests.append((key, requested, self.headers.get("If-Range", "")))
                drop = state.drop_once
                state.drop_once = False
                bad_range = state.bad_range_once
                state.bad_range_once = False
                state.active += 1
                state.peak = max(state.peak, state.active)
            try:
                offset = int(requested.split("=")[1].rstrip("-")) if requested else 0
                ranged = bool(requested) and not state.ignore_range
                if offset >= len(body) and requested and not state.ignore_range:
                    self.send_response(416)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                start = offset if ranged else 0
                payload = body[start:]
                self.send_response(206 if ranged else 200)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("ETag", '"v1"')
                if ranged:
                    self.send_header("Content-Range", f"bytes {start + (1 if bad_range else 0)}-{len(body)-1}/{len(body)}")
                self.end_headers()
                if drop:
                    self.wfile.write(payload[:max(1, len(payload)//2)])
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                for offset in range(0, len(payload), state.chunk_size):
                    self.wfile.write(payload[offset:offset+state.chunk_size])
                    self.wfile.flush()
                    if state.delay:
                        time.sleep(state.delay)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with state.lock:
                    state.active -= 1

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base = f"http://127.0.0.1:{server.server_port}"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def remote(state, key="video", path="video.mp4", md5=True, export=False, version="v1"):
    return RemoteFile(key, Path(path).name, path, None if export else len(state.data[key]),
                      hashlib.md5(state.data[key]).hexdigest() if md5 else "",
                      "date", "", "application/pdf" if export else "", version, not export)


class Client:
    def __init__(self, state, files, directories=()):
        self.state, self.files, self.directories = state, files, list(directories)

    def plan(self, url, destination, control):
        control.check()
        return Plan(self.files, self.directories, "Pasta de teste")

    def validate(self, file):
        return next(item for item in self.files if item.id == file.id)

    def open(self, file, offset, validator):
        headers = {"Accept-Encoding": "identity"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            if validator:
                headers["If-Range"] = validator
        return requests.get(self.state.base + "/" + file.id, headers=headers, stream=True, timeout=(2, 2))


def wait(manager, predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = manager.snapshot()
        if predicate(snapshot):
            return snapshot
        time.sleep(.02)
    pytest.fail(f"Queue did not reach expected state: {manager.snapshot()}")


def finished(manager, identity):
    return wait(manager, lambda data: any(job["id"] == identity and job["state"] in ("completed", "error", "cancelled") for job in data["jobs"]))


def test_single_zero_and_multiple_nested_direct_files(tmp_path, http_source):
    state = http_source
    state.data = {"a": b"original" * 20000, "b": b"second" * 30000, "zero": b""}
    state.delay = .001
    files = [remote(state, "a", "sub/ação.mp4"), remote(state, "b", "b.bin"), remote(state, "zero", "zero.bin")]
    manager = Manager(tmp_path / "state", lambda: Client(state, files, ["sub", "empty"]), concurrency=2)
    destination = tmp_path / "downloads"
    try:
        identity = manager.add("https://drive.google.com/drive/folders/test", destination)
        data = finished(manager, identity)
        assert data["jobs"][0]["state"] == "completed", data
        assert (destination / "sub/ação.mp4").read_bytes() == state.data["a"]
        assert (destination / "b.bin").read_bytes() == state.data["b"]
        assert (destination / "zero.bin").read_bytes() == b""
        assert (destination / "empty").is_dir()
        assert 1 < state.peak <= 2
        assert len(state.requests) == 3
    finally:
        manager.shutdown()


def test_pause_true_byte_resume_and_queue_restart(tmp_path, http_source):
    state = http_source
    state.data = {"video": b"abcdefgh" * (512 * 1024)}
    state.delay = .0015
    files = [remote(state)]
    factory = lambda: Client(state, files)
    manager = Manager(tmp_path / "state", factory)
    destination = tmp_path / "downloads"
    identity = manager.add("link", destination)
    wait(manager, lambda data: data["files"] and data["files"][0]["done"] > 131072)
    manager.pause(identity)
    snapshot = wait(manager, lambda data: data["jobs"][0]["state"] == "paused")
    offset = snapshot["files"][0]["done"]
    assert 0 < offset < len(state.data["video"])
    time.sleep(.1)
    assert manager.snapshot()["files"][0]["done"] == offset
    assert not (destination / "video.mp4").exists()
    manager.shutdown()
    manager = Manager(tmp_path / "state", factory)
    try:
        assert manager.snapshot()["jobs"][0]["state"] == "paused"
        assert len(state.requests) == 1
        manager.resume(identity)
        data = finished(manager, identity)
        assert data["jobs"][0]["state"] == "completed", data
        assert ("video", f"bytes={offset}-", '"v1"') in state.requests
        assert (destination / "video.mp4").read_bytes() == state.data["video"]
    finally:
        manager.shutdown()


def test_network_drop_automatically_resumes(tmp_path, http_source):
    state = http_source
    state.data = {"video": b"01234567" * (256 * 1024)}
    state.drop_once = True
    file = remote(state)
    manager = Manager(tmp_path / "state", lambda: Client(state, [file]))
    try:
        identity = manager.add("link", tmp_path / "downloads")
        data = finished(manager, identity)
        assert data["jobs"][0]["state"] == "completed", data
        assert any(item[1].startswith("bytes=") for item in state.requests)
        assert (tmp_path / "downloads/video.mp4").read_bytes() == state.data["video"]
    finally:
        manager.shutdown()


@pytest.mark.parametrize("ignore,bad", [(True, False), (False, True)])
def test_server_without_ranges_or_invalid_range_never_appends_full_body(tmp_path, http_source, ignore, bad):
    state = http_source
    state.data = {"video": b"correct-content" * 50000}
    state.ignore_range, state.bad_range_once = ignore, bad
    file = remote(state)
    destination = tmp_path / "downloads"
    destination.mkdir()
    part_path(destination, file).write_bytes(state.data["video"][:65536])
    manager = Manager(tmp_path / "state", lambda: Client(state, [file]))
    try:
        identity = manager.add("link", destination)
        data = finished(manager, identity)
        assert data["jobs"][0]["state"] == "completed", data
        assert (destination / "video.mp4").read_bytes() == state.data["video"]
        assert state.requests[0][1] == "bytes=65536-"
        if bad:
            assert state.requests[-1][1] == ""
    finally:
        manager.shutdown()


def test_cancel_preserves_existing_target_and_can_resume(tmp_path, http_source):
    state = http_source
    state.data = {"video": b"x" * (4 * 1024 * 1024)}
    state.delay = .0015
    file = remote(state)
    destination = tmp_path / "downloads"
    destination.mkdir()
    (destination / "video.mp4").write_bytes(b"previous-complete-version")
    manager = Manager(tmp_path / "state", lambda: Client(state, [file]))
    try:
        identity = manager.add("link", destination)
        wait(manager, lambda data: data["files"] and data["files"][0]["done"] > 131072)
        manager.cancel(identity)
        snapshot = finished(manager, identity)
        assert snapshot["jobs"][0]["state"] == "cancelled"
        assert (destination / "video.mp4").read_bytes() == b"previous-complete-version"
        manager.resume(identity)
        snapshot = finished(manager, identity)
        assert snapshot["jobs"][0]["state"] == "completed", snapshot
        assert (destination / "video.mp4").read_bytes() == state.data["video"]
    finally:
        manager.shutdown()


def test_completed_files_and_exports_are_not_downloaded_again(tmp_path, http_source):
    state = http_source
    state.data = {"video": b"completed-content" * 10000, "export": b"exported-pdf-content" * 1000}
    files = [remote(state), remote(state, "export", "Documento.pdf", md5=False, export=True)]
    manager = Manager(tmp_path / "state", lambda: Client(state, files))
    try:
        identity = manager.add("link", tmp_path / "downloads")
        assert finished(manager, identity)["jobs"][0]["state"] == "completed"
        before = len(state.requests)
        identity = manager.add("link", tmp_path / "downloads")
        snapshot = finished(manager, identity)
        assert snapshot["jobs"][0]["state"] == "completed", snapshot
        assert len(state.requests) == before
        # Same-size corruption must not be trusted just because a checkpoint exists.
        target = tmp_path / "downloads/Documento.pdf"
        target.write_bytes(b"!" * target.stat().st_size)
        identity = manager.add("link", tmp_path / "downloads")
        assert finished(manager, identity)["jobs"][0]["state"] == "completed"
        assert len(state.requests) == before + 1
        assert target.read_bytes() == state.data["export"]
    finally:
        manager.shutdown()


def test_changed_source_new_job_never_reuses_old_partial_without_md5(tmp_path, http_source):
    state = http_source
    state.data = {"video": b"new-version" * 20000}
    new_file = remote(state, md5=False, version="version-2")
    old_file = replace(new_file, version="version-1")
    destination = tmp_path / "downloads"
    destination.mkdir()
    old_part = part_path(destination, old_file)
    old_part.write_bytes(b"wrong-old-content" * 10000)
    manager = Manager(tmp_path / "state", lambda: Client(state, [new_file]))
    try:
        identity = manager.add("link", destination)
        snapshot = finished(manager, identity)
        assert snapshot["jobs"][0]["state"] == "completed", snapshot
        assert state.requests[0][1] == ""
        assert (destination / "video.mp4").read_bytes() == state.data["video"]
    finally:
        manager.shutdown()


def test_exports_restart_safely_after_pause_instead_of_fake_byte_resume(tmp_path, http_source):
    state = http_source
    state.data = {"export": b"pdf-content" * (300 * 1024)}
    state.delay = .001
    file = remote(state, "export", "Documento.pdf", md5=False, export=True)
    manager = Manager(tmp_path / "state", lambda: Client(state, [file]))
    try:
        identity = manager.add("link", tmp_path / "downloads")
        wait(manager, lambda data: data["files"] and data["files"][0]["done"] > 131072)
        manager.pause(identity)
        wait(manager, lambda data: data["jobs"][0]["state"] == "paused")
        manager.resume(identity)
        assert finished(manager, identity)["jobs"][0]["state"] == "completed"
        assert all(not requested for _, requested, _ in state.requests)
        assert (tmp_path / "downloads/Documento.pdf").read_bytes() == state.data["export"]
    finally:
        manager.shutdown()


def test_insufficient_space_fails_before_network(tmp_path, http_source, monkeypatch):
    import shutil
    from collections import namedtuple
    state = http_source
    state.data = {"video": b"video"}
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(1024, 1024, 0))
    manager = Manager(tmp_path / "state", lambda: Client(state, [remote(state)]))
    try:
        identity = manager.add("link", tmp_path / "downloads")
        snapshot = finished(manager, identity)
        assert snapshot["jobs"][0]["state"] == "error"
        assert "espaço" in snapshot["jobs"][0]["error"]
        assert not state.requests
    finally:
        manager.shutdown()


def test_permission_denied_plan_never_opens_transfer(tmp_path):
    class Denied:
        def plan(self, *args):
            raise DownloadError("Sem permissão de acesso.")
    manager = Manager(tmp_path / "state", Denied)
    try:
        identity = manager.add("link", tmp_path / "downloads")
        snapshot = finished(manager, identity)
        assert snapshot["jobs"][0]["state"] == "error"
        assert not snapshot["files"]
    finally:
        manager.shutdown()


def test_pause_interrupts_slow_trickle_before_requests_chunk_fills(tmp_path, http_source):
    state = http_source
    state.data = {"video": b"v" * (128 * 1024)}
    state.chunk_size, state.delay = 1, .05
    manager = Manager(tmp_path / "state", lambda: Client(state, [remote(state)]))
    try:
        identity = manager.add("link", tmp_path / "downloads")
        wait(manager, lambda data: bool(state.requests))
        started = time.monotonic()
        manager.pause(identity)
        snapshot = wait(manager, lambda data: data["jobs"][0]["state"] == "paused", timeout=2)
        assert time.monotonic() - started < 2
        assert snapshot["files"][0]["done"] < 65536
        assert not (tmp_path / "downloads/video.mp4").exists()
    finally:
        manager.shutdown()


def test_checksum_mismatch_never_replaces_existing_final_file(tmp_path, http_source, monkeypatch):
    state = http_source
    state.data = {"video": b"corrupt-data" * 100}
    file = replace(remote(state), md5=hashlib.md5(b"different-expected-content").hexdigest())
    destination = tmp_path / "downloads"
    destination.mkdir()
    (destination / "video.mp4").write_bytes(b"previous-valid-file")
    store = Store(tmp_path / "state")
    store.add_job("job", "link", str(destination))
    store.add_files("job", destination, [file])
    identity = store.query("SELECT id FROM files")[0]["id"]
    original_wait = Control.wait
    monkeypatch.setattr(Control, "wait", lambda self, seconds: original_wait(self, min(.01, seconds)))
    try:
        with pytest.raises(DownloadError, match="integridade"):
            transfer_file(store, identity, Client(state, [file]), destination, Control(), Bandwidth(), retries=3)
        assert len(state.requests) == 3
        assert (destination / "video.mp4").read_bytes() == b"previous-valid-file"
        assert store.file(identity)["state"] != "completed"
    finally:
        store.close()
