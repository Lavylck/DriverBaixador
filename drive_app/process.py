from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from .domain import AppError, Cancelled


MIN_RCLONE = (1, 68, 0)


def interrupt_helper(pid: int) -> int:
    """Executado em processo separado: não desconecta o console do usuário."""
    if os.name != "nt":
        return 1
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.FreeConsole()
    if not kernel.AttachConsole(pid):
        return 1
    try:
        kernel.SetConsoleCtrlHandler(None, True)
        ok = kernel.GenerateConsoleCtrlEvent(1, pid)  # CTRL_BREAK_EVENT
        time.sleep(0.15)
        return 0 if ok else 1
    finally:
        kernel.FreeConsole()


def stop_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            command = [sys.executable]
            if not getattr(sys, "frozen", False):
                command.append(str(Path(__file__).resolve().parents[1] / "main.py"))
            command += ["--interrupt", str(proc.pid)]
            subprocess.run(command, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(proc.pid, signal.SIGINT)
        proc.wait(timeout=8)
    except (OSError, subprocess.TimeoutExpired):
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait()


def run_process(command: list[str], cancel: threading.Event,
                on_line: Callable[[str], None] | None = None,
                timeout: float | None = None, capture: bool = True) -> tuple[int, str]:
    if cancel.is_set():
        raise Cancelled()
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("RCLONE_")}
    options = {}
    if os.name == "nt":
        info = subprocess.STARTUPINFO()
        info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        info.wShowWindow = 0
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE,
                   "startupinfo": info}
    else:
        options = {"start_new_session": True}
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, encoding="utf-8", errors="replace",
                            env=env, **options)
    lines: queue.Queue[str | None] = queue.Queue(maxsize=512)

    def read() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                lines.put(line)
        finally:
            lines.put(None)

    reader = threading.Thread(target=read, daemon=True, name="rclone-output")
    reader.start()
    output: list[str] = []
    started = time.monotonic()
    stopped = False
    timed_out = False
    try:
        while True:
            if not stopped and (cancel.is_set() or (timeout and time.monotonic() - started > timeout)):
                timed_out = not cancel.is_set()
                stop_process(proc)
                stopped = True
            try:
                line = lines.get(timeout=0.1)
            except queue.Empty:
                continue
            if line is None:
                break
            if capture:
                output.append(line)
            if on_line:
                on_line(line.rstrip("\r\n"))
        code = proc.wait()
    finally:
        if proc.poll() is None:
            stop_process(proc)
        # Drenar a fila libera o leitor mesmo se o callback lançar exceção.
        while reader.is_alive():
            try:
                lines.get(timeout=0.1)
            except queue.Empty:
                pass
        reader.join()
        if proc.stdout:
            proc.stdout.close()
    if cancel.is_set():
        raise Cancelled()
    if timed_out:
        raise AppError("A operação excedeu o tempo de espera. Verifique a conexão e tente novamente.")
    return code, "".join(output)


def locate_rclone() -> Path:
    import shutil
    root = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    choices = [root / "rclone.exe", root / "bin" / "rclone.exe"]
    choices += [root / "bin" / "rclone"] if os.name != "nt" else []
    for path in choices:
        if path.is_file():
            return path.resolve()
    found = shutil.which("rclone")
    if not found:
        raise AppError("Rclone não encontrado. Coloque rclone.exe em bin ou instale-o no PATH, conforme o LEIA-ME.")
    return Path(found).resolve()


def check_version(binary: Path, cancel: threading.Event) -> None:
    code, out = run_process([str(binary), "version"], cancel, timeout=15)
    match = re.search(r"rclone v(\d+)\.(\d+)\.(\d+)", out)
    if code or not match or tuple(map(int, match.groups())) < MIN_RCLONE:
        raise AppError("Instale o Rclone 1.68.0 ou posterior. Essa versão é necessária para proteger os arquivos parciais.")
