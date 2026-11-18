"""O verificador deve rejeitar artefatos incompletos antes de publicar uma release."""
import struct
import os
from pathlib import Path

import pytest

from tools.verify_distribution import pe_machine, verify


def write_test_pe(path, machine=0x8664):
    # Cabeçalho sintético somente para testar o detector; não é um executável.
    content = bytearray(256)
    content[:2] = b"MZ"
    struct.pack_into("<I", content, 0x3C, 128)
    content[128:132] = b"PE\x00\x00"
    struct.pack_into("<H", content, 132, machine)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def test_rejects_text_renamed_to_exe(tmp_path):
    path = tmp_path / "false.exe"
    path.write_text("not an executable")
    with pytest.raises(ValueError, match="PE"):
        pe_machine(path)


def test_rejects_wrong_architecture(tmp_path):
    write_test_pe(tmp_path / "DriverDownloader.exe", 0x14C)
    write_test_pe(tmp_path / "bin/rclone.exe")
    with pytest.raises(ValueError, match="x64"):
        verify(tmp_path)


def test_rejects_missing_qt_dependencies(tmp_path):
    write_test_pe(tmp_path / "DriverDownloader.exe")
    write_test_pe(tmp_path / "bin/rclone.exe")
    with pytest.raises(ValueError, match="Qt6Core.dll"):
        verify(tmp_path)


def test_official_icons_are_valid():
    icons = Path(__file__).resolve().parents[1] / "assets" / "icons"
    assert (icons / "driver_downloader_icon.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    ico = (icons / "driver_downloader_icon.ico").read_bytes()
    assert ico[:4] == b"\x00\x00\x01\x00"
    assert struct.unpack_from("<H", ico, 4)[0] > 0


def test_windows_data_directory_remains_compatible():
    if os.name == "nt":
        from drive_app.storage import app_directory
        assert app_directory() == Path(os.environ["LOCALAPPDATA"]) / "DriveBaixador"


def test_distribution_version_matches_application():
    from drive_app import __version__

    root = Path(__file__).resolve().parents[1]
    assert __version__ == "2.1.0"
    assert f'#define AppVersion "{__version__}"' in (root / "tools/installer.iss").read_text(encoding="utf-8")
    assert f"'/DAppVersion={__version__}'" in (root / "tools/build-windows.ps1").read_text(encoding="utf-8")
    assert f'version="{__version__}.0"' in (root / "tools/windows.manifest").read_text(encoding="utf-8")
