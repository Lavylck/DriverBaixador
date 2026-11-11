"""Bounded-memory HTTP transfers with durable byte-range checkpoints."""
from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import random
import re
import shutil
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path

import requests

from .models import Cancelled, Control, DownloadError, Paused, Plan, RemoteFile
from .state import Store

CHUNK = 64 * 1024
RESERVE = 16 * 1024 * 1024
CACHE_NAME = ".drive-baixador-partials"
LOG = logging.getLogger(__name__)


def reject_links(path: Path) -> None:
    for candidate in (path, *path.parents):
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise DownloadError("O destino não pode conter links simbólicos ou junções do Windows.")


def safe_target(root: Path, relative: str) -> Path:
    parts = relative.split("/")
    if not parts or parts[0].casefold() == CACHE_NAME.casefold():
        raise DownloadError("Um nome da origem conflita com a pasta temporária do aplicativo.")
    for part in parts:
        stem = part.split(".")[0].upper()
        if (not part or part in (".", "..") or re.search(r'[<>:"\\|?*\x00-\x1f]', part)
                or part.endswith((" ", ".")) or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
                or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem)
                or len(part.encode("utf-16-le")) // 2 > 255):
            raise DownloadError(f"O nome {part!r} não pode ser preservado no Windows. Nenhum item do Drive foi alterado.")
    target = root.joinpath(*parts)
    reject_links(target)
    return target


def validate_plan(plan: Plan, destination: Path) -> None:
    seen: dict[str, bool] = {}
    for relative, is_dir in ([(p, True) for p in plan.directories]
                              + [(f.relative_path, False) for f in plan.files]):
        target = safe_target(destination, relative)
        key = relative.casefold()
        if key in seen:
            raise DownloadError(f"Há nomes duplicados ou que diferem apenas em maiúsculas: {relative!r}. Escolha uma origem sem esse conflito.")
        seen[key] = is_dir
        if target.exists() and target.is_dir() != is_dir:
            raise DownloadError(f"Conflito entre arquivo e pasta no destino: {relative!r}.")
    for key in seen:
        parent = key.rsplit("/", 1)[0] if "/" in key else ""
        while parent:
            if parent in seen and not seen[parent]:
                raise DownloadError("Um arquivo e uma pasta compartilham o mesmo caminho no destino.")
            parent = parent.rsplit("/", 1)[0] if "/" in parent else ""


class Bandwidth:
    """One limiter shared by all workers; zero means unlimited, units bytes/sec."""
    def __init__(self, limit: int = 0):
        self.lock = threading.Lock()
        self.limit = max(0, limit)
        self.next_time = time.monotonic()

    def configure(self, value: int) -> None:
        with self.lock:
            self.limit = max(0, value)
            self.next_time = time.monotonic()

    def consume(self, count: int, control: Control) -> None:
        with self.lock:
            if not self.limit:
                return
            now = time.monotonic()
            start = max(now, self.next_time)
            self.next_time = start + count / self.limit
            delay = self.next_time - now
        control.wait(delay)


def hashes(path: Path, control: Control) -> tuple[str, str]:
    md5 = hashlib.md5(usedforsecurity=False)
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            control.check()
            md5.update(block)
            sha.update(block)
    control.check()
    return md5.hexdigest(), sha.hexdigest()


def part_path(destination: Path, remote: RemoteFile) -> Path:
    cache = destination / CACHE_NAME
    reject_links(cache)
    cache.mkdir(exist_ok=True, mode=0o700)
    key = hashlib.sha256((remote.id + "\n" + remote.relative_path + "\n" + remote.signature).encode()).hexdigest()
    part = cache / (key + ".part")
    reject_links(part)
    return part


