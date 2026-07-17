"""
protocols/aruma_ar580/builder.py — Builder ACK^R01 ARUMA AR580

Referensi: "LIS communication protocol instruction" (Genrui) bab 2.3.1, tabel 4,
tabel 11.

Alat mengirim ulang hasil dalam 3 detik bila ACK tidak diterima (bab 2.3.1),
sehingga MSA-2 wajib memantulkan MSH-10 pesan yang diterima. Tabel 4 menetapkan
response LIS→PC hanya terdiri dari MSH + MSA.
"""

from datetime import datetime

from lib.utils import get_logger

from protocols.aruma_ar580.constants import (
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_MSA,
    HL7_VERSION, CHARACTER_SET, COUNTRY_CODE,
    PROC_PRODUCTION,
    EVENT_ACK_R01,
    ACK_AA,
    LIS_SENDING_APP, LIS_SENDING_FACILITY,
    DEFAULT_INSTRUMENT_APP, DEFAULT_INSTRUMENT_MODEL,
)
from protocols.aruma_ar580.parser import strip_mllp, _fields, _field


logger = get_logger("protocol_aruma_ar580")


def _timestamp() -> str:
    """MSH-7 — format YYYYMMDDHHMMSS (dokumen tabel 5)."""
    return datetime.now().strftime("%Y%m%d%H%M%S")


def build_ack(raw_message: bytes, instrument: dict,
              timestamp: str | None = None) -> bytes:
    """
    Bangun ACK^R01 untuk pesan yang diterima dari alat.

    Args:
        raw_message: Pesan ORU^R01 mentah dari alat (dengan/tanpa MLLP).
        instrument: Dict info instrumen.
        timestamp: Override MSH-7, dipakai unit test agar hasil deterministik.

    Returns:
        Bytes ACK terbungkus MLLP, atau b"" bila pesan tak punya MSH — pesan
        yang tidak bisa diidentifikasi tidak boleh di-ACK, karena ACK palsu
        membuat alat mengira data sudah tersimpan.
    """
    if not raw_message:
        return b""

    teks = strip_mllp(raw_message).decode("utf-8", errors="replace")

    msh = next(
        (s for s in teks.split(SEGMENT_TERMINATOR) if s.startswith(SEG_MSH)),
        None,
    )
    if msh is None:
        logger.warning(
            f"[{instrument.get('name', '-')}] pesan tanpa MSH — ACK tidak dikirim"
        )
        return b""

    f = _fields(msh)
    pengirim_app = _field(f, 2) or DEFAULT_INSTRUMENT_APP
    pengirim_facility = _field(f, 3) or DEFAULT_INSTRUMENT_MODEL
    control_id = _field(f, 9)

    stempel = timestamp or _timestamp()

    # MSH-5/6 = MSH-3/4 pesan alat: ACK diarahkan balik ke pengirimnya.
    msh_ack = FIELD_SEPARATOR.join([
        SEG_MSH,
        ENCODING_CHARACTERS,
        LIS_SENDING_APP,
        LIS_SENDING_FACILITY,
        pengirim_app,
        pengirim_facility,
        stempel,
        "",
        EVENT_ACK_R01,
        control_id,
        PROC_PRODUCTION,
        HL7_VERSION,
        "", "", "", "",
        COUNTRY_CODE,
        CHARACTER_SET,
    ])

    # MSA-2 memantulkan MSH-10 alat — kunci agar alat tidak kirim ulang.
    msa = FIELD_SEPARATOR.join([SEG_MSA, ACK_AA, control_id])

    pesan = msh_ack + SEGMENT_TERMINATOR + msa + SEGMENT_TERMINATOR
    return MLLP_START_BYTE + pesan.encode("utf-8") + MLLP_TRAILER
