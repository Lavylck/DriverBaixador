"""Domain types shared by the API, transfer engine and desktop interface."""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field


class DownloadError(Exception):
    def __init__(self, message: str, *, retryable: bool = False,
                 retry_after: float = 0, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after
        self.status = status


class Paused(Exception):
    pass


class Cancelled(Exception):
    pass


class Control:
    """Cooperative interruption that stops writes, hashes, throttles and retries."""
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._reason = ""
        self._aborters: dict[object, object] = {}

    @property
    def stopped(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    def pause(self) -> None:
        with self._lock:
            if self._reason != "cancelled":
                self._reason = "paused"
            self._event.set()
            aborters = list(self._aborters.values())
        self._abort(aborters)

    def cancel(self) -> None:
        with self._lock:
            self._reason = "cancelled"
            self._event.set()
            aborters = list(self._aborters.values())
        self._abort(aborters)

    @staticmethod
    def _abort(callbacks) -> None:
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def register_abort(self, callback):
        """Register a nonblocking I/O interrupter; returns an unregister callback.

        Callbacks must only interrupt transport (e.g. socket.shutdown), never
        acquire a buffered stream's read lock or wait for a worker.
        """
        token = object()
        with self._lock:
            self._aborters[token] = callback
            stopped = self._event.is_set()
        if stopped:
            self._abort([callback])

        def unregister():
            with self._lock:
                self._aborters.pop(token, None)

        return unregister

    def check(self) -> None:
        if self.stopped:
            if self.reason == "cancelled":
                raise Cancelled()
            raise Paused()

    def wait(self, seconds: float) -> None:
        self.check()
        if self._event.wait(max(0, seconds)):
            self.check()


@dataclass(frozen=True)
class RemoteFile:
    id: str
    name: str
    relative_path: str
    size: int | None
    md5: str = ""
    modified_time: str = ""
    resource_key: str = ""
    export_mime: str = ""
    version: str = ""
    resumable: bool = True

    @property
    def signature(self) -> str:
        # A resource key is an access credential, not a content version.
        fields = (self.id, self.size, self.md5.lower(), self.modified_time,
                  self.export_mime, self.version)
        return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict) -> RemoteFile:
        return cls(**value)


@dataclass
class Plan:
    files: list[RemoteFile] = field(default_factory=list)
    directories: list[str] = field(default_factory=list)
    name: str = "Download"
