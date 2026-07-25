"""
services/tap/recorder.py — Perekam sesi tapping ke JSONL.

Byte disimpan HEX, bukan base64: bisa dibaca mata dan di-grep langsung.

Setiap event di-flush segera. Ini bukan optimasi yang kelewat — ini pengaman:
bila MidLab meng-ACK, alat menganggap datanya aman dan tidak mengirim ulang.
Kalau proses mati setelah ACK terkirim tapi sebelum tulisan mendarat, hasil
pasien itu lenyap tanpa jejak. flush() bertahan terhadap crash proses; kehilangan
event terakhir saat listrik mati diterima sebagai batas yang wajar.
"""

import json
import os
from datetime import datetime

from lib.utils import get_logger, LOG_DIR, LOG_DIR_FALLBACK


logger = get_logger("tap_recorder")


def _resolve_tap_dir() -> str:
    """
    Pilih direktori capture yang bisa ditulis.

    Ikuti fallback yang sama dengan lib.utils logging: coba /var/log/midlab/tap,
    jatuh ke /tmp/midlab/tap bila /var/log/midlab tidak writable (dev, atau
    service dijalankan user yang bukan pemilik /var/log/midlab). Tanpa ini,
    seluruh sesi tap mati karena PermissionError saat membuat subdir.
    """
    for base in (LOG_DIR, LOG_DIR_FALLBACK):
        # Direktori 'tap' sudah ada dan writable → langsung pakai.
        d = os.path.join(base, "tap")
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return d
        # Base ada & writable → nanti subdir 'tap' bisa dibuat di sini.
        if os.path.isdir(base) and os.access(base, os.W_OK):
            return d
    return os.path.join(LOG_DIR_FALLBACK, "tap")


TAP_LOG_DIR = _resolve_tap_dir()

_ARAH_VALID = ("rx", "tx", "meta")


class TapRecorder:
    """Tulis event sesi tapping ke file JSONL, satu event per baris."""

    def __init__(self, path: str):
        self._path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")

    def __enter__(self) -> "TapRecorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def write_event(self, direction: str, data: bytes, note: str = "") -> None:
        """Rekam byte yang mengalir. Dipanggil SEBELUM balasan dikirim."""
        if direction not in _ARAH_VALID:
            raise ValueError(
                f"direction '{direction}' tidak valid, harus salah satu dari {_ARAH_VALID}"
            )
        if not data:
            return
        row = {"t": self._now(), "dir": direction, "hex": data.hex()}
        if note:
            row["note"] = note
        self._tulis(row)

    def mark_message(self, index: int) -> None:
        """Tandai bahwa satu pesan lengkap sudah terdeteksi."""
        self._tulis({
            "t": self._now(), "dir": "meta",
            "event": "message_complete", "index": index,
        })

    def mark_query(self, index: int) -> None:
        """
        Tandai pesan ke-index sebagai query (alat meminta order).

        Hanya penanda — MidLab sengaja tidak menjawabnya. Lihat detect.is_query().
        """
        self._tulis({
            "t": self._now(), "dir": "meta",
            "event": "query_detected", "index": index,
        })

    def _tulis(self, row: dict) -> None:
        self._f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self._f.flush()      # pengaman: di disk sebelum balasan dikirim

    @staticmethod
    def _now() -> str:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")

    def close(self) -> None:
        try:
            self._f.close()
        except OSError as e:
            logger.warning(f"Gagal menutup recorder {self._path}: {e}")


def read_events(path: str) -> list[dict]:
    """
    Baca semua event dari file JSONL.

    Baris rusak dilewati, bukan melempar: sesi yang terpotong (proses dibunuh
    saat menulis) tetap harus bisa dibaca sampai baris terakhir yang utuh.
    """
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for baris in f:
            baris = baris.strip()
            if not baris:
                continue
            try:
                out.append(json.loads(baris))
            except json.JSONDecodeError:
                logger.warning(f"Baris JSONL rusak dilewati di {path}")
    return out
