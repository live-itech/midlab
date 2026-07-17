"""
Test get_logger() — terutama fallback saat direktori log tidak bisa ditulis.

Kondisi nyata di mesin dev: /var/log/midlab ADA (dibuat installer, milik user
`midlab`, mode 755) tapi tidak writable oleh user yang menjalankan test.
os.makedirs(exist_ok=True) sukses, yang gagal justru pembukaan file log.
"""

import logging
import os
import stat

import pytest

import lib.utils
from lib.utils import get_logger


@pytest.fixture
def dir_tak_writable(tmp_path):
    """Direktori yang ada tapi tidak bisa ditulis — mode 555."""
    d = tmp_path / "log-readonly"
    d.mkdir()
    d.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x, tanpa write
    yield str(d)
    d.chmod(stat.S_IRWXU)  # pulihkan agar tmp_path bisa dibersihkan


@pytest.fixture
def dir_fallback(tmp_path):
    return str(tmp_path / "fallback")


@pytest.fixture(autouse=True)
def bersihkan_logger():
    """Buang logger buatan test agar cache handler tidak bocor antar-test."""
    dibuat = []
    yield dibuat
    for nama in dibuat:
        lg = logging.getLogger(nama)
        for h in list(lg.handlers):
            h.close()
            lg.removeHandler(h)


def _file_handler_paths(logger) -> list:
    return [
        h.baseFilename for h in logger.handlers
        if isinstance(h, logging.FileHandler)
    ]


@pytest.mark.skipif(os.geteuid() == 0, reason="root menembus izin file")
def test_fallback_saat_direktori_ada_tapi_tidak_writable(
    monkeypatch, dir_tak_writable, dir_fallback, bersihkan_logger
):
    # Inti bug: makedirs(exist_ok=True) SUKSES karena direktori sudah ada,
    # sehingga fallback lama tidak pernah aktif dan pembukaan file meledak.
    monkeypatch.setattr(lib.utils, "LOG_DIR", dir_tak_writable)
    monkeypatch.setattr(lib.utils, "LOG_DIR_FALLBACK", dir_fallback)
    bersihkan_logger.append("midlab.svc_readonly")

    logger = get_logger("svc_readonly")

    paths = _file_handler_paths(logger)
    assert paths, "logger tidak punya file handler"
    assert paths[0].startswith(dir_fallback)


@pytest.mark.skipif(os.geteuid() == 0, reason="root menembus izin file")
def test_log_benar_benar_tertulis_setelah_fallback(
    monkeypatch, dir_tak_writable, dir_fallback, bersihkan_logger
):
    monkeypatch.setattr(lib.utils, "LOG_DIR", dir_tak_writable)
    monkeypatch.setattr(lib.utils, "LOG_DIR_FALLBACK", dir_fallback)
    bersihkan_logger.append("midlab.svc_tulis")

    logger = get_logger("svc_tulis")
    logger.error("pesan uji fallback")

    isi = open(_file_handler_paths(logger)[0]).read()
    assert "pesan uji fallback" in isi
    assert "[ERROR]" in isi


@pytest.mark.skipif(os.geteuid() == 0, reason="root menembus izin file")
def test_peringatan_fallback_dicetak_ke_stderr(
    monkeypatch, dir_tak_writable, dir_fallback, capsys, bersihkan_logger
):
    monkeypatch.setattr(lib.utils, "LOG_DIR", dir_tak_writable)
    monkeypatch.setattr(lib.utils, "LOG_DIR_FALLBACK", dir_fallback)
    bersihkan_logger.append("midlab.svc_warn")

    get_logger("svc_warn")

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert dir_tak_writable in err
    assert dir_fallback in err


def test_direktori_writable_dipakai_apa_adanya(
    monkeypatch, tmp_path, dir_fallback, bersihkan_logger
):
    # Jalur normal produksi tidak boleh ikut berubah.
    normal = str(tmp_path / "log-ok")
    monkeypatch.setattr(lib.utils, "LOG_DIR", normal)
    monkeypatch.setattr(lib.utils, "LOG_DIR_FALLBACK", dir_fallback)
    bersihkan_logger.append("midlab.svc_normal")

    logger = get_logger("svc_normal")

    paths = _file_handler_paths(logger)
    assert paths[0] == os.path.join(normal, "svc_normal.log")
    assert not os.path.exists(dir_fallback), "fallback tidak boleh dibuat"


def test_direktori_belum_ada_tetap_dibuat(
    monkeypatch, tmp_path, dir_fallback, bersihkan_logger
):
    # Perilaku lama yang harus tetap jalan: LOG_DIR belum ada → dibuat.
    baru = str(tmp_path / "belum" / "ada")
    monkeypatch.setattr(lib.utils, "LOG_DIR", baru)
    monkeypatch.setattr(lib.utils, "LOG_DIR_FALLBACK", dir_fallback)
    bersihkan_logger.append("midlab.svc_baru")

    logger = get_logger("svc_baru")

    assert _file_handler_paths(logger)[0].startswith(baru)


@pytest.mark.skipif(os.geteuid() == 0, reason="root menembus izin file")
def test_logger_per_instrument_ikut_fallback(
    monkeypatch, dir_tak_writable, dir_fallback, bersihkan_logger
):
    monkeypatch.setattr(lib.utils, "LOG_DIR", dir_tak_writable)
    monkeypatch.setattr(lib.utils, "LOG_DIR_FALLBACK", dir_fallback)
    bersihkan_logger.append("midlab.tcp_socket.9")

    logger = get_logger("tcp_socket", instrument_id=9)

    assert _file_handler_paths(logger)[0] == os.path.join(dir_fallback, "tcp_9.log")
