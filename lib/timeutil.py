"""
lib/timeutil.py — Sumber tunggal waktu lokal lab untuk MidLab.

Seluruh MidLab bekerja pada waktu lokal lab (default Asia/Jakarta, WIB +07:00),
bukan UTC. Alasannya:

- Alat lab (Sysmex, Cobas, Mindray, dst) emit dan mengharapkan jam dinding lokal
  tanpa offset. Menstempel pesan keluar dengan UTC membuat jam di alat meleset
  7 jam.
- Kolom DATETIME MySQL tidak menyimpan offset. Aware datetime yang diserahkan ke
  sana kehilangan tzinfo-nya secara diam-diam, jadi yang tersimpan adalah jam
  dinding apa adanya. Kalau jam dinding itu UTC sementara console menampilkannya
  sebagai lokal, semua timestamp terbaca mundur 7 jam.
- Log file memakai time.localtime, jadi log sudah lokal. Menyimpan DB dalam UTC
  membuat log dan DB tidak bisa dikorelasikan.

Kontrak modul ini:
- `now()`       → aware, untuk perhitungan dan serialisasi.
- `now_naive()` → naive jam dinding lokal; SATU-SATUNYA yang boleh masuk kolom
                  DATETIME.
- `to_local()`  → naive dari DB dianggap sudah lokal, aware dikonversi.
- `isoformat()` → ISO8601 selalu ber-offset (+07:00), supaya konsumen (browser,
                  EazyApp) tidak perlu menebak zona waktu.
- `stamp()`     → jam dinding lokal terformat untuk field ASTM/HL7.

Zona waktu diambil dari config key `timezone` (default "Asia/Jakarta").
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone, tzinfo

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9; tidak terjadi di target 3.10+
    ZoneInfo = None


# Default zona waktu lab bila config tidak menyebutkan apa-apa.
DEFAULT_TIMEZONE = "Asia/Jakarta"

# Fallback bila paket tzdata tidak terpasang (container minimal) atau nama zona
# di config tidak dikenal. WIB tidak punya DST, jadi offset tetap +07:00 setara
# dengan Asia/Jakarta.
_FALLBACK_TZ = timezone(timedelta(hours=7), "WIB")

# Pembeda "argumen tidak diberikan" dari "diberikan tapi None". Keduanya harus
# berperilaku berbeda di isoformat(): tanpa argumen artinya sekarang, None
# artinya kolom DB kosong dan harus tetap None.
_UNSET = object()

_cached_tz: tzinfo | None = None


def get_lab_tz() -> tzinfo:
    """
    Zona waktu lab dari config (`timezone`), di-cache setelah resolve pertama.

    Import Config sengaja ditunda ke dalam fungsi: lib/config.py harus bebas
    dari dependensi modul ini, dan protocols/ kadang di-import tanpa config yang
    valid (unit test, probe importlib). Fallback ke +07:00 memastikan modul ini
    tidak pernah jadi penyebab service gagal start.
    """
    global _cached_tz
    if _cached_tz is not None:
        return _cached_tz

    name = DEFAULT_TIMEZONE
    try:
        from lib.config import Config

        name = Config().get("timezone", DEFAULT_TIMEZONE) or DEFAULT_TIMEZONE
    except Exception:
        pass

    if ZoneInfo is not None:
        try:
            _cached_tz = ZoneInfo(name)
            return _cached_tz
        except Exception:
            # Nama zona tidak dikenal, atau tzdata tidak terpasang.
            pass

    _cached_tz = _FALLBACK_TZ
    return _cached_tz


def reset_lab_tz_cache() -> None:
    """Buang cache zona waktu. Dipakai test yang mengganti config."""
    global _cached_tz
    _cached_tz = None


def now() -> datetime:
    """Waktu sekarang, aware, di zona lab."""
    return datetime.now(get_lab_tz())


def now_naive() -> datetime:
    """
    Waktu sekarang sebagai naive jam dinding lokal.

    Ini yang dipakai untuk semua kolom DATETIME. Menyerahkan aware datetime ke
    driver MySQL hanya membuang tzinfo tanpa peringatan, sehingga niat
    penyimpanan tidak terbaca lagi di kode; jadi buang tzinfo di sini, eksplisit.
    """
    return datetime.now(get_lab_tz()).replace(tzinfo=None)


def to_local(dt: datetime | None) -> datetime | None:
    """
    Normalkan datetime ke zona lab.

    Naive dianggap SUDAH waktu lokal — itu kontrak penyimpanan DB — bukan UTC.
    Aware dikonversi ke zona lab.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=get_lab_tz())
    return dt.astimezone(get_lab_tz())


def naive_local(dt: datetime | None) -> datetime | None:
    """
    Datetime apa pun → naive jam dinding lokal, siap dibandingkan dengan kolom
    DATETIME.

    Dipakai di boundary query: input filter dari user bisa naive (`2026-07-22`
    dari <input type="date">) atau aware ber-offset. Keduanya harus diturunkan
    ke jam lokal dulu; membandingkan aware datetime langsung dengan kolom naive
    membuat driver membuang tzinfo tanpa konversi, jadi offset selain +07:00
    akan menggeser hasil filter secara diam-diam.
    """
    local = to_local(dt)
    return local.replace(tzinfo=None) if local is not None else None


def isoformat(dt=_UNSET) -> str | None:
    """
    ISO8601 ber-offset lokal, misal "2026-07-22T09:30:00+07:00".

    Tanpa argumen → waktu sekarang. Argumen None → None, supaya pemanggil bisa
    meneruskan kolom nullable (`sent_at`, `last_lis_sync_at`) apa adanya tanpa
    guard tambahan di setiap call site.
    """
    if dt is _UNSET:
        return now().isoformat()
    local = to_local(dt)
    return local.isoformat() if local is not None else None


def stamp(fmt: str = "%Y%m%d%H%M%S", dt: datetime | None = None) -> str:
    """
    Jam dinding lokal terformat, untuk field timestamp pesan ASTM/HL7.

    Alat mengharapkan jam lokal tanpa offset, jadi jangan pernah pakai UTC di
    sini.
    """
    return to_local(dt if dt is not None else now()).strftime(fmt)


def logging_converter(secs: float | None = None) -> time.struct_time:
    """
    Converter untuk logging.Formatter — epoch → struct_time di zona lab.

    Default logging.Formatter memakai time.localtime, yang hanya mengikuti TZ
    OS. Dipasang eksplisit supaya timestamp log tetap benar walau service
    dijalankan dengan TZ=UTC (systemd environment bersih) atau kalau config
    `timezone` sengaja berbeda dari zona OS.
    """
    if secs is None:
        secs = time.time()
    return datetime.fromtimestamp(secs, get_lab_tz()).timetuple()


def install_process_timezone() -> None:
    """
    Set TZ proses ke zona lab lalu time.tzset().

    Dipanggil sekali saat service start. Ini yang membuat time.localtime — dan
    karenanya seluruh timestamp logging.Formatter — mengikuti zona lab, bahkan
    kalau service dijalankan supervisor yang mewarisi TZ=UTC (systemd dengan
    environment bersih adalah kasus nyata).
    """
    tz = get_lab_tz()
    name = getattr(tz, "key", None) or DEFAULT_TIMEZONE
    os.environ["TZ"] = name
    try:
        time.tzset()
    except AttributeError:
        # Windows; bukan target deployment, tapi jangan sampai raise.
        pass
