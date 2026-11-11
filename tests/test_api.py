import io
import json
import threading
from copy import deepcopy

import pytest
import requests
from urllib3.response import HTTPResponse

from drive_app.api import API, DriveClient, FOLDER, RemoteError
from drive_app.auth import decode_authorization
from drive_app.domain import AppError, parse_link
from drive_app.logging_setup import redact
from drive_app.models import Control, DownloadError
from drive_app.storage import dpapi


class Auth:
    def __init__(self): self.forces = []
    def token(self, event=None, force=False):
        self.forces.append(force)
        return "synthetic-token"


def response(status, data, headers=None):
    result = requests.Response()
    result.status_code = status
    raw = json.dumps(data).encode()
    result.raw = HTTPResponse(body=io.BytesIO(raw), preload_content=False)
    result.headers = headers or {}
    result.url = API
    return result


def metadata(identifier, name, mime="video/mp4", **extra):
    return {"id": identifier, "name": name, "mimeType": mime, "size": "3", "md5Checksum": "900150983cd24fb0d6963f7d28e17f72",
            "modifiedTime": "2026-01-01T00:00:00Z", "version": "1", "capabilities": {"canDownload": True}, **extra}


class MetadataClient(DriveClient):
    def __init__(self, items, folders):
        super().__init__(Auth())
        self.items, self.folders = items, folders
        self.calls = []
    def _json(self, path, params, identifier, key, control=None):
        control.check() if control else None
        self.calls.append((path, params, identifier, key))
        if path == "/files":
            return deepcopy(self.folders[(identifier, params.get("pageToken"))])
        return deepcopy(self.items[identifier])


@pytest.mark.parametrize("url", [
    "https://drive.google.com/drive/folders/abcde?resourcekey=0-KEY",
    "https://drive.google.com/drive/u/0/folders/abcde?resourcekey=0-KEY",
    "https://drive.google.com/file/d/abcde/view?resourcekey=0-KEY",
    "https://drive.google.com/open?id=abcde&resourcekey=0-KEY",
    "https://docs.google.com/document/d/abcde/edit?resourcekey=0-KEY",
])
def test_links_file_folder_and_key(url):
    assert parse_link(url).id == "abcde"
    assert parse_link(url).resource_key == "0-KEY"


@pytest.mark.parametrize("url", ["http://drive.google.com/open?id=abcde", "https://drive.google.com.evil.test/open?id=abcde",
    "https://drive.google.com@evil.test/open?id=abcde", "https://drive.google.com/open?id=abcde&id=fghij",
    "https://drive.google.com:abc/open?id=abcde", "https://drive.google.com/open?id=abcde&resourcekey=foo%0aBar"])
def test_invalid_links(url):
    with pytest.raises(AppError): parse_link(url)


def test_single_file_plan_preserves_name_and_key(tmp_path):
    client = MetadataClient({"abcde": metadata("abcde", "Vídeo do cliente.mp4")}, {})
    plan = client.plan("https://drive.google.com/file/d/abcde/view?resourcekey=0-KEY", tmp_path, Control())
    assert plan.files[0].relative_path == "Vídeo do cliente.mp4"
    assert plan.files[0].resource_key == "0-KEY"
    assert client.calls[0][-1] == "0-KEY"


def test_pagination_subfolders_empty_and_permissions(tmp_path):
    root = metadata("root123", "Cliente", FOLDER)
    sub = metadata("sub123", "Vídeos", FOLDER)
    empty = metadata("empty123", "Vazia", FOLDER)
    client = MetadataClient({"root123": root}, {
        ("root123", None): {"files": [sub], "nextPageToken": "page2"},
        ("root123", "page2"): {"files": [empty]},
        ("sub123", None): {"files": [metadata("video123", "Vídeo.mp4")]},
        ("empty123", None): {"files": []},
    })
    plan = client.plan("https://drive.google.com/drive/folders/root123", tmp_path, Control())
    assert plan.directories == ["Vídeos", "Vazia"]
    assert plan.files[0].relative_path == "Vídeos/Vídeo.mp4"


