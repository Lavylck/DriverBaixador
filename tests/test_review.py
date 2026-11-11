"""Independent regressions from the security/reliability review."""
import json
from dataclasses import replace

from drive_app.models import Control, RemoteFile
from drive_app.state import Store
from drive_app.transfer import Bandwidth, part_path, transfer_file


def test_runtime_restores_real_global_bandwidth_limit(tmp_path, monkeypatch):
    from drive_app import manager, runtime

    (tmp_path / "preferences.json").write_text(
        json.dumps({"concurrency": 2, "bandwidth_mib": 3.5}), encoding="utf-8")
    calls = {}

    class FakeManager:
        def __init__(self, root, factory, **limits):
            calls.update(limits)

    monkeypatch.setattr(manager, "Manager", FakeManager)
    monkeypatch.setattr(runtime, "prepare_root", lambda root: None)
    monkeypatch.setattr(runtime, "clean_stale_sessions", lambda root: None)
    monkeypatch.setattr(runtime, "RcloneAuth", lambda root: object())
    runtime.create_runtime(tmp_path)
    assert calls == {"concurrency": 2, "bandwidth_limit": 3670016}


def test_new_job_cannot_mix_an_old_partial_with_new_content_without_md5(tmp_path):
    """A source version change must isolate partials even for a NEW queue row."""
    destination = tmp_path / "downloads"
    destination.mkdir()
    old = RemoteFile("file-id", "video.mp4", "video.mp4", 6, version="old")
    part_path(destination, old).write_bytes(b"OLD")
    fresh = replace(old, version="new")
    store = Store(tmp_path)
    store.add_job("new-job", "https://drive.google.com/file/d/file-id/view", str(destination))
    store.add_files("new-job", destination, [fresh])
    identity = store.query("SELECT id FROM files")[0]["id"]

    class Response:
        status_code = 200
        headers = {"Content-Length": "6"}

        def iter_content(self, chunk_size):
            yield b"NEWNEW"

        def close(self):
            pass

    class Client:
        offsets = []

        def validate(self, remote):
            return remote

        def open(self, remote, offset, validator):
            self.offsets.append(offset)
            return Response()

    client = Client()
    try:
        transfer_file(store, identity, client, destination, Control(), Bandwidth())
        assert client.offsets == [0]
        assert (destination / "video.mp4").read_bytes() == b"NEWNEW"
        assert store.file(identity)["state"] == "completed"
    finally:
        store.close()
