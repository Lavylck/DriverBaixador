"""Persistent download queue with controlled concurrency; independent of Qt."""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .models import Cancelled, Control, DownloadError, Paused
from .state import Store
from .transfer import RESERVE, Bandwidth, part_path, reject_links, safe_target, transfer_file, validate_plan

LOG = logging.getLogger(__name__)


class Manager:
    def __init__(self, root: Path, drive_factory, concurrency: int = 4, bandwidth_limit: int = 0):
        self.store = Store(Path(root))
        self.drive_factory = drive_factory
        self.concurrency = min(8, max(1, int(concurrency)))
        self.bandwidth = Bandwidth(bandwidth_limit)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.wake = threading.Event()
        self.workers = ThreadPoolExecutor(max_workers=8, thread_name_prefix="download")
        self.planners = ThreadPoolExecutor(max_workers=2, thread_name_prefix="drive-plan")
        self.active: dict[str, tuple[Control, object, str]] = {}
        self.planning: dict[str, Control] = {}
        self.metrics: dict[str, tuple[int, float]] = {}
        self.closed = False
        self.scheduler = threading.Thread(target=self._schedule, daemon=True, name="download-queue")
        self.scheduler.start()

    def set_limits(self, concurrency: int = 4, bandwidth_limit: int = 0) -> None:
        with self.lock:
            self.concurrency = min(8, max(1, int(concurrency)))
            self.bandwidth.configure(bandwidth_limit)
        self.wake.set()

    def add(self, url: str, destination: str | Path) -> str:
        if self.stopping.is_set():
            raise DownloadError("O aplicativo está encerrando.")
        selected = Path(destination).expanduser().absolute()
        reject_links(selected)
        root = Path(os.path.abspath(selected))
        reject_links(root)
        if not url.strip():
            raise DownloadError("Cole o link de um arquivo ou pasta do Google Drive.")
        identity = uuid.uuid4().hex
        self.store.add_job(identity, url.strip(), str(root))
        self._start_plan(identity)
        return identity

    def _start_plan(self, identity: str) -> None:
        with self.lock:
            if identity in self.planning or self.stopping.is_set():
                return
            control = Control()
            self.planning[identity] = control
        self.store.update("jobs", identity, state="planning", error="")
        self.planners.submit(self._plan, identity, control)

    def _plan(self, identity: str, control: Control) -> None:
        try:
            job = self.store.job(identity)
            destination = Path(job["destination"])
            control.check()
            reject_links(destination)
            destination.mkdir(parents=True, exist_ok=True)
            if not destination.is_dir():
                raise DownloadError("O destino precisa ser uma pasta.")
            client = self.drive_factory()
            try:
                plan = client.plan(job["url"], destination, control)
            finally:
                close = getattr(client, "close", None)
                if close:
                    close()
            control.check()
            validate_plan(plan, destination)
            required = 0
            for remote in plan.files:
                target = safe_target(destination, remote.relative_path)
                # Existing complete-size files are verified before network I/O.
                if remote.size is not None and target.is_file() and target.stat().st_size == remote.size:
                    continue
                partial = part_path(destination, remote)
                offset = partial.stat().st_size if partial.exists() and remote.resumable else 0
                required += max(0, (remote.size or 0) - offset)
            if shutil.disk_usage(destination).free < required + RESERVE:
                raise DownloadError("Não há espaço livre suficiente para esta fila. Escolha outro destino ou libere espaço.")
            control.check()
            for relative in plan.directories:
                safe_target(destination, relative).mkdir(parents=True, exist_ok=True)
            with self.lock:
                control.check()
                self.store.add_files(identity, destination, plan.files)
                self.store.update("jobs", identity, name=plan.name,
                                  state="queued" if plan.files else "completed", error="")
        except Paused:
            self.store.update("jobs", identity, state="paused", error="")
        except Cancelled:
            self.store.update("jobs", identity, state="cancelled", error="")
        except DownloadError as exc:
            self.store.update("jobs", identity, state="error", error=str(exc))
            LOG.warning("Falha na verificação da origem: %s", str(exc))
        except Exception as exc:
            self.store.update("jobs", identity, state="error",
                              error="Não foi possível preparar o download. Confira a conexão, o destino e a autorização.")
            LOG.error("Falha de planejamento (%s)", type(exc).__name__)
        finally:
            with self.lock:
                self.planning.pop(identity, None)
            self.wake.set()

    def pause(self, identity: str) -> None:
        with self.lock:
            job = self.store.job(identity)
            if job["state"] in ("completed", "cancelled"):
                return
            # 'pausing' is honest while a socket read/hash is still unwinding.
            busy = identity in self.planning
            if identity in self.planning:
                self.planning[identity].pause()
            for control, _, job_id in self.active.values():
                if job_id == identity:
                    control.pause()
                    busy = True
            self.store.update("jobs", identity, state="pausing" if busy else "paused")
            self.store.execute("UPDATE files SET state='paused' WHERE job_id=? AND state='queued'", (identity,))
        self.wake.set()

    def cancel(self, identity: str) -> None:
        with self.lock:
            job = self.store.job(identity)
            if job["state"] == "completed":
                return
            busy = identity in self.planning
            if identity in self.planning:
                self.planning[identity].cancel()
            for control, _, job_id in self.active.values():
                if job_id == identity:
                    control.cancel()
                    busy = True
            self.store.update("jobs", identity, state="cancelling" if busy else "cancelled")
            self.store.execute("UPDATE files SET state='cancelled' WHERE job_id=? AND state IN ('queued','paused')", (identity,))
        self.wake.set()

    def resume(self, identity: str) -> None:
        with self.lock:
            if self.stopping.is_set():
                return
            job = self.store.job(identity)
            if job["state"] in ("running", "queued", "planning", "pausing", "cancelling", "completed"):
                return
            count = self.store.query("SELECT COUNT(*) AS n FROM files WHERE job_id=?", (identity,))[0]["n"]
            if count:
                self.store.execute("UPDATE files SET state='queued',error='' WHERE job_id=? AND state<>'completed'", (identity,))
                self.store.update("jobs", identity, state="queued", error="")
            else:
                self._start_plan(identity)
        self.wake.set()

    def _metric(self, identity: str, done: int, speed: float) -> None:
        with self.lock:
            self.metrics[identity] = (done, speed)

    def _run(self, identity: str, job_id: str, control: Control) -> None:
        client = None
        try:
            job = self.store.job(job_id)
            client = self.drive_factory()
            transfer_file(self.store, identity, client, Path(job["destination"]), control,
                          self.bandwidth, self._metric)
        except Paused:
            self.store.update("files", identity, state="paused", error="")
        except Cancelled:
            self.store.update("files", identity, state="cancelled", error="")
        except DownloadError as exc:
            self.store.update("files", identity, state="error", error=str(exc))
            LOG.warning("Download interrompido: %s", str(exc))
        except Exception as exc:
            self.store.update("files", identity, state="error",
                              error="Falha inesperada durante a transferência. Consulte os registros e clique em Continuar.")
            LOG.error("Falha de transferência (%s)", type(exc).__name__)
        finally:
            if client:
                close = getattr(client, "close", None)
                if close:
                    close()
            with self.lock:
                self.metrics.pop(identity, None)
            self.wake.set()

    def _aggregate(self) -> None:
        for item in self.store.query("SELECT id FROM jobs WHERE state IN ('queued','running','pausing','cancelling')"):
            with self.lock:
                job = self.store.job(item["id"])
                if job["state"] not in ("queued", "running", "pausing", "cancelling"):
                    continue
                counts = {r["state"]: r["n"] for r in self.store.query("SELECT state,COUNT(*) AS n FROM files WHERE job_id=? GROUP BY state", (job["id"],))}
                busy = any(job_id == job["id"] for _, _, job_id in self.active.values()) or job["id"] in self.planning
                if job["state"] in ("pausing", "cancelling"):
                    if not busy:
                        self.store.update("jobs", job["id"], state="paused" if job["state"] == "pausing" else "cancelled")
                elif counts and not busy and not any(counts.get(s) for s in ("running", "checking", "queued")):
                    if counts.get("error"):
                        errors = self.store.query("SELECT error FROM files WHERE job_id=? AND state='error' LIMIT 1", (job["id"],))
                        self.store.update("jobs", job["id"], state="error", error=errors[0]["error"] if errors else "")
                    elif counts.get("cancelled"):
                        self.store.update("jobs", job["id"], state="cancelled")
                    elif counts.get("paused"):
                        self.store.update("jobs", job["id"], state="paused")
                    else:
                        self.store.update("jobs", job["id"], state="completed", error="")

    def _schedule(self) -> None:
        while not self.stopping.is_set():
            try:
                with self.lock:
                    for identity, (_, future, _) in list(self.active.items()):
                        if future.done():
                            self.active.pop(identity)
                    slots = self.concurrency - len(self.active)
                    if slots > 0:
                        candidates = self.store.query("""SELECT f.id,f.job_id,f.destination FROM files f
                            JOIN jobs j ON j.id=f.job_id WHERE f.state='queued'
                            AND j.state IN ('queued','running') ORDER BY j.created,f.rowid LIMIT 100""")
                        occupied = {self.store.file(fid)["destination"].casefold() for fid in self.active}
                        for file in candidates:
                            if slots <= 0:
                                break
                            if file["destination"].casefold() in occupied:
                                continue
                            control = Control()
                            self.store.update("files", file["id"], state="running", error="")
                            self.store.update("jobs", file["job_id"], state="running", error="")
                            future = self.workers.submit(self._run, file["id"], file["job_id"], control)
                            self.active[file["id"]] = (control, future, file["job_id"])
                            occupied.add(file["destination"].casefold())
                            slots -= 1
                self._aggregate()
            except Exception as exc:
                LOG.error("Falha no agendamento (%s)", type(exc).__name__)
            self.wake.wait(.1)
            self.wake.clear()

    def snapshot(self) -> dict:
        jobs = self.store.query("""SELECT j.*,COUNT(f.id) AS file_count,
            SUM(CASE WHEN f.state='completed' THEN 1 ELSE 0 END) AS completed_files,
            COALESCE(SUM(f.done),0) AS done,
            CASE WHEN SUM(CASE WHEN f.id IS NOT NULL AND f.size IS NULL THEN 1 ELSE 0 END)>0 THEN NULL
                 ELSE COALESCE(SUM(f.size),0) END AS size
            FROM jobs j LEFT JOIN files f ON f.job_id=j.id GROUP BY j.id ORDER BY j.created DESC""")
        # Bound UI snapshots; the queue itself remains fully persisted in SQLite.
        files = self.store.query("""SELECT id,job_id,name,relative_path,destination,size,done,state,error,updated,remote
            FROM files ORDER BY CASE state WHEN 'running' THEN 0 WHEN 'checking' THEN 1
            WHEN 'queued' THEN 2 WHEN 'paused' THEN 3 ELSE 4 END, updated DESC LIMIT 1000""")
        with self.lock:
            metrics = dict(self.metrics)
        for file in files:
            metadata = json.loads(file.pop("remote"))
            file["resumable"] = bool(metadata.get("resumable", True))
            file["note"] = "" if file["resumable"] else "Documento Google: ao continuar, esta exportação reinicia do início. Arquivos já concluídos são preservados."
            file["speed"] = metrics.get(file["id"], (0, 0))[1] if file["state"] == "running" else 0
            file["eta"] = max(0, file["size"] - file["done"]) / file["speed"] if file["size"] is not None and file["speed"] > 0 else None
        speeds = {}
        for file in files:
            speeds[file["job_id"]] = speeds.get(file["job_id"], 0) + file["speed"]
        for job in jobs:
            job.pop("url", None)  # Shared links can contain resource keys.
            job["speed"] = speeds.get(job["id"], 0)
            job["eta"] = max(0, job["size"] - job["done"]) / job["speed"] if job["size"] is not None and job["speed"] > 0 else None
        return {"jobs": jobs, "files": files,
                "history": [job for job in jobs if job["state"] in ("completed", "cancelled", "error")]}

    def shutdown(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.closed = True
            self.stopping.set()
            for control in self.planning.values():
                control.pause()
            for control, _, _ in self.active.values():
                control.pause()
        self.wake.set()
        self.scheduler.join()
        self.planners.shutdown(wait=True, cancel_futures=False)
        self.workers.shutdown(wait=True, cancel_futures=False)
        self.store.execute("UPDATE jobs SET state='paused' WHERE state IN ('queued','running','planning','pausing')")
        self.store.execute("UPDATE files SET state='paused' WHERE state IN ('queued','running','checking')")
        self.store.close()
