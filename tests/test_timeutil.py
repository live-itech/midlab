"""
tests/test_timeutil.py — Kontrak waktu lokal lab.

Yang dijaga di sini adalah tiga hal yang dulu bikin timestamp meleset 7 jam:
naive-ke-DB harus jam lokal, ISO keluar harus ber-offset, dan naive dari DB
tidak boleh diperlakukan sebagai UTC.
"""
from datetime import datetime, timedelta, timezone

import pytest

from lib import timeutil


@pytest.fixture(autouse=True)
def _clean_tz_cache():
    """Setiap test mulai tanpa cache, dan tidak mewariskan cache ke test lain."""
    timeutil.reset_lab_tz_cache()
    yield
    timeutil.reset_lab_tz_cache()


def test_default_tz_is_jakarta_offset():
    """Tanpa config apa pun, zona lab harus +07:00."""
    offset = timeutil.now().utcoffset()
    assert offset == timedelta(hours=7)


def test_now_naive_has_no_tzinfo_and_matches_local_wall_clock():
    """
    now_naive() adalah jam dinding lokal tanpa tzinfo — persis bentuk yang
    disimpan kolom DATETIME.
    """
    naive = timeutil.now_naive()
    assert naive.tzinfo is None

    expected = datetime.now(timezone.utc) + timedelta(hours=7)
    assert abs((naive - expected.replace(tzinfo=None)).total_seconds()) < 5


def test_now_naive_is_ahead_of_utc_by_seven_hours():
    """Regresi inti: yang masuk DB tidak boleh lagi berupa UTC."""
    naive = timeutil.now_naive()
    utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
    assert 6.9 * 3600 < (naive - utc_naive).total_seconds() < 7.1 * 3600


def test_to_local_treats_naive_as_already_local():
    """
    Naive dari DB sudah jam lokal. Memperlakukannya sebagai UTC akan menggeser
    tampilan 7 jam — justru bug yang sedang diperbaiki.
    """
    db_value = datetime(2026, 7, 22, 9, 30, 0)
    local = timeutil.to_local(db_value)

    assert local.hour == 9
    assert local.minute == 30
    assert local.utcoffset() == timedelta(hours=7)


def test_to_local_converts_aware_utc():
    """Aware UTC dikonversi, bukan sekadar ditempeli tzinfo."""
    aware_utc = datetime(2026, 7, 22, 2, 30, 0, tzinfo=timezone.utc)
    local = timeutil.to_local(aware_utc)

    assert local.hour == 9
    assert local.utcoffset() == timedelta(hours=7)


def test_to_local_passes_through_none():
    assert timeutil.to_local(None) is None


def test_isoformat_always_carries_offset():
    """
    Browser mem-parse ISO tanpa offset sebagai waktu lokal browser. Offset
    eksplisit menghilangkan tebakan itu.
    """
    iso = timeutil.isoformat(datetime(2026, 7, 22, 9, 30, 0))
    assert iso == "2026-07-22T09:30:00+07:00"


def test_isoformat_none_returns_none():
    """Kolom nullable bisa diteruskan langsung tanpa guard di pemanggil."""
    assert timeutil.isoformat(None) is None


def test_isoformat_no_args_is_now_with_offset():
    iso = timeutil.isoformat()
    assert iso.endswith("+07:00")


def test_stamp_is_local_wall_clock_for_instrument_messages():
    """Field timestamp ASTM/HL7 harus jam lokal, bukan UTC."""
    dt = datetime(2026, 7, 22, 2, 30, 0, tzinfo=timezone.utc)
    assert timeutil.stamp("%Y%m%d%H%M%S", dt) == "20260722093000"


def test_stamp_default_now_tracks_local_clock():
    assert timeutil.stamp("%H") == timeutil.now().strftime("%H")


def test_unknown_timezone_falls_back_to_plus_seven(monkeypatch):
    """
    Nama zona salah ketik atau tzdata tidak terpasang tidak boleh membuat
    service gagal start — harus jatuh ke offset tetap +07:00.
    """
    class _FakeConfig:
        def get(self, key, default=None):
            return "Not/ARealZone" if key == "timezone" else default

    monkeypatch.setattr("lib.config.Config", _FakeConfig)
    timeutil.reset_lab_tz_cache()

    assert timeutil.now().utcoffset() == timedelta(hours=7)


def test_timezone_is_configurable(monkeypatch):
    """Lab di WITA cukup ubah config, tidak menyentuh kode."""
    class _FakeConfig:
        def get(self, key, default=None):
            return "Asia/Makassar" if key == "timezone" else default

    monkeypatch.setattr("lib.config.Config", _FakeConfig)
    timeutil.reset_lab_tz_cache()

    assert timeutil.now().utcoffset() == timedelta(hours=8)


def test_config_failure_falls_back_without_raising(monkeypatch):
    """Config belum siap (unit test, probe importlib) tetap dapat zona default."""
    def _boom(*args, **kwargs):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("lib.config.Config", _boom)
    timeutil.reset_lab_tz_cache()

    assert timeutil.now().utcoffset() == timedelta(hours=7)
