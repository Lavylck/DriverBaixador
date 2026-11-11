"""Verificação real dos binários Windows produzidos; não gera executáveis fictícios."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


def pe_machine(path: Path) -> int:
    with path.open("rb") as file:
        if file.read(2) != b"MZ":
            raise ValueError(f"Não é um executável PE: {path.name}")
        file.seek(0x3C)
        header = struct.unpack("<I", file.read(4))[0]
        file.seek(header)
        if file.read(4) != b"PE\x00\x00":
            raise ValueError(f"Cabeçalho PE inválido: {path.name}")
        return struct.unpack("<H", file.read(2))[0]


def verify(package: Path, smoke: bool = False, installer: Path | None = None,
           install_smoke: bool = False) -> dict:
    if not package.is_dir():
        raise ValueError("A pasta do aplicativo não foi produzida.")
    app = package / "MeuXodoDrive.exe"
    rclone = package / "bin" / "rclone.exe"
    if pe_machine(app) != 0x8664 or pe_machine(rclone) != 0x8664:
        raise ValueError("O aplicativo e o Rclone devem ser executáveis Windows x64.")
    required = ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll", "qwindows.dll")
    for name in required:
        if not any(package.rglob(name)):
            raise ValueError(f"Dependência ausente: {name}")
    for name in ("README.md", "LICENSE", "THIRD_PARTY.md", "bin/RCLONE-LICENSE.txt",
                 "licenses/LGPL-3.0.txt", "licenses/GPL-3.0.txt"):
        if not (package / name).is_file():
            raise ValueError(f"Arquivo obrigatório ausente: {name}")
    with app.open("rb") as stream:
        app_digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {
        "platform": platform.platform(), "python": sys.version.split()[0],
        "commit": os.environ.get("GITHUB_SHA", "local"),
        "pe_x64": True, "qt_dependencies_present": True,
        "app_sha256": app_digest,
        "startup_smoke_test": "not_run", "real_google_oauth_test": "not_run",
        "real_google_download_test": "not_run",
    }
    if smoke:
        if os.name != "nt":
            raise ValueError("O teste do executável exige Windows real.")
        env = os.environ.copy()
        env.pop("QT_QPA_PLATFORM", None)
        result = subprocess.run([str(app.resolve()), "--smoke-test"], env=env,
                                cwd=package.resolve(), timeout=45, check=False)
        if result.returncode:
            raise ValueError(f"Inicialização falhou: código {result.returncode}")
        version = subprocess.run([str(rclone.resolve()), "version"], capture_output=True,
                                 text=True, timeout=20, check=True).stdout.splitlines()[0]
        report.update(startup_smoke_test="passed", rclone_version=version)
    if installer:
        # Inno Setup pode utilizar um carregador PE de 32 bits para instalar um app x64.
        if pe_machine(installer) not in (0x14C, 0x8664):
            raise ValueError("O instalador não é um executável PE válido.")
        report["installer_present"] = True
        report["installer_size"] = installer.stat().st_size
        if install_smoke:
            if os.name != "nt":
                raise ValueError("O teste do instalador exige Windows real.")
            with tempfile.TemporaryDirectory(prefix="meuxodo-installer-") as temporary:
                installed = Path(temporary) / "installed"
                log = Path(temporary) / "install.log"
                subprocess.run([str(installer.resolve()), "/VERYSILENT", "/SUPPRESSMSGBOXES",
                                "/NORESTART", "/SP-", "/NOICONS", "/TASKS=",
                                f"/DIR={installed}", f"/LOG={log}"],
                               timeout=120, check=True)
                try:
                    verify(installed, smoke=True)
                    report["installer_install_and_startup_test"] = "passed"
                finally:
                    uninstaller = installed / "unins000.exe"
                    if uninstaller.exists():
                        subprocess.run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES",
                                        "/NORESTART"], timeout=90, check=True)
    elif install_smoke:
        raise ValueError("Informe --installer para testar a instalação.")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package", type=Path)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--install-smoke-test", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.package, args.smoke_test, args.installer, args.install_smoke_test)
        if args.report:
            args.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"ERRO DE DISTRIBUIÇÃO: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