def test_download_blocked_detected_before_any_transfer(tmp_path):
    blocked = metadata("abcde", "Confidencial.mp4", capabilities={"canDownload": False})
    client = MetadataClient({"abcde": blocked}, {})
    with pytest.raises(DownloadError, match="bloqueou"):
        client.plan("https://drive.google.com/file/d/abcde/view", tmp_path, Control())


@pytest.mark.parametrize("names", [("A.mp4", "a.mp4"), ("igual.mp4", "igual.mp4"), ("video.mp4", "CON.mp4"), ("video.mp4", "a/b.mp4")])
def test_duplicate_and_windows_incompatible_names_fail_preflight(tmp_path, names):
    client = MetadataClient({"root123": metadata("root123", "Root", FOLDER)}, {
        ("root123", None): {"files": [metadata("file001", names[0]), metadata("file002", names[1])]}})
    with pytest.raises(DownloadError):
        client.plan("https://drive.google.com/drive/folders/root123", tmp_path, Control())


def test_native_document_export_and_non_resumable(tmp_path):
    client = MetadataClient({"abcde": metadata("abcde", "Contrato", "application/vnd.google-apps.document")}, {})
    plan = client.plan("https://docs.google.com/document/d/abcde/edit", tmp_path, Control())
    item = plan.files[0]
    assert item.relative_path == "Contrato.docx" and item.export_mime and item.size is None
    assert not item.resumable


def test_non_exportable_google_item_is_not_silently_omitted(tmp_path):
    client = MetadataClient({"abcde": metadata("abcde", "Formulário", "application/vnd.google-apps.form")}, {})
    with pytest.raises(DownloadError, match="exportação compatível"):
        client.plan("https://drive.google.com/file/d/abcde/view", tmp_path, Control())


def test_request_only_get_and_no_redirect_token_forwarding():
    class Session:
        def __init__(self): self.calls = []
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return response(200, {})
    session = Session()
    client = DriveClient(Auth(), session)
    client._wait = lambda *args: None
    result = client._request("/files/abcde", key="0-key", identifier="abcde", headers={"Range": "bytes=100-"})
    result.close()
    url, args = session.calls[0]
    assert url.startswith("https://www.googleapis.com/drive/v3/")
    assert args["headers"]["X-Goog-Drive-Resource-Keys"] == "abcde/0-key"
    assert args["headers"]["Range"] == "bytes=100-"
    assert args["stream"] and not args["allow_redirects"]


def test_401_refresh_once_and_429_retry_backoff():
    class Session:
        def __init__(self): self.values = [401, 429, 200]
        def get(self, *args, **kwargs): return response(self.values.pop(0), {"name": "Root"}, {"Retry-After": "3"})
    auth = Auth()
    client = DriveClient(auth, Session())
    waits = []
    client._wait = lambda delay, control=None: waits.append(delay)
    assert client._metadata("abcde")["name"] == "Root"
    assert True in auth.forces and any(delay >= 3 for delay in waits)


def test_api_403_never_retries_forever():
    class Session:
        def get(self, *args, **kwargs): return response(403, {"error": "insufficientFilePermissions"})
    client = DriveClient(Auth(), Session())
    client._wait = lambda *a: None
    with pytest.raises(RemoteError, match="permissão") as error:
        client._metadata("abcde")
    assert not error.value.retryable


def test_oauth_decoding_and_redaction():
    token = {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh"}
    assert decode_authorization("--->\n" + json.dumps(token) + "\n<---End paste") == token
    result = redact('Bearer hidden-access refresh_token=hidden-refresh https://example.com/key')
    assert "hidden-access" not in result and "hidden-refresh" not in result and "example.com" not in result


def test_cancel_preflight_before_any_request(tmp_path):
    control = Control()
    control.pause()
    client = MetadataClient({}, {})
    from drive_app.models import Paused
    with pytest.raises(Paused):
        client.plan("https://drive.google.com/file/d/abcde/view", tmp_path, control)
    assert not client.calls


@pytest.mark.skipif(__import__('os').name != 'nt', reason='DPAPI requer Windows')
def test_windows_dpapi_roundtrip_and_tampering():
    encrypted = dpapi(b'synthetic test token')
    assert encrypted != b'synthetic test token'
    assert dpapi(encrypted, decrypt=True) == b'synthetic test token'
    with pytest.raises(AppError): dpapi(encrypted[:8], decrypt=True)
