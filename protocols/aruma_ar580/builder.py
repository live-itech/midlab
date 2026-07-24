"""
protocols/aruma_ar580/builder.py — Builder ACK^R01 ARUMA AR580

Referensi: "LIS communication protocol instruction" (Genrui) bab 2.3.1, tabel 4,
tabel 11.

Alat mengirim ulang hasil dalam 3 detik bila ACK tidak diterima (bab 2.3.1),
sehingga MSA-2 wajib memantulkan MSH-10 pesan yang diterima. Tabel 4 menetapkan
response LIS→PC hanya terdiri dari MSH + MSA.

Layout field dikunci ke contoh manual, bukan diringkas: manual menuliskan MSA
dengan trailing field sampai MSA-6 (`MSA|AA|1275||||`) dan MSH sampai MSH-21.
ACK ringkas `MSA|AA|<id>` ditolak alat di lapangan — alat mengirim ulang pesan
dengan control ID yang sama lalu me-reset koneksi.
"""

from lib import timeutil
from lib.utils import get_logger

from protocols.aruma_ar580.constants import (
    MLLP_START_BYTE, MLLP_TRAILER,
    FIELD_SEPARATOR, ENCODING_CHARACTERS, SEGMENT_TERMINATOR,
    SEG_MSH, SEG_MSA,
    HL7_VERSION, CHARACTER_SET, COUNTRY_CODE,
    PROC_PRODUCTION,
    EVENT_ACK_R01,
    ACK_AA, MSA_TEXT_ACCEPTED, STATUS_CODE_OK,
    LIS_SENDING_APP, LIS_SENDING_FACILITY,
)
from protocols.aruma_ar580.parser import strip_mllp, _fields, _field


logger = get_logger("protocol_aruma_ar580")


def _timestamp() -> str:
    """MSH-7 — format YYYYMMDDHHMMSS (dokumen tabel 5), jam lokal lab."""
    return timeutil.stamp("%Y%m%d%H%M%S")


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

    control_id = _field(_fields(msh), 9)
    stempel = timestamp or _timestamp()

    # MSH-5/6 sengaja dikosongkan, bukan diisi identitas alat. AR580 di lapangan
    # mengirim MSH-3..6 kosong; mengisinya dengan nama vendor hasil tebakan
    # ("Genrui"/"KT-6610") membuat alat menolak ACK dan mengirim ulang hasil.
    msh_ack = FIELD_SEPARATOR.join([
        SEG_MSH,
        ENCODING_CHARACTERS,    # MSH-2
        LIS_SENDING_APP,        # MSH-3
        LIS_SENDING_FACILITY,   # MSH-4
        "",                     # MSH-5  receiving application
        "",                     # MSH-6  receiving facility
        stempel,                # MSH-7
        "",                     # MSH-8
        EVENT_ACK_R01,          # MSH-9
        control_id,             # MSH-10
        PROC_PRODUCTION,        # MSH-11
        HL7_VERSION,            # MSH-12
        "", "", "", "",         # MSH-13..16
        COUNTRY_CODE,           # MSH-17
        CHARACTER_SET,          # MSH-18
        "", "", "",             # MSH-19..21 — manual menutup MSH dengan "|||"
    ])

    # MSA-2 memantulkan MSH-10 alat — kunci agar alat tidak kirim ulang.
    msa = FIELD_SEPARATOR.join([
        SEG_MSA,
        ACK_AA,                 # MSA-1
        control_id,             # MSA-2
        MSA_TEXT_ACCEPTED,      # MSA-3
        "",                     # MSA-4
        "",                     # MSA-5
        STATUS_CODE_OK,         # MSA-6 — 0 = message success
        "",                     # MSA-7
    ])

    pesan = msh_ack + SEGMENT_TERMINATOR + msa + SEGMENT_TERMINATOR
    return MLLP_START_BYTE + pesan.encode("utf-8") + MLLP_TRAILER