def interrupt_response(response) -> None:
    """Interrupt a blocked Requests read without waiting for its buffered lock.

    urllib3 exposes the live connection while streaming; depending on its
    version a socket may instead be reachable through HTTPResponse's file.
    Closing Response synchronously can deadlock on the active reader, so the
    worker alone closes it after shutdown wakes that reader.
    """
    raw = getattr(response, "raw", None)
    connection = getattr(raw, "_connection", None)
    sock = getattr(connection, "sock", None)
    if sock is None:
        fp = getattr(getattr(raw, "_fp", None), "fp", None)
        sock = getattr(getattr(fp, "raw", None), "_sock", None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def transfer_file(store: Store, identity: str, client, destination: Path, control: Control,
                  bandwidth: Bandwidth, progress=None, retries: int = 4) -> None:
    record = store.file(identity)
    remote = RemoteFile.from_dict(json.loads(record["remote"]))
    target = safe_target(destination, remote.relative_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(destination, remote)
    validator = record["validator"]
    last_report = 0.0
    sample_at, sample_bytes, speed = time.monotonic(), 0, 0.0

    def report(done: int, state: str = "running", force: bool = False) -> None:
        nonlocal last_report, sample_at, sample_bytes, speed
        now = time.monotonic()
        if force or now - last_report >= .2:
            delta_time = now - sample_at
            if delta_time >= .2:
                instantaneous = max(0, done - sample_bytes) / delta_time
                speed = instantaneous if not speed else speed * .65 + instantaneous * .35
                sample_at, sample_bytes = now, done
            store.update("files", identity, done=done, state=state)
            if progress:
                progress(identity, done, speed)
            last_report = now

    def complete(digest: str) -> None:
        actual = target.stat().st_size
        part.unlink(missing_ok=True)
        store.update("files", identity, done=actual, size=actual, state="completed", error="",
                     digest=digest, signature=remote.signature)
        if progress:
            progress(identity, actual, 0)

    def promote() -> None:
        reject_links(target)
        reject_links(part)
        md5, digest = hashes(part, control)
        if remote.md5 and md5.lower() != remote.md5.lower():
            part.unlink(missing_ok=True)
            store.update("files", identity, done=0, validator="")
            raise DownloadError("A verificação de integridade falhou. O arquivo será tentado novamente.", retryable=True)
        if not remote.md5:
            # With no remote checksum, at least reject a source that changed
            # while its content/export was being streamed.
            fresh = client.validate(remote)
            if fresh.signature != remote.signature:
                part.unlink(missing_ok=True)
                raise DownloadError("O arquivo mudou durante o download. A nova versão será verificada e baixada novamente.", retryable=True)
        control.check()
        os.replace(part, target)
        complete(digest)

    try:
        for attempt in range(retries):
            control.check()
            response = None
            unregister_abort = None
            try:
                fresh = client.validate(remote)
                control.check()
                if fresh.id != remote.id:
                    raise DownloadError("A identidade do arquivo mudou durante a transferência.")
                if fresh.signature != remote.signature:
                    part.unlink(missing_ok=True)
                    validator = ""
                    remote = replace(fresh, relative_path=remote.relative_path)
                    part = part_path(destination, remote)
                    store.update("files", identity, remote=json.dumps(remote.to_dict(), ensure_ascii=False),
                                 size=remote.size, signature=remote.signature, validator="", done=0, digest="")
                    LOG.info("Versão remota alterada; parcial reiniciado para o arquivo %s", remote.name)
                # Completed local files survive re-enqueue/restart, including exports.
                if target.is_file() and (remote.size is None or target.stat().st_size == remote.size):
                    known = store.known_digest(str(target), remote.signature)
                    if remote.md5 or known:
                        report(0, state="checking", force=True)
                        md5, digest = hashes(target, control)
                        reusable = (md5.lower() == remote.md5.lower()) if remote.md5 else bool(known and digest == known)
                        if reusable:
                            complete(digest)
                            return
                offset = part.stat().st_size if part.exists() else 0
                if not remote.resumable or (remote.size is not None and offset > remote.size):
                    part.unlink(missing_ok=True)
                    offset, validator = 0, ""
                if part.exists() and remote.size is not None and offset == remote.size and remote.md5:
                    promote()
                    return
                sample_at, sample_bytes = time.monotonic(), offset
                report(offset, force=True)
                needed = max(0, (remote.size or CHUNK) - offset)
                if shutil.disk_usage(destination).free < needed + RESERVE:
                    raise DownloadError("Não há espaço livre suficiente no destino. Libere espaço e clique em Continuar.")
                response = client.open(remote, offset, validator or None)
                unregister_abort = control.register_abort(lambda response=response: interrupt_response(response))
                control.check()
                status = response.status_code
                if status == 416:
                    part.unlink(missing_ok=True)
                    validator = ""
                    raise DownloadError("O servidor não aceitou o ponto de retomada. A transferência será reiniciada com segurança.", retryable=True)
                if status not in (200, 206):
                    raise DownloadError("O servidor recusou o download.", retryable=status in (408, 429, 500, 502, 503, 504), status=status)
                new_validator = response.headers.get("ETag", "")
                encoding = response.headers.get("Content-Encoding", "identity").lower()
                if encoding not in ("", "identity"):
                    raise DownloadError("O servidor devolveu uma codificação incompatível com a verificação dos bytes. Tente novamente.")
                if new_validator.startswith("W/"):
                    new_validator = ""
                response_total = remote.size
                if status == 206:
                    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", ""))
                    if not match:
                        part.unlink(missing_ok=True)
                        validator = ""
                        raise DownloadError("O servidor enviou uma faixa de bytes inválida. O download será reiniciado.", retryable=True)
                    start, end, total = map(int, match.groups())
                    if start != offset or end < start or end >= total or (remote.size is not None and total != remote.size):
                        part.unlink(missing_ok=True)
                        validator = ""
                        raise DownloadError("A faixa de retomada não corresponde ao arquivo. O download será reiniciado.", retryable=True)
                    if validator and new_validator and new_validator != validator:
                        part.unlink(missing_ok=True)
                        validator = ""
                        raise DownloadError("A versão do conteúdo mudou. O download será reiniciado.", retryable=True)
                    response_total = total
                elif offset:
                    # A 200 response to Range is the FULL file. Never append it.
                    LOG.info("Servidor sem suporte à retomada; reiniciando com segurança: %s", remote.name)
                    offset = 0
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        content_length = int(length)
                    except ValueError:
                        raise DownloadError("O servidor enviou um tamanho de conteúdo inválido.") from None
                    if content_length < 0:
                        raise DownloadError("O servidor enviou um tamanho de conteúdo inválido.")
                    if status == 206 and content_length != end - start + 1:
                        part.unlink(missing_ok=True)
                        validator = ""
                        raise DownloadError("O servidor enviou um comprimento de faixa inconsistente.", retryable=True)
                    if status == 200 and remote.size is not None and content_length != remote.size:
                        raise DownloadError("O tamanho do arquivo mudou no Google Drive. Refaça a verificação antes de continuar.")
                    if response_total is None:
                        response_total = offset + content_length
                validator = new_validator or validator
                store.update("files", identity, validator=validator, done=offset, state="running", error="")
                received = offset
                last_space_check = received
                expected_response = int(length) if length is not None else None
                if response_total is None and expected_response is None and response.headers.get("Transfer-Encoding", "").lower() != "chunked":
                    raise DownloadError("O servidor não informou o tamanho nem um término verificável da exportação. Não é seguro marcá-la como concluída; tente exportar pelo Google Drive.")
                response_received = 0
                reject_links(part)
                with part.open("ab" if offset else "wb") as stream:
                    try:
                        for block in response.iter_content(chunk_size=CHUNK):
                            control.check()
                            if not block:
                                continue
                            if response_total is not None and received + len(block) > response_total:
                                raise DownloadError("O servidor enviou mais dados que o tamanho esperado. O arquivo não foi concluído.")
                            bandwidth.consume(len(block), control)
                            if received - last_space_check >= 4 * 1024 * 1024:
                                if shutil.disk_usage(destination).free < len(block) + RESERVE:
                                    raise DownloadError("O disco ficou sem espaço durante o download. Libere espaço e continue.")
                                last_space_check = received
                            stream.write(block)
                            received += len(block)
                            response_received += len(block)
                            report(received)
                        stream.flush()
                        os.fsync(stream.fileno())
                    finally:
                        # A pause/cancellation closes and flushes the file before
                        # the worker transitions to its final state.
                        stream.flush()
                control.check()
                if expected_response is not None and response_received != expected_response:
                    raise DownloadError("A conexão terminou antes de receber todos os bytes. Retomando…", retryable=True)
                if response_total is not None and received != response_total:
                    raise DownloadError("O arquivo ainda está incompleto. Retomando os bytes restantes…", retryable=True)
                report(received, state="checking", force=True)
                promote()
                return
            except (Paused, Cancelled):
                raise
            except requests.RequestException:
                error = DownloadError("A conexão foi interrompida. O aplicativo tentará retomar automaticamente.", retryable=True)
            except DownloadError as exc:
                error = exc
            except OSError as exc:
                if exc.errno in (errno.ENOSPC, errno.EDQUOT):
                    error = DownloadError("Não há espaço suficiente no disco. Libere espaço e continue.")
                elif exc.errno in (errno.EACCES, errno.EPERM):
                    error = DownloadError("O Windows não permitiu gravar no destino. Confira a pasta e se o arquivo está aberto.")
                else:
                    error = DownloadError("Falha ao gravar ou verificar o arquivo local. Confira o disco e tente continuar.")
            finally:
                if unregister_abort is not None:
                    unregister_abort()
                if response is not None:
                    response.close()
            control.check()
            current = part.stat().st_size if part.exists() else 0
            store.update("files", identity, done=current, error=str(error), validator=validator)
            if not getattr(error, "retryable", False) or attempt == retries - 1:
                raise error
            delay = max(min(60, getattr(error, "retry_after", 0)), min(16, 2 ** attempt) + random.random() * .25)
            LOG.warning("Retentativa %d/%d: %s", attempt + 1, retries, str(error))
            control.wait(delay)
    except (Paused, Cancelled):
        current = part.stat().st_size if part.exists() else 0
        store.update("files", identity, done=current)
        raise
