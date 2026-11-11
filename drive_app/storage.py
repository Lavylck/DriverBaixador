from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .domain import AppError, reject_links


def app_directory() -> Path:
    if os.name == "nt":
        return Path(os.environ["LOCALAPPDATA"]) / "DriveBaixador"
    return Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "DriveBaixador"


def private_directory(path: Path) -> None:
    reject_links(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW}
        identity = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                                  capture_output=True, check=True, **flags)
        match = re.search(rb"S-1-5-[0-9-]+", identity.stdout)
        if not match:
            raise AppError("Não foi possível proteger a pasta de credenciais do Windows.")
        sid = "*" + match.group().decode("ascii")
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r",
                        f"{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"],
                       capture_output=True, check=True, **flags)
    else:
        path.chmod(0o700)


def dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise AppError("O armazenamento de credenciais usa DPAPI e requer Windows. A prévia da interface pode rodar em outros sistemas.")
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise AppError("O Windows não conseguiu abrir as credenciais. Use ‘Trocar conta’ para autorizar novamente.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)


class Vault:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / "oauth.dpapi"

    def load(self) -> bytes | None:
        return dpapi(self.path.read_bytes(), decrypt=True) if self.path.exists() else None

    def save(self, content: bytes) -> None:
        encrypted = dpapi(content)
        pending = self.path.with_suffix(".tmp")
        pending.write_bytes(encrypted)
        pending.replace(self.path)

    def forget(self) -> None:
        self.path.unlink(missing_ok=True)


class ConfigSession:
    def __init__(self, vault: Vault):
        self.vault = vault
        self.temp = tempfile.TemporaryDirectory(prefix="session-", dir=vault.root)
        self.root = Path(self.temp.name)
        self.config = self.root / "rclone.conf"
        try:
            self.config.write_bytes(vault.load() or b"")
            self.config.chmod(0o600)
        except Exception:
            self.temp.cleanup()
            raise

    def has_token(self) -> bool:
        parser = self.read()
        return parser.has_option("drive", "token")

    def read(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(self.config, encoding="utf-8")
        return parser

    def set_token(self, token: dict) -> None:
        import json
        parser = configparser.ConfigParser(interpolation=None)
        parser["drive"] = {"type": "drive", "scope": "drive.readonly",
                           "token": json.dumps(token, separators=(",", ":"))}
        with self.config.open("w", encoding="utf-8") as stream:
            parser.write(stream)
        self.save()

    def access_token(self) -> str:
        import json
        return json.loads(self.read()["drive"]["token"])["access_token"]

    def save(self) -> None:
        if self.has_token():
            self.vault.save(self.config.read_bytes())

    def close(self) -> None:
        try:
            self.save()
        finally:
            self.temp.cleanup()


def clean_stale_sessions(root: Path) -> None:
    # Chamar apenas depois de adquirir o bloqueio de instância única.
    for path in root.glob("session-*"):
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)

