from __future__ import annotations

import base64
import json
import re
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .domain import AppError
from .process import check_version, locate_rclone, run_process
from .storage import ConfigSession, Vault


def decode_authorization(output: str) -> dict:
    match = re.search(r"--->\s*(.*?)\s*<---End paste", output, re.S)
    try:
        if not match:
            raise ValueError()
        encoded = match.group(1).strip()
        data = json.loads(encoded) if encoded.startswith("{") else json.loads(
            base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        token = json.loads(data["token"]) if "token" in data else data
        if not token.get("access_token") or not token.get("refresh_token"):
            raise ValueError()
        return token
    except (ValueError, TypeError, KeyError):
        raise AppError("O Rclone não retornou uma autorização válida. Atualize-o e conclua o login no navegador.") from None


class RcloneAuth:
    """OAuth de leitura; configuração persistente protegida pelo DPAPI."""
    def __init__(self, root: Path):
        self.root = root
        self.vault = Vault(root)
        self._lock = threading.RLock()
        self._cached = None
        self._expiry = datetime.min.replace(tzinfo=timezone.utc)

    @property
    def connected(self) -> bool:
        return self.vault.path.is_file()

    def forget(self) -> None:
        with self._lock:
            self.vault.forget()
            self._cached = None

    def connect(self, cancel: threading.Event) -> None:
        self.token(cancel)

    def token(self, cancel=None, force: bool = False) -> str:
        cancel = cancel or threading.Event()
        with self._lock:
            if not force and self._cached and self._expiry > datetime.now(timezone.utc) + timedelta(seconds=120):
                if cancel.is_set():
                    from .models import Cancelled
                    raise Cancelled()
                return self._cached
            binary = locate_rclone()
            session = ConfigSession(self.vault)
            try:
                if not session.has_token():
                    check_version(binary, cancel)
                    blob = base64.urlsafe_b64encode(b'{"scope":"drive.readonly"}').decode().rstrip("=")
                    code, out = run_process([str(binary), "authorize", "drive", blob,
                                             "--config", str(session.config)], cancel, timeout=300)
                    if code:
                        raise AppError("A autorização Google não foi concluída. Confira o navegador e as políticas da conta.")
                    session.set_token(decode_authorization(out))
                parser = session.read()
                token = json.loads(parser["drive"]["token"])
                try:
                    expiry = datetime.fromisoformat(token.get("expiry", "").replace("Z", "+00:00"))
                except ValueError:
                    expiry = datetime.min.replace(tzinfo=timezone.utc)
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if force or expiry <= datetime.now(timezone.utc) + timedelta(seconds=120):
                    if force:
                        token["expiry"] = "2000-01-01T00:00:00Z"
                        parser["drive"]["token"] = json.dumps(token)
                        with session.config.open("w", encoding="utf-8") as stream:
                            parser.write(stream)
                    code, _ = run_process([str(binary), "about", "drive:", "--json",
                                           "--config", str(session.config)], cancel, timeout=60)
                    if code:
                        raise AppError("Não foi possível renovar a autorização. Use ‘Trocar conta’ nas configurações.")
                    session.save()
                token = json.loads(session.read()["drive"]["token"])
                self._cached = token["access_token"]
                try:
                    self._expiry = datetime.fromisoformat(token.get("expiry", "").replace("Z", "+00:00"))
                    if self._expiry.tzinfo is None:
                        self._expiry = self._expiry.replace(tzinfo=timezone.utc)
                except ValueError:
                    self._expiry = datetime.min.replace(tzinfo=timezone.utc)
                return self._cached
            finally:
                session.close()
