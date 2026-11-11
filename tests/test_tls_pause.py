"""Exercise cancellation through an actual verified TLS socket, not a fake stream."""
import hashlib
import shutil
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from drive_app.manager import Manager
from drive_app.models import Plan, RemoteFile


def test_verified_https_trickle_can_be_paused_before_chunk_fills(tmp_path):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("OpenSSL executable is required to create the isolated TLS test certificate")
    key, cert = tmp_path / "server.key", tmp_path / "server.crt"
    generated = subprocess.run([
        openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=127.0.0.1",
        "-addext", "subjectAltName=IP:127.0.0.1",
    ], capture_output=True, timeout=20)
    if generated.returncode:
        pytest.skip("Installed OpenSSL could not generate an IP SAN test certificate")

    stopping, opened = threading.Event(), threading.Event()
    body = b"v" * (128 * 1024)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                while not stopping.is_set():
                    self.wfile.write(b"v")
                    self.wfile.flush()
                    stopping.wait(.05)
            except OSError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(cert), str(key))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    file = RemoteFile("tls-file", "video.mp4", "video.mp4", len(body),
                      md5=hashlib.md5(body).hexdigest(), version="1")

    class Client:
        def __init__(self):
            self.session = requests.Session()
            self.session.trust_env = False  # Isolated localhost; no proxy or CA overrides.

        def plan(self, *_args):
            return Plan([file], [], "TLS test")

        def validate(self, remote):
            return remote

        def open(self, *_args):
            response = self.session.get(f"https://127.0.0.1:{server.server_port}/video",
                                        stream=True, verify=str(cert), timeout=(2, 15))
            opened.set()
            return response

        def close(self):
            self.session.close()

    destination = tmp_path / "downloads"
    manager = Manager(tmp_path / "state", Client)
    try:
        identity = manager.add("local-tls-fixture", destination)
        assert opened.wait(5), manager.snapshot()
        started = time.monotonic()
        manager.pause(identity)
        while time.monotonic() - started < 2:
            snapshot = manager.snapshot()
            if snapshot["jobs"][0]["state"] == "paused":
                break
            time.sleep(.01)
        assert snapshot["jobs"][0]["state"] == "paused", snapshot
        assert time.monotonic() - started < 2
        assert snapshot["files"][0]["done"] < 65536
        assert not (destination / "video.mp4").exists()
    finally:
        stopping.set()
        server.shutdown()
        server.server_close()
        manager.shutdown()
        serving.join(timeout=2)
