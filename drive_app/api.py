from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath
from urllib.parse import quote

import requests

from .domain import parse_link, reject_links, safe_component, windows_parts
from .models import DownloadError, Plan, RemoteFile


API = "https://www.googleapis.com/drive/v3"
FOLDER = "application/vnd.google-apps.folder"
SHORTCUT = "application/vnd.google-apps.shortcut"
FIELDS = ("id,name,mimeType,size,md5Checksum,modifiedTime,version,resourceKey,trashed,"
          "capabilities(canDownload,canListChildren),exportLinks,"
          "shortcutDetails(targetId,targetMimeType,targetResourceKey)")
EXPORTS = {
    "application/vnd.google-apps.document": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"),
    "application/vnd.google-apps.spreadsheet": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "application/vnd.google-apps.presentation": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
    "application/vnd.google-apps.drawing": ("image/svg+xml", ".svg"),
}


class RemoteError(DownloadError):
    pass


def response_error(response) -> RemoteError:
    try:
        # Errors are bounded; never log the OAuth request or token.
        body = response.raw.read(65536, decode_content=True).decode("utf-8", errors="replace")
    except (AttributeError, OSError, TypeError):
        body = ""
    value = body.lower()
    status = response.status_code
    retryable = status in (408, 429, 500, 502, 503, 504) or (
        status == 403 and any(s in value for s in ("ratelimitexceeded", "userratelimitexceeded")))
    if status == 401:
        message = "A autorização expirou. Reconecte sua conta Google nas configurações."
    elif any(s in value for s in ("downloadquotaexceeded", "dailylimitexceeded", "exportsizelimitexceeded")):
        message = "O Google atingiu a cota de download ou o limite de exportação deste item. Aguarde ou escolha outro formato no Drive."
        retryable = False
    elif retryable:
        message = f"O Google está temporariamente indisponível ou limitou as requisições (HTTP {status})."
    elif status == 403:
        message = "A conta não tem permissão para baixar este conteúdo. Confira o compartilhamento e as restrições do proprietário."
    elif status == 404:
        message = "O item não foi encontrado ou não está acessível. Confira a conta, o link completo e a resource key."
    else:
        message = f"A API do Google recusou a solicitação (HTTP {status})."
    try:
        retry_after = min(60, max(0, float(response.headers.get("Retry-After", "0"))))
    except ValueError:
        retry_after = 0
    return RemoteError(message, retryable=retryable, retry_after=retry_after, status=status)


class _ControlEvent:
    def __init__(self, control):
        self.control = control

    def is_set(self):
        self.control.check()
        return False


