"""O verificador deve rejeitar artefatos incompletos antes de publicar uma release."""
import struct

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
    write_test_pe(tmp_path / "MeuXodoDrive.exe", 0x14C)
    write_test_pe(tmp_path / "bin/rclone.exe")
    with pytest.raises(ValueError, match="x64"):
        verify(tmp_path)


def test_rejects_missing_qt_dependencies(tmp_path):
    write_test_pe(tmp_path / "MeuXodoDrive.exe")
    write_test_pe(tmp_path / "bin/rclone.exe")
    with pytest.raises(ValueError, match="Qt6Core.dll"):
        verify(tmp_path)
