"""Actual 256MiB streaming transfer, with bounded Python memory measurement."""
import hashlib
import threading
import tracemalloc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from drive_app.models import Control, RemoteFile
from drive_app.state import Store
from drive_app.transfer import Bandwidth, transfer_file


def test_256_mib_stream_has_bounded_memory(tmp_path):
    block = bytes(range(256)) * 256  # 64KiB; never materialize the whole video
    blocks = 4096  # exactly 256MiB
    total = len(block) * blocks
    digest = hashlib.md5(usedforsecurity=False)
    for _ in range(blocks): digest.update(block)
    remote = RemoteFile('large-video', 'Video-grande.mp4', 'Video-grande.mp4', total, md5=digest.hexdigest(), version='1')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Length', str(total))
            self.end_headers()
            try:
                for _ in range(blocks): self.wfile.write(block)
            except (BrokenPipeError, ConnectionResetError): pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    class Client:
        def validate(self, item): return item
        def open(self, *args): return requests.get(f'http://127.0.0.1:{server.server_port}/video', stream=True, timeout=(3, 10))

    store = Store(tmp_path / 'state')
    destination = tmp_path / 'downloads'
    destination.mkdir()
    store.add_job('job', 'synthetic-link', str(destination))
    store.add_files('job', destination, [remote])
    identity = store.query('SELECT id FROM files')[0]['id']
    tracemalloc.start()
    try:
        transfer_file(store, identity, Client(), destination, Control(), Bandwidth())
        _, peak = tracemalloc.get_traced_memory()
        assert store.file(identity)['state'] == 'completed'
        assert (destination / remote.name).stat().st_size == total
        assert peak < 20 * 1024 * 1024, f'Peak Python allocations were {peak} bytes'
    finally:
        tracemalloc.stop()
        store.close()
        server.shutdown()
        server.server_close()
        thread.join()
        (destination / remote.name).unlink(missing_ok=True)