class DriveClient:
    _pace_lock = threading.Lock()
    _next_request = 0.0

    def __init__(self, auth, session=None):
        self.auth = auth
        self.session = session or requests.Session()

    def close(self):
        self.session.close()

    @staticmethod
    def _wait(delay, control=None):
        if control:
            control.wait(delay)
        else:
            threading.Event().wait(delay)

    def _request(self, path, params=None, key="", identifier="", headers=None, control=None):
        if control:
            control.check()
        # Limite compartilhado entre os workers, sem acelerar com a concorrência.
        with self._pace_lock:
            now = time.monotonic()
            delay = max(0, self._next_request - now)
            type(self)._next_request = max(now, self._next_request) + 0.125
        self._wait(delay, control)
        request_headers = {"Accept-Encoding": "identity", **(headers or {})}
        if key:
            request_headers["X-Goog-Drive-Resource-Keys"] = f"{identifier}/{key}"
        event = _ControlEvent(control) if control else None
        for attempt in range(2):
            request_headers["Authorization"] = "Bearer " + self.auth.token(event, force=bool(attempt))
            response = self.session.get(API + path, params=params, headers=request_headers,
                                        stream=True, timeout=(10, 15), allow_redirects=False)
            # API media/export endpoints serve content directly. Do not forward
            # Authorization to a redirect destination supplied by a server.
            if response.status_code == 401 and attempt == 0:
                response.close()
                continue
            return response
        raise RemoteError("Não foi possível autorizar a solicitação.")

    def _json(self, path, params, identifier, key, control=None):
        for attempt in range(5):
            if control:
                control.check()
            try:
                with self._request(path, params, key, identifier, control=control) as response:
                    if response.status_code != 200:
                        raise response_error(response)
                    return response.json()
            except RemoteError as error:
                if not error.retryable or attempt == 4:
                    raise
                delay = max(error.retry_after, min(30, 0.5 * 2 ** attempt + random.random()))
                logging.getLogger(__name__).warning("Tentativa %s de metadados: %s", attempt + 1, error)
            except (requests.RequestException, ValueError):
                if attempt == 4:
                    raise RemoteError("Falha de conexão ao verificar o Drive. Confira a internet e tente novamente.", retryable=True) from None
                delay = min(30, 0.5 * 2 ** attempt + random.random())
            self._wait(delay, control)
        raise RemoteError("Não foi possível verificar o item.")

    def _metadata(self, identifier, key="", control=None):
        return self._json("/files/" + quote(identifier, safe=""),
                          {"fields": FIELDS, "supportsAllDrives": "true"}, identifier, key, control)

    def _resolve(self, metadata, key, control=None):
        seen = set()
        original_name = metadata["name"]
        while metadata.get("mimeType") == SHORTCUT:
            detail = metadata.get("shortcutDetails", {})
            target = detail.get("targetId")
            if not target or target in seen:
                raise DownloadError("Um atalho está quebrado ou contém um ciclo. Nenhum arquivo foi alterado no Drive.")
            seen.add(target)
            key = detail.get("targetResourceKey", "")
            metadata = self._metadata(target, key, control)
        if metadata.get("trashed"):
            raise DownloadError("Um dos itens está na lixeira do Drive.")
        metadata = {**metadata, "name": original_name}
        return metadata, metadata.get("resourceKey", key)

    def _remote_file(self, metadata, path, key):
        if metadata.get("capabilities", {}).get("canDownload") is False:
            raise DownloadError(f"O proprietário bloqueou o download de {metadata['name']!r}. A transferência não foi iniciada.")
        mime = metadata.get("mimeType", "")
        export_mime = ""
        if mime.startswith("application/vnd.google-apps."):
            if mime not in EXPORTS:
                raise DownloadError(f"O item {metadata['name']!r} não possui uma exportação compatível (tipo {mime}).")
            export_mime, extension = EXPORTS[mime]
            if not path.lower().endswith(extension):
                path += extension
            size = None
        else:
            if "size" not in metadata:
                raise DownloadError(f"O Google não informou o tamanho de {metadata['name']!r}; não é seguro concluir este download automaticamente.")
            size = int(metadata["size"])
            if size < 0:
                raise DownloadError("O Google informou um tamanho de arquivo inválido.")
        windows_parts(path)
        return RemoteFile(id=metadata["id"], name=PurePosixPath(path).name,
                          relative_path=path, size=size, md5=metadata.get("md5Checksum", "").lower(),
                          modified_time=metadata.get("modifiedTime", ""), resource_key=key,
                          export_mime=export_mime, version=str(metadata.get("version", "")),
                          resumable=not bool(export_mime))

    def plan(self, url, destination: Path, control) -> Plan:
        link = parse_link(url)
        reject_links(Path(destination).absolute())
        root, root_key = self._resolve(self._metadata(link.id, link.resource_key, control), link.resource_key, control)
        files, directories = [], []
        seen_paths = set()

        def reserve(path):
            windows_parts(path)
            canonical = path.casefold()
            if canonical in seen_paths:
                raise DownloadError(f"Há nomes duplicados ou que diferem apenas por maiúsculas: {path!r}. Não é possível preservar ambos no Windows.")
            seen_paths.add(canonical)
            target = Path(destination).joinpath(*path.split("/"))
            reject_links(target)

        if root.get("mimeType") != FOLDER:
            item = self._remote_file(root, safe_component(root["name"]), root_key)
            reserve(item.relative_path)
            return Plan(files=[item], directories=[], name=root["name"])

        def walk(folder, prefix, key, ancestors):
            control.check()
            identifier = folder["id"]
            if identifier in ancestors:
                raise DownloadError("Um atalho de pasta cria um ciclo. Escolha uma pasta sem esse ciclo.")
            if folder.get("capabilities", {}).get("canListChildren") is False:
                raise DownloadError(f"A conta não pode listar a pasta {folder['name']!r}.")
            page = None
            while True:
                control.check()
                params = {"q": f"'{identifier}' in parents and trashed = false", "pageSize": 1000,
                          "fields": "nextPageToken,incompleteSearch,files(" + FIELDS + ")",
                          "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
                if page:
                    params["pageToken"] = page
                data = self._json("/files", params, identifier, key, control)
                if data.get("incompleteSearch"):
                    raise DownloadError("O Google retornou uma listagem incompleta. Tente novamente antes de baixar.")
                for child in data.get("files", []):
                    control.check()
                    child, child_key = self._resolve(child, child.get("resourceKey", ""), control)
                    name = safe_component(child["name"])
                    path = f"{prefix}/{name}" if prefix else name
                    if child.get("mimeType") == FOLDER:
                        reserve(path)
                        directories.append(path)
                        walk(child, path, child_key, ancestors | {identifier})
                    else:
                        item = self._remote_file(child, path, child_key)
                        reserve(item.relative_path)
                        files.append(item)
                page = data.get("nextPageToken")
                if not page:
                    return

        walk(root, "", root_key, set())
        return Plan(files=files, directories=directories, name=root["name"])

    def validate(self, file: RemoteFile) -> RemoteFile:
        metadata = self._metadata(file.id, file.resource_key)
        if metadata.get("trashed"):
            raise DownloadError("O arquivo foi movido para a lixeira. A cópia não será concluída.")
        fresh = self._remote_file(metadata, file.relative_path, metadata.get("resourceKey", file.resource_key))
        return replace(fresh, relative_path=file.relative_path)

    def open(self, file: RemoteFile, offset: int, validator: str | None = None):
        if file.export_mime:
            if offset:
                raise DownloadError("Documentos Google exportados não permitem retomar do mesmo byte; reinicie essa exportação.")
            path, params = f"/files/{quote(file.id, safe='')}/export", {"mimeType": file.export_mime}
        else:
            path, params = f"/files/{quote(file.id, safe='')}", {"alt": "media", "supportsAllDrives": "true"}
        headers = {}
        if offset:
            headers["Range"] = f"bytes={offset}-"
            if validator:
                headers["If-Range"] = validator
        response = self._request(path, params, file.resource_key, file.id, headers)
        if response.status_code not in (200, 206, 416):
            error = response_error(response)
            response.close()
            raise error
        return response
