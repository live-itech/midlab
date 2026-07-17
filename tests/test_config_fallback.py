"""
Test Config._load() — fallback saat file config tidak bisa dibaca.

Sekelas dengan bug get_logger(): kode lama hanya menangani "file tidak ada",
padahal kasus nyata di mesin dev adalah file ADA tapi tidak readable
(/etc/midlab/config.yaml milik root/midlab).
"""

import os
import stat

import pytest

from lib.config import Config


@pytest.fixture(autouse=True)
def reset_singleton():
    Config.reset()
    yield
    Config.reset()


@pytest.fixture
def config_tak_terbaca(tmp_path):
    """File config yang ada tapi tidak punya izin baca."""
    f = tmp_path / "config.yaml"
    f.write_text("logging:\n  level: DEBUG\n")
    f.chmod(0)
    yield str(f)
    f.chmod(stat.S_IRUSR | stat.S_IWUSR)


@pytest.mark.skipif(os.geteuid() == 0, reason="root menembus izin file")
def test_config_tak_terbaca_jatuh_ke_default(config_tak_terbaca, capsys):
    config = Config(config_path=config_tak_terbaca)

    # Jatuh ke default, bukan melempar PermissionError.
    assert config.get("logging.level") == "INFO"
    assert config.get("database.port") == 3306

    err = capsys.readouterr().err
    assert "WARNING" in err
    assert config_tak_terbaca in err


def test_config_tidak_ada_tetap_jatuh_ke_default(tmp_path, capsys):
    # Perilaku lama yang harus tetap jalan.
    config = Config(config_path=str(tmp_path / "hilang.yaml"))

    assert config.get("logging.level") == "INFO"
    assert "WARNING" in capsys.readouterr().err


def test_config_terbaca_dipakai_apa_adanya(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("logging:\n  level: DEBUG\n")

    config = Config(config_path=str(f))

    assert config.get("logging.level") == "DEBUG"


def test_yaml_rusak_tetap_error_keras(tmp_path):
    # Config ADA dan TERBACA tapi isinya salah = kesalahan operator yang harus
    # kelihatan, bukan didiamkan dengan default.
    f = tmp_path / "config.yaml"
    f.write_text("logging:\n  level: [unclosed\n")

    with pytest.raises(ValueError):
        Config(config_path=str(f))
