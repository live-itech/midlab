"""
lib/utils.py — Utility Functions untuk MidLab

Menyediakan fungsi-fungsi umum yang dipakai di seluruh service:
- generate_message_id: UUID untuk ResultObject
- get_logger: Logger dengan RotatingFileHandler per service
- format_datetime: Format datetime ke ISO8601
- safe_json_loads: Parse JSON dengan error handling
"""

import json
import logging
import os
import sys
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler

from lib import timeutil
from lib.config import Config


# Direktori log
LOG_DIR = "/var/log/midlab"

# Fallback bila LOG_DIR tidak bisa dipakai (dev, atau service dijalankan oleh
# user yang bukan pemilik /var/log/midlab). Log di sini volatile.
LOG_DIR_FALLBACK = "/tmp/midlab"


def generate_message_id() -> str:
    """Generate UUID string unik untuk message_id di ResultObject."""
    return str(uuid.uuid4())


def _build_handler(
    log_dir: str,
    log_filename: str,
    max_bytes: int,
    backup_count: int,
) -> RotatingFileHandler | None:
    """
    Coba buat RotatingFileHandler di log_dir.

    Returns None (bukan raise) bila direktori tidak bisa dibuat ATAU file log
    tidak bisa dibuka di dalamnya. Dua-duanya harus ditangani: direktori bisa
    saja sudah ada tapi milik user lain — kasus nyata /var/log/midlab yang
    dibuat installer sebagai milik user `midlab` dengan mode 755. Di situ
    os.makedirs(exist_ok=True) sukses dan justru pembukaan file yang gagal.

    Pemanggil yang menyusun pesan peringatan, karena hanya ia yang tahu ke mana
    fallback-nya.
    """
    try:
        os.makedirs(log_dir, exist_ok=True)
        return RotatingFileHandler(
            os.path.join(log_dir, log_filename),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
    except OSError:
        # PermissionError adalah subclass OSError; tangkap keduanya sekaligus.
        return None


def get_logger(
    service_name: str,
    instrument_id: int = None,
) -> logging.Logger:
    """
    Buat logger dengan RotatingFileHandler untuk service tertentu.

    Format log: YYYY-MM-DD HH:MM:SS.mmm [LEVEL] [SERVICE] [INSTRUMENT] pesan
    Output ke: /var/log/midlab/<service_name>.log
    (atau fallback ke /tmp/midlab jika /var/log/midlab tidak writable)

    Args:
        service_name: Nama service (tcp_socket, result_sender, dll)
        instrument_id: ID instrumen (opsional, untuk TCPSocketService)

    Returns:
        logging.Logger yang sudah dikonfigurasi
    """
    config = Config()
    log_level = config.get("logging.level", "INFO")
    max_bytes = config.get("logging.max_bytes", 10485760)
    backup_count = config.get("logging.backup_count", 5)

    # Nama logger unik per service+instrument agar tidak duplikat handler
    if instrument_id is not None:
        logger_name = f"midlab.{service_name}.{instrument_id}"
        log_filename = f"tcp_{instrument_id}.log"
    else:
        logger_name = f"midlab.{service_name}"
        log_filename = f"{service_name}.log"

    logger = logging.getLogger(logger_name)

    # Jangan tambah handler jika sudah ada (hindari duplikasi)
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Tentukan direktori log (fallback ke /tmp untuk development)
    handler = _build_handler(LOG_DIR, log_filename, max_bytes, backup_count)

    if handler is None:
        print(
            f"WARNING: cannot write logs to {LOG_DIR}; "
            f"falling back to {LOG_DIR_FALLBACK}. This is acceptable for "
            f"development but logs are volatile (cleared on reboot).",
            file=sys.stderr,
        )
        handler = _build_handler(
            LOG_DIR_FALLBACK, log_filename, max_bytes, backup_count
        )

    if handler is None:
        # Kedua direktori gagal — log ke stderr saja. Service tidak boleh mati
        # hanya karena logging tidak bisa menulis ke disk.
        print(
            f"WARNING: cannot write logs to {LOG_DIR_FALLBACK} either; "
            f"logging to stderr only.",
            file=sys.stderr,
        )
        handler = logging.StreamHandler(sys.stderr)

    # Format: YYYY-MM-DD HH:MM:SS.mmm [LEVEL] [SERVICE] [INSTRUMENT] pesan
    #
    # Timestamp sengaja disamakan dengan comm_logger. Tanpa ini, log service
    # tidak bisa dikorelasikan dengan lalu lintas byte di *.comm.log — jarak
    # antar-event (mis. seberapa sering alat konek ulang) jadi tidak terbaca.
    instrument_tag = str(instrument_id) if instrument_id is not None else "-"
    formatter = logging.Formatter(
        f"%(asctime)s.%(msecs)03d [%(levelname)s] [{service_name.upper()}] "
        f"[{instrument_tag}] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Jam log = jam lokal lab, bukan TZ proses. Tanpa ini log ikut TZ=UTC bila
    # systemd menjalankan service dengan environment bersih, dan jadi tidak
    # cocok dengan timestamp di DB.
    formatter.converter = timeutil.logging_converter
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def format_datetime(dt: datetime = None) -> str:
    """
    Format datetime ke ISO8601 string ber-offset waktu lokal lab.

    Naive datetime dianggap sudah waktu lokal — itu kontrak penyimpanan kolom
    DATETIME (lihat lib/timeutil.py).

    Args:
        dt: Datetime object. Jika None, gunakan waktu sekarang (lokal lab).

    Returns:
        String ISO8601, misal "2026-07-22T09:30:00+07:00"
    """
    if dt is None:
        return timeutil.isoformat()
    return timeutil.isoformat(dt)


def safe_json_loads(text: str) -> dict | None:
    """
    Parse JSON string dengan error handling.

    Args:
        text: String JSON yang akan di-parse

    Returns:
        dict hasil parsing, atau None jika gagal
    """
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
