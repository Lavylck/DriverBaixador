from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .models import DownloadError as AppError, Cancelled as Cancelled


@dataclass(frozen=True)
class DriveLink:
    id: str
    resource_key: str = ""


def parse_link(value: str) -> DriveLink:
    try:
        value = value.strip()
        parsed = urlparse(value)
        if len(value) > 4096 or parsed.scheme != "https" or parsed.hostname not in (
                "drive.google.com", "www.drive.google.com", "docs.google.com"):
            raise ValueError()
        if parsed.username or parsed.password or parsed.port not in (None, 443):
            raise ValueError()
        query = parse_qs(parsed.query)
        match = re.fullmatch(r"/(?:drive/(?:u/\d+/)?)?folders/([\w-]+)/?", parsed.path)
        if not match:
            match = re.fullmatch(r"/(?:file|document|spreadsheets|presentation|drawings)/d/([\w-]+)(?:/[^?]*)?", parsed.path)
        identifier = match.group(1) if match else ""
        if not identifier and parsed.path.rstrip("/") in ("/open", "/folderview", "/uc"):
            identifier = query.get("id", [""])[0]
        keys = [v for k, values in query.items() if k.lower() == "resourcekey" for v in values]
        key = keys[0] if keys else ""
        if len(keys) > 1 or len(query.get("id", [])) > 1:
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9_-]{5,256}", identifier):
            raise ValueError()
        if key and not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", key):
            raise ValueError()
        return DriveLink(identifier, key)
    except (ValueError, TypeError, AttributeError):
        raise AppError("Cole um link HTTPS válido de arquivo ou pasta do Google Drive.") from None


def windows_parts(value: str) -> tuple[str, ...]:
    parts = tuple(value.split("/"))
    if not value or any(p in ("", ".", "..") for p in parts):
        raise AppError("O Drive retornou um caminho inválido.")
    for part in parts:
        reserved = part.split(".")[0].upper()
        if (re.search(r'[<>:"\\|?*\x00-\x1f]', part) or part.endswith((" ", "."))
                or reserved in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", reserved)
                or len(part.encode("utf-16-le")) // 2 > 255):
            raise AppError(f"O nome {part!r} não pode ser preservado no Windows. A origem não foi alterada.")
    return parts


def reject_links(path: Path) -> None:
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise AppError("Escolha um destino sem links simbólicos ou junções.")


def safe_component(name: str) -> str:
    if "/" in name:
        raise AppError(f"O nome {name!r} contém uma barra e não pode ser preservado no Windows.")
    windows_parts(name)
    return name
