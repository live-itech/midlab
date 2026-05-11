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
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

from lib.config import Config


# Direktori log
LOG_DIR = "/var/log/midlab"


def generate_message_id() -> str:
    """Generate UUID string unik untuk message_id di ResultObject."""
    return str(uuid.uuid4())


def get_logger(
    service_name: str,
    instrument_id: int = None,
) -> logging.Logger:
    """
    Buat logger dengan RotatingFileHandler untuk service tertentu.

    Format log: [LEVEL] [SERVICE] [INSTRUMENT] pesan
    Output ke: /var/log/midlab/<service_name>.log

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

    # Pastikan direktori log ada
    os.makedirs(LOG_DIR, exist_ok=True)

    log_path = os.path.join(LOG_DIR, log_filename)

    # RotatingFileHandler sesuai konfigurasi
    handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )

    # Format: [LEVEL] [SERVICE] [INSTRUMENT] pesan
    instrument_tag = str(instrument_id) if instrument_id is not None else "-"
    formatter = logging.Formatter(
        f"[%(levelname)s] [{service_name.upper()}] [{instrument_tag}] %(message)s"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    return logger


def format_datetime(dt: datetime = None) -> str:
    """
    Format datetime ke ISO8601 string.

    Args:
        dt: Datetime object. Jika None, gunakan waktu sekarang (UTC).

    Returns:
        String ISO8601, misal "2026-04-16T10:30:00+00:00"
    """
    if dt is None:
        dt = datetime.now(timezone.utc)
    return dt.isoformat()


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
