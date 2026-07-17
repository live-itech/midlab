"""
protocols/aruma_ar580/module.py — ArumaAR580Module

Driver protocol untuk hematology analyzer ARUMA AR580 (rebrand OEM Genrui
KT-6610), HL7 v2.3.1 di atas MLLP.

Lingkup: UNIDIRECTIONAL. Alat mengirim ORU^R01, MidLab membalas ACK^R01.

Query mode tidak diimplementasi. Dokumen acuan bab 2.2 menyebut ORM^O01 dan
ORR^O02, tetapi tidak memuat definisi field, contoh, maupun grammar segment
untuk keduanya; bab 2.3.2 (QC upload) juga masih kosong ("Not available, to be
added"). Menebak format ke hematology analyzer bukan pilihan yang aman.

Detail desain: docs/superpowers/specs/2026-07-17-aruma-ar580-design.md
"""

from lib.utils import get_logger

from protocols.base import BaseProtocolModule
from protocols.aruma_ar580.builder import build_ack
from protocols.aruma_ar580.constants import (
    PROTOCOL_NAME, PROTOCOL_VERSION,
    SEG_MSA, SEGMENT_TERMINATOR,
    ACK_CODES_POSITIF, ACK_CODES_NEGATIF,
)
from protocols.aruma_ar580.parser import parse_oru, strip_mllp, _fields, _field


logger = get_logger("protocol_aruma_ar580")


_PESAN_DILUAR_LINGKUP = (
    "{metode}() tidak tersedia untuk {protocol}: dokumen LIS Genrui/AR580 "
    "menyebut ORM^O01/ORR^O02 di bab 2.2 tetapi tidak menspesifikasikannya. "
    "Alat harus dijalankan dengan mode 'unidirectional'."
)


class ArumaAR580Module(BaseProtocolModule):
    """Protocol module ARUMA AR580 — terima hasil, balas ACK."""

    @property
    def PROTOCOL_NAME(self) -> str:
        return PROTOCOL_NAME

    @property
    def VERSION(self) -> str:
        return PROTOCOL_VERSION

    # ============================================================
    # Alur utama — hasil masuk
    # ============================================================

    def parse(self, raw_bytes: bytes, instrument: dict) -> dict:
        """Parse ORU^R01 → ResultObject dict."""
        return parse_oru(raw_bytes, instrument)

    def build_ack_response(self, raw_message: bytes, instrument: dict) -> bytes:
        """
        Bangun ACK^R01 untuk pesan yang diterima.

        Dipanggil ResultReceiver lewat hook opsional (services/tcp_socket/
        receiver.py). Mengembalikan b"" bila pesan tak bisa diidentifikasi.
        """
        return build_ack(raw_message, instrument)

    def handle_ack(self, raw_bytes: bytes) -> str:
        """
        Identifikasi MSA-1 dari pesan masuk (dokumen tabel 12).

        Hanya AA yang berarti diterima; AE/AR/CA/CE/CR semuanya penolakan.
        """
        if not raw_bytes:
            return "UNKNOWN"

        teks = strip_mllp(raw_bytes).decode("utf-8", errors="replace")
        msa = next(
            (s for s in teks.split(SEGMENT_TERMINATOR) if s.startswith(SEG_MSA)),
            None,
        )
        if msa is None:
            return "UNKNOWN"

        kode = _field(_fields(msa), 1)
        if kode in ACK_CODES_POSITIF:
            return "ACK"
        if kode in ACK_CODES_NEGATIF:
            return "NAK"
        return "UNKNOWN"

    # ============================================================
    # Di luar lingkup — ORM^O01/ORR^O02 tidak dispesifikasi dokumen
    # ============================================================

    def is_enq(self, raw_bytes: bytes) -> bool:
        """AR580 tidak pernah query LIS dalam lingkup driver ini."""
        return False

    def handle_enq(self, raw_bytes: bytes, instrument: dict) -> dict:
        raise NotImplementedError(
            _PESAN_DILUAR_LINGKUP.format(metode="handle_enq", protocol=PROTOCOL_NAME)
        )

    def format_order(self, order: dict, instrument: dict) -> bytes:
        raise NotImplementedError(
            _PESAN_DILUAR_LINGKUP.format(metode="format_order", protocol=PROTOCOL_NAME)
        )

    def format_query_response(self, order: dict, instrument: dict) -> bytes:
        raise NotImplementedError(
            _PESAN_DILUAR_LINGKUP.format(
                metode="format_query_response", protocol=PROTOCOL_NAME
            )
        )

    def format_query_not_found(self, instrument: dict) -> bytes:
        """Tidak ada query untuk dijawab — tidak mengirim apa pun."""
        return b""
